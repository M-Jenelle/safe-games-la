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
        self.assertIn("Los Angeles Venue Safety Intelligence", home.text)
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
        self.assertGreater(peacock["nibrs_count"], 0)
        self.assertEqual(body["nibrs"]["count"], peacock["nibrs_count"])
        self.assertEqual(body["crime_count_nearby"], peacock["crime_count_nearby"])
        baseline = body["city_baseline"]
        reports = baseline["reports"]
        nibrs = baseline["nibrs"]
        self.assertEqual(reports["crime_per_km2"], 824.6)
        self.assertEqual(reports["incident_count"], 1_002_654)
        self.assertEqual(reports["relation"], "above")
        self.assertEqual(reports["value"], "8.5×")
        self.assertIn("2020–2024", reports["caption"])
        self.assertAlmostEqual(
            reports["ratio"],
            round(body["crime_per_km2"] / reports["crime_per_km2"], 3),
            places=3,
        )
        self.assertIn("Above the city", reports["summary"])
        self.assertIn("not added into that total", reports["hint"])
        self.assertEqual(nibrs["crime_per_km2"], 395.7)
        self.assertEqual(nibrs["offense_count"], 481_109)
        self.assertEqual(nibrs["relation"], "above")
        self.assertIn("Mar 2024–present", nibrs["caption"])
        self.assertIn("not the same count", nibrs["hint"])
        present = body["present"]
        present_count = sum(
            sum(groups.values()) for groups in body["merged_by_month"].values()
        )
        self.assertEqual(present["count"], present_count)
        self.assertEqual(present["peak_count"], max(
            sum(groups.values()) for groups in body["merged_by_month"].values()
        ))
        span = baseline["present"]
        self.assertEqual(span["period"], "2020–present")
        self.assertIn("2020–present", span["caption"])
        self.assertIn("Venue circles are not added", span["hint"])
        self.assertNotIn("March 6, 2024", span["hint"])
        self.assertAlmostEqual(
            span["ratio"],
            round(present["crime_per_km2"] / span["crime_per_km2"], 3),
            places=3,
        )
        zoo = self.client.get("/api/venues/V08")
        self.assertEqual(zoo.status_code, 200)
        self.assertEqual(zoo.json()["city_baseline"]["reports"]["relation"], "below")
        self.assertEqual(zoo.json()["city_baseline"]["nibrs"]["relation"], "below")
        self.assertEqual(zoo.json()["city_baseline"]["present"]["relation"], "below")
        self.assertIn("2020-01", body["merged_by_month"])
        self.assertIn("2024-03", body["nibrs"]["by_month"])
        self.assertNotIn("2024-02", body["nibrs"]["by_month"])
        clock = body["crime_time"]
        self.assertEqual(clock["total"], body["crime_count_nearby"])
        self.assertEqual(clock["noon_count"], 311)
        self.assertEqual(clock["unknown_count"], 0)
        self.assertIn("800m buffer", clock["disclaimer"])
        self.assertIn("unknown hour", clock["disclaimer"])
        self.assertEqual(
            {period["id"]: period["count"] for period in clock["periods"]},
            {"night": 2041, "morning": 2551, "afternoon": 4432, "evening": 5146},
        )
        evening = next(period for period in clock["periods"] if period["id"] == "evening")
        self.assertEqual(sum(group["count"] for group in evening["groups"]), evening["count"])
        self.assertEqual(evening["groups"][0]["label"], "Vehicle")
        self.assertEqual(sum(item["total"] for item in clock["by_month"].values()), clock["total"])
        self.assertEqual(clock["by_month"]["2023-03"]["total"], 342)
        distance = body["crime_distance"]
        self.assertEqual(distance["total"], body["crime_count_nearby"])
        self.assertEqual(sum(item["total"] for item in distance["by_month"].values()), distance["total"])
        self.assertIn("not a crime at the door", distance["disclaimer"])
        self.assertEqual(
            {band["id"]: band["count"] for band in distance["bands"]},
            {"near": 674, "mid": 2302, "far": 11194},
        )
        far = next(band for band in distance["bands"] if band["id"] == "far")
        self.assertEqual(sum(group["count"] for group in far["groups"]), far["count"])
        self.assertNotIn("ticketmaster", body)
        nibrs_time = body["nibrs_time"]
        self.assertEqual(nibrs_time["total"], body["nibrs"]["count"])
        self.assertIn("NIBRS offenses", nibrs_time["disclaimer"])
        self.assertEqual(sum(period["count"] for period in nibrs_time["periods"]), nibrs_time["total"])
        self.assertEqual(sum(item["total"] for item in nibrs_time["by_month"].values()), nibrs_time["total"])
        self.assertNotIn("2023-03", nibrs_time["by_month"])
        nibrs_distance = body["nibrs_distance"]
        self.assertEqual(nibrs_distance["total"], body["nibrs"]["count"])
        self.assertIn("not a crime at the door", nibrs_distance["disclaimer"])
        nibrs_permit = self.client.get("/api/venues/V04/permit-comparison?source=nibrs")
        self.assertEqual(nibrs_permit.status_code, 200)
        nibrs_body = nibrs_permit.json()
        self.assertTrue(nibrs_body["available"])
        self.assertEqual(nibrs_body["source"], "nibrs")
        self.assertIn("NIBRS offenses", nibrs_body["source_note"])
        self.assertIn("2025", nibrs_body["years"])
        self.assertEqual(nibrs_body["upcoming"], [])
        self.assertEqual(body["density_rank"], {"rank": 1, "of": len(venues), "tied": False})
        self.assertEqual(body["crime_per_km2"], max(venue["crime_per_km2"] for venue in venues))
        self.assertTrue(body["overlapping_venues"])
        self.assertTrue(all(item["distance_m"] < 1600 for item in body["overlapping_venues"]))
        self.assertEqual(body["nearest_emergency_room"]["emergency_room"], "Yes")
        clock_days = body["crime_weekday"]
        self.assertEqual(sum(day["count"] for day in clock_days["days"]), clock_days["total"])
        self.assertEqual(clock_days["weekday_count"] + clock_days["weekend_count"], clock_days["total"])
        self.assertEqual(clock_days["by_month"]["2023-03"]["total"], 342)
        self.assertEqual(body["nibrs_weekday"]["total"], body["nibrs"]["count"])
        self.assertLess(
            body["merged_weekday"]["total"],
            clock_days["total"] + body["nibrs_weekday"]["total"],
        )
        self.assertEqual(body["merged_weekday"]["disclaimer"], "")
        merged_time = body["merged_time"]
        self.assertEqual(merged_time["by_month"]["2023-03"]["total"], clock["by_month"]["2023-03"]["total"])
        self.assertEqual(merged_time["by_month"]["2024-06"]["total"], nibrs_time["by_month"]["2024-06"]["total"])
        self.assertNotIn("NIBRS", merged_time["disclaimer"])
        self.assertIn("unknown hour", merged_time["disclaimer"])
        self.assertGreater(max(merged_time["by_month"]), "2024-12")
        self.assertEqual(sum(item["total"] for item in merged_time["by_month"].values()), merged_time["total"])
        self.assertEqual(
            sum(period["count"] for period in merged_time["periods"]),
            sum(period["count"] for period in merged_time["by_month"]["2023-03"]["periods"])
            + sum(
                period["count"]
                for month, item in merged_time["by_month"].items()
                if month != "2023-03"
                for period in item["periods"]
            ),
        )
        merged_distance = body["merged_distance"]
        self.assertEqual(merged_distance["by_month"]["2023-03"]["total"], distance["by_month"]["2023-03"]["total"])
        self.assertIn("not a crime at the door", merged_distance["disclaimer"])
        self.assertGreater(max(merged_distance["by_month"]), "2024-12")
        dodger = self.client.get("/api/venues/V01").json()
        self.assertEqual(dodger["nearest_hospital"]["emergency_room"], "No")
        self.assertEqual(dodger["nearest_emergency_room"]["emergency_room"], "Yes")
        self.assertNotEqual(
            dodger["nearest_emergency_room"]["station_name"],
            dodger["nearest_hospital"]["station_name"],
        )
        self.assertGreater(dodger["density_rank"]["rank"], 1)

    def test_ticketmaster_listings(self):
        dodger = self.client.get("/api/venues/V01").json()["ticketmaster"]
        self.assertEqual(dodger["event_count"], 83)
        self.assertEqual(dodger["with_start_time"], 1)
        self.assertEqual(dodger["distance_m"], 907)
        self.assertIn("outside the 800 m circle", dodger["note"])
        self.assertIn("May 2027 and August 2027", dodger["busiest"])
        self.assertIn("no report count", dodger["disclaimer"])
        self.assertEqual(dodger["events"][0]["date"], "2026-10-09")
        self.assertIn("NLDS", dodger["events"][0]["name"])
        self.assertTrue(dodger["events"][0]["on_sale"])
        self.assertEqual(sum(month["count"] for month in dodger["months"]), 83)
        self.assertTrue(any(month["count"] == 0 for month in dodger["months"]))

        center = self.client.get("/api/venues/V03").json()["ticketmaster"]
        self.assertEqual(center["event_count"], 13)
        self.assertEqual(center["with_start_time"], 13)
        self.assertIn("Peacock Theater is", center["note"])
        self.assertIn("inside the 800 m circle", center["note"])
        self.assertEqual(center["busiest"], "October 2026 has the most listings, 7.")
        self.assertTrue(all(event["start_time"] for event in center["events"]))

    def test_unknown_venue(self):
        response = self.client.get("/api/venues/V99")
        self.assertEqual(response.status_code, 404)
        missing = self.client.get("/api/venues/V99/permit-comparison")
        self.assertEqual(missing.status_code, 404)

    def test_permit_comparison(self):
        peacock = self.client.get("/api/venues/V04/permit-comparison")
        self.assertEqual(peacock.status_code, 200)
        body = peacock.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["summary"]["event_day_count"], 302)
        self.assertEqual(body["summary"]["event_day_mean"], 8.98)
        self.assertEqual(body["summary"]["other_day_mean"], 7.51)
        self.assertTrue(body["summary"]["percent_shown"])
        self.assertGreaterEqual(len(body["series"]), 1)
        self.assertTrue(body["groups"])
        self.assertEqual(
            [row["label"] for row in body["permit_load"]],
            ["One permit", "Several permits", "Other days"],
        )
        self.assertGreater(body["permit_load"][0]["day_count"], 0)
        self.assertGreater(len(body["upcoming"]), 0)
        self.assertIn("start", body["upcoming"][0])
        self.assertIn("weaker signal", body["disclaimer"])

        dodger = self.client.get("/api/venues/V01/permit-comparison").json()
        self.assertEqual(dodger["summary"]["event_day_count"], 41)
        self.assertIn("MLB", dodger["note"])
        self.assertIn("theft", [group["group"] for group in dodger["groups"]])
        self.assertNotIn("assault", [group["group"] for group in dodger["groups"]])

        valley = self.client.get("/api/venues/V12/permit-comparison")
        self.assertEqual(valley.status_code, 200)
        self.assertFalse(valley.json()["available"])

        home = self.client.get("/api/venues/V01/home-games")
        self.assertEqual(home.status_code, 200)
        games = home.json()
        self.assertTrue(games["available"])
        self.assertEqual(games["summary"]["event_day_count"], 350)
        self.assertEqual(games["summary"]["other_day_count"], 875)
        self.assertEqual(games["summary"]["event_day_mean"], 1.14)
        self.assertEqual(games["summary"]["other_day_mean"], 0.31)
        self.assertEqual(games["summary"]["absolute_difference"], 0.83)
        self.assertTrue(games["summary"]["percent_shown"])
        self.assertEqual([group["group"] for group in games["groups"]], [
            "assault", "theft", "other", "vehicle", "vandalism",
        ])
        self.assertFalse(games["groups"][0]["percent_shown"])
        self.assertNotIn("upcoming", games)
        self.assertIn("not a forecast", games["disclaimer"])
        self.assertFalse(self.client.get("/api/venues/V04/home-games").json()["available"])

        thin = self.client.get("/api/venues/V11/permit-comparison?year=2020&month=1")
        self.assertEqual(thin.status_code, 200)
        self.assertFalse(thin.json()["summary"]["percent_shown"])

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
        self.assertIn("nibrs", option_ids)
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
        nibrs = self.client.get("/api/map/crime?view=nibrs")
        self.assertEqual(nibrs.status_code, 200)
        nibrs_body = nibrs.json()
        self.assertGreater(nibrs_body["incident_count"], 100_000)
        self.assertFalse(nibrs_body["hot"])

    def test_crime_heat_month_range(self):
        full = self.client.get("/api/map/crime?view=all").json()
        self.assertGreaterEqual(len(full["months"]), 12)
        self.assertEqual(full["start"], full["months"][0])
        self.assertEqual(full["end"], full["months"][-1])
        self.assertGreater(full["end"], "2024-12")
        later = self.client.get("/api/map/crime?view=all&start=2025-01&end=2025-06")
        self.assertEqual(later.status_code, 200)
        later_body = later.json()
        self.assertEqual(later_body["start"], "2025-01")
        self.assertEqual(later_body["end"], "2025-06")
        self.assertGreater(later_body["incident_count"], 0)
        self.assertLess(later_body["incident_count"], full["incident_count"])
        one = self.client.get("/api/map/crime?view=all&start=2023-06&end=2023-06")
        self.assertEqual(one.status_code, 200)
        one_body = one.json()
        self.assertEqual(one_body["start"], "2023-06")
        self.assertEqual(one_body["end"], "2023-06")
        self.assertGreater(one_body["incident_count"], 0)
        self.assertLess(one_body["incident_count"], full["incident_count"])
        span = self.client.get("/api/map/crime?view=all&start=2024-06&end=2024-12").json()
        self.assertLess(span["incident_count"], full["incident_count"])
        self.assertGreater(span["incident_count"], one_body["incident_count"])
        self.assertEqual(self.client.get("/api/map/crime?view=all&start=2024-13&end=2024-12").status_code, 400)
        nibrs = self.client.get("/api/map/crime?view=nibrs&start=2024-06&end=2025-06")
        self.assertEqual(nibrs.status_code, 200)
        nibrs_body = nibrs.json()
        self.assertEqual(nibrs_body["start"], "2024-06")
        self.assertEqual(nibrs_body["end"], "2025-06")
        self.assertGreater(nibrs_body["incident_count"], 0)
        self.assertLess(nibrs_body["incident_count"], self.client.get("/api/map/crime?view=nibrs").json()["incident_count"])

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
        self.assertIn("2.01", body["copy"]["density_hint"])
        self.assertIn("at least 8 permit days", body["copy"]["permit_hints"]["Difference"])
        self.assertIn("0.05", body["copy"]["permit_hints"]["Offense Groups"])

    def test_display_fields_come_from_the_briefing(self):
        peacock = self.client.get("/api/venues/V04").json()
        self.assertIn("2.01", peacock["display"]["density_hint"])
        self.assertIn("other 13", peacock["display"]["density_hint"])
        self.assertTrue(peacock["display"]["overlap_note"])
        self.assertFalse(peacock["home_games_available"])
        self.assertIn("Saturday", peacock["crime_weekday"]["summary"])
        dodger = self.client.get("/api/venues/V01").json()
        self.assertTrue(dodger["home_games_available"])
        self.assertNotIn("V01", dodger["display"]["overlap_note"])
        permit = self.client.get("/api/venues/V01/permit-comparison").json()
        self.assertIn("permit days, not the MLB", permit["note"])
        self.assertIn("0.05", permit["group_gap_note"])


if __name__ == "__main__":
    unittest.main()
