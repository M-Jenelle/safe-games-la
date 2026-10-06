"""Time-of-day buckets for venue crime pies."""

from __future__ import annotations

import unittest

from pipeline.crime_time import classify_time, venue_time_block


class CrimeTimeTests(unittest.TestCase):
    def test_clock_buckets(self):
        self.assertEqual(classify_time(30), ("night", False))
        self.assertEqual(classify_time(845), ("morning", False))
        self.assertEqual(classify_time(1200), ("afternoon", True))
        self.assertEqual(classify_time(1259), ("afternoon", False))
        self.assertEqual(classify_time(1845), ("evening", False))
        self.assertEqual(classify_time(2359), ("evening", False))
        self.assertEqual(classify_time(0), ("night", False))
        self.assertEqual(classify_time(2400), (None, False))
        self.assertEqual(classify_time(1260), (None, False))
        self.assertEqual(classify_time(None), (None, False))

    def test_venue_block_groups_and_noon(self):
        points = [
            {"incident_id": "1", "category": "THEFT"},
            {"incident_id": "2", "category": "VEHICLE - STOLEN"},
            {"incident_id": "3", "category": "BATTERY"},
            {"incident_id": "4", "category": "BURGLARY"},
            {"incident_id": "5", "category": "THEFT"},
        ]
        times = {"1": 1200, "2": 1300, "3": 200, "4": 900, "5": 9999}
        block = venue_time_block(points, times)
        self.assertEqual(block["total"], 5)
        self.assertEqual(block["noon_count"], 1)
        self.assertEqual(block["unknown_count"], 1)
        self.assertIn("1 report at 12:00", block["disclaimer"])
        self.assertIn("1 report has no usable hour", block["disclaimer"])
        by_id = {period["id"]: period for period in block["periods"]}
        self.assertEqual(by_id["afternoon"]["count"], 2)
        self.assertEqual(by_id["afternoon"]["groups"][0]["label"], "Vehicle")
        self.assertEqual(by_id["night"]["groups"][0]["id"], "assault")
        self.assertEqual(by_id["morning"]["count"], 1)
        self.assertEqual(sum(period["count"] for period in block["periods"]) + block["unknown_count"], 5)
