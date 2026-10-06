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

    def test_months_add_without_double_counting_a_split_month(self):
        early = weekday_from_dates(["2024-03-04", "2024-03-05"])
        later = weekday_from_dates(["2024-03-09", "2024-06-01"])
        combined = combine_weekday(early, later)
        self.assertEqual(combined["total"], 4)
        self.assertEqual(combined["by_month"]["2024-03"]["total"], 3)
        self.assertEqual(combined["by_month"]["2024-06"]["weekend_count"], 1)
