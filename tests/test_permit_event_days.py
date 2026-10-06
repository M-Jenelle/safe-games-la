import unittest
from datetime import date

from pipeline.permit_event_days import (
    build_merge,
    collapse_upcoming,
    comparison_view,
    expand_permit_days,
    filter_permit_rows,
    permit_load_rows,
)


class PermitEventDayTests(unittest.TestCase):
    def test_span_is_inclusive(self):
        self.assertEqual(
            expand_permit_days(date(2022, 6, 1), date(2022, 6, 3)),
            [date(2022, 6, 1), date(2022, 6, 2), date(2022, 6, 3)],
        )

    def test_missing_end_stays_on_start(self):
        self.assertEqual(expand_permit_days(date(2022, 6, 1), None), [date(2022, 6, 1)])

    def test_several_permits_are_one_event_day(self):
        permits = [
            {
                "venue_id": "V06",
                "venue_name": "BMO Stadium",
                "event_name": "Match A",
                "event_start_date": "2022-06-01T00:00:00.000",
                "event_end_date": "2022-06-01T00:00:00.000",
                "venue_distance_m": "100",
            },
            {
                "venue_id": "V06",
                "venue_name": "BMO Stadium",
                "event_name": "Match A booth",
                "event_start_date": "2022-06-01T00:00:00.000",
                "event_end_date": "2022-06-02T00:00:00.000",
                "venue_distance_m": "100",
            },
        ]
        crime = {"V06": {"2022-06-01": 4, "2022-06-02": 0}}
        groups = {"V06": {"assault": {"2022-06-01": 2}}}
        rows, summary = build_merge(permits, crime, groups, {"V06": "BMO Stadium"})
        event_rows = [row for row in rows if row["is_permit_event_day"]]
        self.assertEqual([row["date"] for row in event_rows], ["2022-06-01", "2022-06-02"])
        self.assertEqual(event_rows[0]["permit_count"], 2)
        self.assertEqual(event_rows[0]["incident_count"], 4)
        self.assertEqual(event_rows[0]["assault"], 2)
        venue = summary["venues"][0]
        self.assertEqual(venue["event_day_count"], 2)
        self.assertEqual(venue["permit_rows_in_crime_window"], 2)
        self.assertTrue(summary["meta"]["disclaimer"])

    def test_year_slice_drops_months_without_a_permit(self):
        rows = [
            {"date": "2022-01-10", "is_permit_event_day": 0, "incident_count": 1},
            {"date": "2022-06-01", "is_permit_event_day": 1, "incident_count": 4},
            {"date": "2021-01-10", "is_permit_event_day": 1, "incident_count": 2},
        ]
        kept = filter_permit_rows(rows, year="2022", month="all")
        self.assertEqual([row["date"] for row in kept], ["2022-06-01"])

    def test_small_month_hides_the_percentage(self):
        rows = []
        for day in range(1, 4):
            rows.append({
                "date": f"2022-06-{day:02d}",
                "is_permit_event_day": 1,
                "incident_count": 8,
                "theft": 8,
            })
        for day in range(4, 28):
            rows.append({
                "date": f"2022-06-{day:02d}",
                "is_permit_event_day": 0,
                "incident_count": 1,
                "theft": 0,
            })
        view = comparison_view(rows, year="2022", month="6", venue_id="V06", venue_name="BMO Stadium")
        self.assertEqual(view["summary"]["event_day_count"], 3)
        self.assertFalse(view["summary"]["percent_shown"])
        self.assertIsNone(view["summary"]["lift_pct"])
        self.assertEqual(view["groups"], [])
        self.assertIn("at least 8", view["percent_note"])

    def test_group_chart_keeps_a_material_difference(self):
        rows = []
        for day in range(1, 16):
            rows.append({
                "date": f"2022-07-{day:02d}",
                "is_permit_event_day": 1,
                "incident_count": 4,
                "theft": 4,
                "assault": 1,
            })
        for day in range(16, 32):
            rows.append({
                "date": f"2022-07-{day:02d}",
                "is_permit_event_day": 0,
                "incident_count": 1,
                "theft": 0,
                "assault": 1,
            })
        view = comparison_view(rows, year="all", month="all", venue_id="V06", venue_name="BMO Stadium")
        self.assertTrue(view["summary"]["percent_shown"])
        self.assertEqual([group["group"] for group in view["groups"]], ["theft"])
        self.assertFalse(view["groups"][0]["percent_shown"])
        self.assertEqual(view["groups"][0]["absolute_difference"], 4.0)

    def test_several_permits_are_separated_from_one(self):
        rows = []
        for day in range(1, 11):
            rows.append({
                "date": f"2022-07-{day:02d}",
                "is_permit_event_day": 1,
                "permit_count": 1,
                "incident_count": 2,
            })
        for day in range(11, 21):
            rows.append({
                "date": f"2022-07-{day:02d}",
                "is_permit_event_day": 1,
                "permit_count": 3,
                "incident_count": 6,
            })
        for day in range(21, 32):
            rows.append({
                "date": f"2022-07-{day:02d}",
                "is_permit_event_day": 0,
                "permit_count": 0,
                "incident_count": 1,
            })
        loaded = permit_load_rows(rows)
        by_label = {row["label"]: row for row in loaded}
        self.assertEqual(by_label["One permit"]["day_count"], 10)
        self.assertEqual(by_label["One permit"]["mean"], 2)
        self.assertEqual(by_label["Several permits"]["day_count"], 10)
        self.assertEqual(by_label["Several permits"]["mean"], 6)
        self.assertEqual(by_label["Other days"]["day_count"], 11)

    def test_upcoming_days_with_the_same_events_collapse(self):
        spans = collapse_upcoming([
            {"date": "2026-03-15", "permit_count": 2, "event_names": ["LA MARATHON"]},
            {"date": "2026-03-16", "permit_count": 2, "event_names": ["LA MARATHON"]},
            {"date": "2026-03-18", "permit_count": 2, "event_names": ["LA MARATHON"]},
        ])
        self.assertEqual(
            [(row["start"], row["end"]) for row in spans],
            [("2026-03-15", "2026-03-16"), ("2026-03-18", "2026-03-18")],
        )


if __name__ == "__main__":
    unittest.main()
