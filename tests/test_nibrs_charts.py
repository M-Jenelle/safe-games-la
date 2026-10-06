"""NIBRS time, distance, and permit-day windows."""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from pipeline.nibrs_charts import charts_for_frame, permit_rows_for_venue, window_days


class NibrsChartTests(unittest.TestCase):
    def test_window_keeps_permit_months_inside_the_nibrs_dates(self):
        days = window_days({"2024-03-08", "2026-09-26", "2025-06-02"}, date(2024, 3, 7), date(2026, 9, 19))
        self.assertIn("2024-03-07", days)
        self.assertNotIn("2024-03-06", days)
        self.assertIn("2025-06-02", days)
        self.assertIn("2026-06-02", days)
        self.assertNotIn("2026-09-01", days)
        self.assertNotIn("2026-09-26", days)
        self.assertEqual(window_days({"2024-03-01"}, date(2024, 3, 7), date(2026, 9, 19)), [])
        self.assertTrue(all("2024-03-07" <= day <= "2026-09-19" for day in days))

    def test_clock_and_distance_follow_the_offense(self):
        frame = pd.DataFrame([
            {"occurred_at": pd.Timestamp("2024-03-08 03:30"), "category": "THEFT", "latitude": 34.0446, "longitude": -118.2669},
            {"occurred_at": pd.Timestamp("2024-06-01 12:00"), "category": "BATTERY", "latitude": 34.0500, "longitude": -118.2669},
            {"occurred_at": pd.Timestamp("2024-03-01 09:00"), "category": "THEFT", "latitude": 34.0446, "longitude": -118.2669},
            {"occurred_at": pd.Timestamp("2026-10-01 20:00"), "category": "THEFT", "latitude": 34.0446, "longitude": -118.2669},
        ])
        time_block, distance_block, weekday, totals, groups = charts_for_frame(
            frame,
            34.0445,
            -118.2669,
            start=date(2024, 3, 7),
            end=date(2026, 9, 19),
        )
        self.assertEqual(time_block["total"], 2)
        self.assertEqual(weekday["total"], 2)
        self.assertEqual(next(day["count"] for day in weekday["days"] if day["id"] == "fri"), 1)
        self.assertEqual(weekday["weekend_count"], 1)
        self.assertEqual(time_block["noon_count"], 1)
        by_period = {item["id"]: item["count"] for item in time_block["periods"]}
        self.assertEqual(by_period["night"], 1)
        self.assertEqual(by_period["afternoon"], 1)
        self.assertIn("NIBRS offenses", time_block["disclaimer"])
        self.assertIn("not a crime at the door", distance_block["disclaimer"])
        self.assertEqual(distance_block["total"], 2)
        self.assertEqual(totals["2024-03-08"], 1)
        self.assertEqual(groups["theft"]["2024-03-08"], 1)
        self.assertNotIn("2024-03-01", totals)

    def test_permit_rows_count_offenses_on_covered_days(self):
        rows = permit_rows_for_venue(
            "V04",
            "Peacock Theater",
            {"2024-03-08": 2},
            {"theft": {"2024-03-08": 2}},
            {"2024-03-08": {"venue_name": "Peacock Theater", "permit_count": 1}},
            date(2024, 3, 7),
            date(2024, 3, 31),
        )
        covered = next(row for row in rows if row["date"] == "2024-03-08")
        other = next(row for row in rows if row["date"] == "2024-03-09")
        self.assertEqual(covered["incident_count"], 2)
        self.assertEqual(covered["is_permit_event_day"], 1)
        self.assertEqual(covered["theft"], 2)
        self.assertEqual(other["incident_count"], 0)
        self.assertEqual(other["is_permit_event_day"], 0)
        self.assertTrue(all(row["date"] >= "2024-03-07" for row in rows))
