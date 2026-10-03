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
        self.assertIn("Placeholder", home.text)

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
