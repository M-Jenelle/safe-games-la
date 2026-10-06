"""Distance bands for the venue pie."""

from __future__ import annotations

import unittest

from pipeline.crime_distance import band_for_meters, venue_distance_block


class CrimeDistanceTests(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual(band_for_meters(0), "near")
        self.assertEqual(band_for_meters(200), "near")
        self.assertEqual(band_for_meters(200.1), "mid")
        self.assertEqual(band_for_meters(400), "mid")
        self.assertEqual(band_for_meters(400.1), "far")
        self.assertEqual(band_for_meters(800), "far")

    def test_groups_follow_the_band(self):
        points = [
            {"latitude": 34.0450, "longitude": -118.2669, "category": "THEFT"},
            {"latitude": 34.0470, "longitude": -118.2669, "category": "BATTERY"},
            {"latitude": 34.0495, "longitude": -118.2669, "category": "VEHICLE - STOLEN"},
        ]
        block = venue_distance_block(points, 34.0445, -118.2669)
        self.assertEqual(block["total"], 3)
        self.assertIn("not a crime at the door", block["disclaimer"])
        by_id = {band["id"]: band for band in block["bands"]}
        self.assertEqual(by_id["near"]["count"], 1)
        self.assertEqual(by_id["near"]["groups"][0]["id"], "theft")
        self.assertEqual(sum(band["count"] for band in block["bands"]), 3)
