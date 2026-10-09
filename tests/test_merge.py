"""Cutoff and group rules for the merged crime file."""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from pipeline.crime_groups import crime_group, nibrs_group
from pipeline.merge_crime import build_merged, source_for


class MergeRulesTests(unittest.TestCase):
    def test_source_for_splits_on_march_7_2024(self):
        self.assertEqual(source_for(date(2024, 3, 6), "legacy"), "legacy")
        self.assertIsNone(source_for(date(2024, 3, 7), "legacy"))
        self.assertIsNone(source_for(date(2024, 3, 6), "nibrs"))
        self.assertEqual(source_for(date(2024, 3, 7), "nibrs"), "nibrs")
        self.assertIsNone(source_for(None, "legacy"))
        self.assertIsNone(source_for(None, "nibrs"))

    def test_groups_follow_the_first_matching_rule(self):
        self.assertEqual(crime_group("THEFT FROM MOTOR VEHICLE"), "vehicle")
        self.assertEqual(crime_group("BIKE - STOLEN"), "vehicle")
        self.assertEqual(crime_group("RAPE, FORCIBLE"), "sexual")
        self.assertEqual(crime_group("BATTERY - SIMPLE ASSAULT"), "assault")
        self.assertEqual(crime_group("BURGLARY"), "burglary")
        self.assertEqual(crime_group("TRESPASSING"), "other")

    def test_nibrs_groups_follow_the_offense_code(self):
        self.assertEqual(nibrs_group("Grand Theft Auto - GTA - 240"), "vehicle")
        self.assertEqual(crime_group("Grand Theft Auto - GTA - 240"), "theft")
        self.assertEqual(nibrs_group("Theft From Motor Vehicle - 23F"), "theft")
        self.assertEqual(nibrs_group("ADW - Bodily Force - Aggravated - 13A"), "assault")
        self.assertEqual(crime_group("ADW - Bodily Force - Aggravated - 13A"), "other")
        self.assertEqual(nibrs_group("Criminal Threats - 13C"), "other")
        self.assertEqual(nibrs_group("Identity Theft - 26F"), "other")
        self.assertEqual(nibrs_group("All Other Larceny - 23H"), "theft")

    def test_build_keeps_legacy_before_the_cutoff_and_nibrs_after(self):
        venues = [{"venue_id": "V01", "latitude": 34.0, "longitude": -118.0}]
        legacy = {
            "V01": [
                {"date": "2024-02-29", "category": "BURGLARY"},
                {"date": "2024-03-07", "category": "THEFT"},
                {"date": None, "category": "ROBBERY"},
            ]
        }
        nibrs = pd.DataFrame(
            [
                {
                    "occurred_at": pd.Timestamp("2024-03-06"),
                    "category": "ROBBERY",
                    "latitude": 34.0,
                    "longitude": -118.0,
                },
                {
                    "occurred_at": pd.Timestamp("2024-03-07"),
                    "category": "THEFT FROM MOTOR VEHICLE",
                    "latitude": 34.0,
                    "longitude": -118.0,
                },
                {
                    "occurred_at": pd.Timestamp("2024-03-07"),
                    "category": "THEFT FROM MOTOR VEHICLE",
                    "latitude": 34.0,
                    "longitude": -118.0,
                },
            ]
        )
        document = build_merged(venues, legacy, nibrs, radius_m=800)
        block = document["venues"]["V01"]
        self.assertEqual(block["nibrs_count"], 2)
        self.assertEqual(block["merged_by_month"]["2024-02"], {"burglary": 1})
        self.assertEqual(block["merged_by_month"]["2024-03"], {"vehicle": 2})
        self.assertNotIn("theft", block["merged_by_month"]["2024-03"])
        self.assertEqual(block["nibrs_by_category"]["THEFT FROM MOTOR VEHICLE"], 2)
        self.assertEqual(document["nibrs_heat"]["incident_count"], 2)


if __name__ == "__main__":
    unittest.main()
