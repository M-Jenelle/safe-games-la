import unittest
from datetime import date

from pipeline.permit_event_days import build_merge, expand_permit_days


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


if __name__ == "__main__":
    unittest.main()
