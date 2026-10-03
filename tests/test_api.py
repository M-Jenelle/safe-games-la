"""API checks against the processed venue briefing."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.main import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_and_home(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Safe Games LA", home.text)
        self.assertIn("Los Angeles venue safety intelligence", home.text)
        self.assertIn('id="venue-page"', home.text)

    def test_venue_list_and_detail(self):
        listing = self.client.get("/api/venues")
        self.assertEqual(listing.status_code, 200)
        venues = listing.json()["venues"]
        self.assertGreaterEqual(len(venues), 14)
        peacock = next(venue for venue in venues if venue["venue_id"] == "V04")
        self.assertEqual(peacock["venue_name"], "Peacock Theater")
        self.assertGreater(peacock["crime_count_nearby"], 0)

        detail = self.client.get("/api/venues/V04")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertIn("crime_by_category", body)
        self.assertIn("crime_by_month", body)
        self.assertIn("nearest_police_station", body)
        self.assertIn("nearest_hospital", body)
        self.assertEqual(
            sum(body["crime_by_category"].values()),
            body["crime_count_nearby"],
        )
        by_month = body["crime_categories_by_month"]
        self.assertEqual(set(by_month), set(body["crime_by_month"]))
        for month, count in body["crime_by_month"].items():
            self.assertEqual(sum(by_month[month].values()), count)

    def test_unknown_venue(self):
        response = self.client.get("/api/venues/V99")
        self.assertEqual(response.status_code, 404)

    def test_crime_points_limit(self):
        response = self.client.get("/api/venues/V08/crime-points?limit=2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["venue_id"], "V08")
        self.assertEqual(body["returned"], 2)
        self.assertGreater(body["point_count"], 2)
        self.assertEqual(set(body["points"][0]), {
            "venue_id",
            "incident_id",
            "latitude",
            "longitude",
            "category",
            "date",
        })

    def test_map_frame_contains_every_venue(self):
        response = self.client.get("/api/map")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["placeholder"])
        bounds = body["bounds"]
        self.assertGreaterEqual(len(body["markers"]), 14)
        for marker in body["markers"]:
            self.assertGreaterEqual(marker["latitude"], bounds["south"])
            self.assertLessEqual(marker["latitude"], bounds["north"])
            self.assertGreaterEqual(marker["longitude"], bounds["west"])
            self.assertLessEqual(marker["longitude"], bounds["east"])

    def test_crime_heat_covers_the_city(self):
        response = self.client.get("/api/map/crime")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["view"], "all")
        self.assertGreater(body["incident_count"], 500_000)
        self.assertGreater(body["location_count"], 20_000)
        option_ids = {option["id"] for option in body["options"]}
        self.assertIn("high", option_ids)
        self.assertIn("venues", option_ids)
        self.assertIn("type:theft", option_ids)
        latitudes = [point["latitude"] for point in body["points"]]
        longitudes = [point["longitude"] for point in body["points"]]
        self.assertGreater(max(latitudes) - min(latitudes), 0.5)
        self.assertGreater(max(longitudes) - min(longitudes), 0.5)

    def test_crime_heat_views(self):
        city = self.client.get("/api/map/crime?view=all").json()
        high = self.client.get("/api/map/crime?view=high")
        venues = self.client.get("/api/map/crime?view=venues")
        theft = self.client.get("/api/map/crime?view=type:theft")
        missing = self.client.get("/api/map/crime?view=type:not-a-type")
        self.assertEqual(high.status_code, 200)
        self.assertEqual(venues.status_code, 200)
        self.assertEqual(theft.status_code, 200)
        self.assertEqual(missing.status_code, 400)
        high_body = high.json()
        self.assertTrue(high_body["hot"])
        self.assertLess(high_body["location_count"], city["location_count"])
        self.assertGreater(min(point["weight"] for point in high_body["points"]), 1)
        self.assertGreater(venues.json()["incident_count"], 1000)
        self.assertLess(venues.json()["incident_count"], city["incident_count"])
        self.assertGreater(theft.json()["incident_count"], 1000)

    def test_map_layers(self):
        response = self.client.get("/api/map/layers")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for name in ("fire", "hospitals", "police", "rail", "bus"):
            self.assertIn(name, body)
            self.assertGreater(body[name]["count"], 0)
            point = body[name]["points"][0]
            self.assertIn("latitude", point)
            self.assertIn("longitude", point)
            self.assertTrue(point["name"])
            self.assertIn("all", point["views"])
            option_ids = {option["id"] for option in body[name]["options"]}
            self.assertIn("all", option_ids)
        for name in ("fire", "hospitals", "rail", "bus"):
            self.assertIn("venues", {option["id"] for option in body[name]["options"]})
        self.assertTrue(any(point.get("location") for point in body["fire"]["points"]))
        self.assertTrue(any(point.get("location") for point in body["hospitals"]["points"]))
        self.assertIn("er", {option["id"] for option in body["hospitals"]["options"]})
        self.assertIn("lapd", {option["id"] for option in body["police"]["options"]})
        self.assertIn("line:A", {option["id"] for option in body["rail"]["options"]})
        self.assertIn("rapid", {option["id"] for option in body["bus"]["options"]})

    def test_meta_highlights(self):
        response = self.client.get("/api/meta")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(body["venue_count"], 14)
        self.assertGreaterEqual(
            body["densest_venue"]["crime_per_km2"],
            body["quietest_venue"]["crime_per_km2"],
        )


if __name__ == "__main__":
    unittest.main()
