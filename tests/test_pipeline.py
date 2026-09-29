"""Checks for distance math, the venue CSV repair, and jurisdiction."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import pandas as pd

from pipeline.aggregate import build_venue_outputs
from pipeline.geo import EARTH_RADIUS_M, buffer_zone, haversine_m
from pipeline.loaders import is_lapd_agency, load_venues

ROOT = Path(__file__).resolve().parents[1]


class HaversineTests(unittest.TestCase):
    def test_one_degree_of_latitude(self):
        distance = float(haversine_m(0.0, 0.0, 1.0, 0.0))
        expected = EARTH_RADIUS_M * math.pi / 180.0
        self.assertAlmostEqual(distance, expected, places=4)

    def test_symmetric(self):
        forward = float(haversine_m(34.05, -118.25, 34.07, -118.24))
        backward = float(haversine_m(34.07, -118.24, 34.05, -118.25))
        self.assertAlmostEqual(forward, backward, places=6)


class BufferZoneTests(unittest.TestCase):
    def test_radius_includes_near_point_and_excludes_far_point(self):
        meters_per_degree = EARTH_RADIUS_M * math.pi / 180.0
        near = 700.0 / meters_per_degree
        far = 900.0 / meters_per_degree
        points = pd.DataFrame(
            {
                "name": ["near", "far", "origin"],
                "latitude": [34.0 + near, 34.0 + far, 34.0],
                "longitude": [-118.0, -118.0, -118.0],
            }
        )
        hits = buffer_zone(points, 34.0, -118.0, radius_m=800.0)
        self.assertEqual(set(hits["name"]), {"near", "origin"})
        near_distance = float(hits.loc[hits["name"] == "near", "distance_m"].iloc[0])
        self.assertAlmostEqual(near_distance, 700.0, delta=1.0)

    def test_empty_frame(self):
        empty = pd.DataFrame(columns=["latitude", "longitude"])
        hits = buffer_zone(empty, 34.0, -118.0)
        self.assertTrue(hits.empty)
        self.assertIn("distance_m", hits.columns)


class VenueLoaderTests(unittest.TestCase):
    def test_real_venues_file_repairs_broken_rows(self):
        path = ROOT / "data" / "la28_venues.csv"
        if not path.exists():
            self.skipTest("venues csv not present")
        venues, report = load_venues(path)
        self.assertGreaterEqual(len(venues), 14)
        self.assertTrue(venues["venue_id"].is_unique)
        port = venues.loc[venues["venue_id"] == "V11"].iloc[0]
        self.assertAlmostEqual(port["latitude"], 33.74085, places=4)
        self.assertAlmostEqual(port["longitude"], -118.28176, places=4)
        self.assertIn("repaired", " ".join(report.notes))


class JurisdictionTests(unittest.TestCase):
    def test_flag_follows_nearest_local_agency(self):
        meters_per_degree = EARTH_RADIUS_M * math.pi / 180.0
        shift = lambda meters: meters / meters_per_degree
        venues = pd.DataFrame(
            [
                {
                    "venue_id": "V01",
                    "venue_name": "Test Venue",
                    "address": "1 Main",
                    "city": "Los Angeles",
                    "olympic_zone": "DTLA",
                    "sports": "Test",
                    "latitude": 34.0,
                    "longitude": -118.0,
                    "verification_status": "",
                    "geocode_result": "OK",
                }
            ]
        )
        police = pd.DataFrame(
            [
                {
                    "station_id": "1",
                    "station_name": "LAPD Central",
                    "agency": "Los Angeles Police Department",
                    "station_type": "municipal_police",
                    "latitude": 34.0 + shift(200),
                    "longitude": -118.0,
                },
                {
                    "station_id": "2",
                    "station_name": "East LA Sheriff",
                    "agency": "Los Angeles County Sheriff's Department",
                    "station_type": "sheriff",
                    "latitude": 34.0 + shift(900),
                    "longitude": -118.0,
                },
            ]
        )
        empty = pd.DataFrame(columns=["latitude", "longitude"])
        crime = pd.DataFrame(
            columns=["incident_id", "category", "occurred_at", "latitude", "longitude"]
        )
        rail = pd.DataFrame(
            columns=["station_id", "station_name", "lines", "latitude", "longitude"]
        )
        bus = empty.copy()
        bus["stop_id"] = pd.Series(dtype="object")
        bus["bus_line"] = pd.Series(dtype="object")
        fire = pd.DataFrame(columns=["station_id", "station_name", "latitude", "longitude"])

        summary, _points = build_venue_outputs(
            venues, crime, rail, bus, fire, police, radius_m=800.0
        )
        venue = summary["venues"][0]
        self.assertTrue(venue["lapd_jurisdiction"])
        self.assertEqual(venue["nearest_police_station"]["station_name"], "LAPD Central")
        self.assertIn("no_crime_within_radius", venue["data_quality_flags"])

        police.loc[police["station_id"] == "2", "latitude"] = 34.0 + shift(50)
        summary, _points = build_venue_outputs(
            venues, crime, rail, bus, fire, police, radius_m=800.0
        )
        venue = summary["venues"][0]
        self.assertFalse(venue["lapd_jurisdiction"])
        self.assertIn("city_of_la_but_nearest_station_is_not_lapd", venue["data_quality_flags"])

    def test_bus_lines_are_unique_and_split(self):
        venues = pd.DataFrame(
            [
                {
                    "venue_id": "V01",
                    "venue_name": "Test Venue",
                    "address": "",
                    "city": "Los Angeles",
                    "olympic_zone": "",
                    "sports": "",
                    "latitude": 34.0,
                    "longitude": -118.0,
                    "geocode_result": "",
                }
            ]
        )
        bus = pd.DataFrame(
            [
                {"stop_id": "1", "bus_line": "2;4", "latitude": 34.0, "longitude": -118.0},
                {"stop_id": "2", "bus_line": "4;720", "latitude": 34.0001, "longitude": -118.0},
            ]
        )
        empty_crime = pd.DataFrame(
            columns=["incident_id", "category", "occurred_at", "latitude", "longitude"]
        )
        empty_rail = pd.DataFrame(
            columns=["station_id", "station_name", "lines", "latitude", "longitude"]
        )
        empty_stations = pd.DataFrame(
            columns=["station_id", "station_name", "agency", "latitude", "longitude"]
        )
        summary, _points = build_venue_outputs(
            venues,
            empty_crime,
            empty_rail,
            bus,
            empty_stations.rename(columns={"agency": "department"}).drop(columns=["department"]),
            empty_stations,
            radius_m=800.0,
        )
        self.assertEqual(summary["venues"][0]["bus_stops_nearby"]["lines"], ["2", "4", "720"])
        self.assertEqual(summary["venues"][0]["bus_stops_nearby"]["count"], 2)

    def test_is_lapd_agency(self):
        self.assertTrue(is_lapd_agency("Los Angeles Police Department"))
        self.assertFalse(is_lapd_agency("Los Angeles County Sheriff's Department"))


if __name__ == "__main__":
    unittest.main()
