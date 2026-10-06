"""Display sentences are built from the radius and the permit rules."""

from __future__ import annotations

import unittest

from backend.briefing import (
    circle_area_km2,
    density_hint,
    overlap_note,
    pair_overlap_note,
    reports_through_label,
    weekday_summary,
)


class BriefingTests(unittest.TestCase):
    def test_cutoff_is_the_day_before_nibrs(self):
        self.assertEqual(reports_through_label("2024-03-07"), "March 6, 2024")

    def test_density_hint_uses_the_circle(self):
        self.assertAlmostEqual(circle_area_km2(800), 2.01, places=2)
        hint = density_hint(800, 14, "March 6, 2024")
        self.assertIn("800 m circle", hint)
        self.assertIn("2.01 km²", hint)
        self.assertIn("March 6, 2024", hint)
        self.assertIn("other 13", hint)

    def test_overlap_sentences_use_the_radius(self):
        self.assertEqual(overlap_note(800, []), "")
        note = overlap_note(800, ["Crypto.com Arena", "LA Convention Center"])
        self.assertIn("800 m circle overlaps Crypto.com Arena and LA Convention Center", note)
        pair = pair_overlap_note(800, "Peacock Theater", "Crypto.com Arena")
        self.assertIn("circles of Peacock Theater and Crypto.com Arena", pair)

    def test_weekday_summary_keeps_the_first_tie(self):
        block = {
            "total": 10,
            "weekend_count": 3,
            "days": [
                {"label": "Monday", "count": 4},
                {"label": "Tuesday", "count": 4},
            ],
        }
        text = weekday_summary(block, "incidents")
        self.assertTrue(text.startswith("Monday is the busiest day, 4 incidents."))
        self.assertIn("Weekend days are 3 (30%)", text)


if __name__ == "__main__":
    unittest.main()
