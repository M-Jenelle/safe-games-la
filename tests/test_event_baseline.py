"""Weekday adjustment and the count model sit beside the published percentages."""

from __future__ import annotations

import unittest

from backend.event_baseline import home_game_rows, scale_month, weekday_standardized
from backend.store import home_game_comparison, permit_comparison


class EventBaselineTests(unittest.TestCase):
    def test_published_percentages_stay_in_place(self):
        peacock = permit_comparison("V04")["summary"]
        dodger_permits = permit_comparison("V01")["summary"]
        games = home_game_comparison("V01")["summary"]
        self.assertEqual(peacock["lift_pct"], 19.5)
        self.assertEqual(dodger_permits["lift_pct"], 298.4)
        self.assertEqual(games["lift_pct"], 266.3)

    def test_same_weekday_and_count_model_are_separate_figures(self):
        peacock = permit_comparison("V04")["baseline"]
        games = home_game_comparison("V01")["baseline"]
        for baseline in (peacock, games):
            weekday = baseline["weekday"]
            model = baseline["model"]
            self.assertGreaterEqual(weekday["event_day_count"], 8)
            self.assertLessEqual(model["low"], model["multiplier"])
            self.assertLessEqual(model["multiplier"], model["high"])
            self.assertIn("do not replace the percentage", baseline["text"])
            self.assertIn("not a forecast", baseline["text"])
        self.assertNotEqual(peacock["weekday"]["lift_pct"], 19.5)
        self.assertIn("March 7, 2024", games["text"])

    def test_home_game_rows_match_the_published_window(self):
        rows = home_game_rows()
        games = [row for row in rows if row["is_home_game"]]
        others = [row for row in rows if not row["is_home_game"]]
        self.assertEqual(len(games), 350)
        game_mean = sum(row["incident_count"] for row in games) / len(games)
        other_mean = sum(row["incident_count"] for row in others) / len(others)
        self.assertAlmostEqual(game_mean, 1.14, delta=0.02)
        self.assertAlmostEqual(other_mean, 0.31, delta=0.02)
        weekday = weekday_standardized(rows, "is_home_game")
        self.assertLess(weekday["lift_pct"], 266.3)

    def test_schedule_scales_only_listed_game_days(self):
        self.assertEqual(scale_month(100, 31, 0, 1.5), 100)
        self.assertEqual(scale_month(310, 31, 10, 2), 410)


if __name__ == "__main__":
    unittest.main()
