"""Day-of-week counts stay on the calendar date."""

from __future__ import annotations

import unittest

from pipeline.weekday import combine_weekday, weekday_from_dates


class WeekdayTests(unittest.TestCase):
    def test_weekend_is_saturday_and_sunday(self):
        block = weekday_from_dates([
            "2024-03-04",
            "2024-03-04",
            "2024-03-09",
            "2024-03-10",
            "not-a-date",
        ])
        self.assertEqual(block["total"], 4)
        self.assertEqual(block["weekday_count"], 2)
        self.assertEqual(block["weekend_count"], 2)
        monday = next(day for day in block["days"] if day["id"] == "mon")
        self.assertEqual(monday["count"], 2)
        self.assertEqual(block["by_month"]["2024-03"]["total"], 4)
        self.assertNotIn("by_month", block["by_month"]["2024-03"])

    def test_groups_stay_on_their_day(self):
        block = weekday_from_dates([
            {"date": "2024-03-04", "group": "theft"},
            {"date": "2024-03-04", "group": "theft"},
            {"date": "2024-03-04", "group": "assault"},
            {"date": "2024-03-09", "group": "vehicle"},
        ])
        monday = next(day for day in block["days"] if day["id"] == "mon")
        self.assertEqual([group["id"] for group in monday["groups"]], ["theft", "assault"])
        self.assertEqual(monday["groups"][0]["count"], 2)
        saturday = next(day for day in block["by_month"]["2024-03"]["days"] if day["id"] == "sat")
        self.assertEqual(saturday["groups"][0]["id"], "vehicle")

    def test_combined_groups_add(self):
        early = weekday_from_dates([{"date": "2024-03-04", "group": "theft"}])
        later = weekday_from_dates([{"date": "2024-03-11", "group": "theft"}, {"date": "2024-03-11", "group": "assault"}])
        monday = next(day for day in combine_weekday(early, later)["days"] if day["id"] == "mon")
        self.assertEqual(monday["count"], 3)
        self.assertEqual(monday["groups"][0], {"id": "theft", "label": "Theft", "count": 2})

    def test_months_add_without_double_counting_a_split_month(self):
        early = weekday_from_dates(["2024-03-04", "2024-03-05"])
        later = weekday_from_dates(["2024-03-09", "2024-06-01"])
        combined = combine_weekday(early, later)
        self.assertEqual(combined["total"], 4)
        self.assertEqual(combined["by_month"]["2024-03"]["total"], 3)
        self.assertEqual(combined["by_month"]["2024-06"]["weekend_count"], 1)
