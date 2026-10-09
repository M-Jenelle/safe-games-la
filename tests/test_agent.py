"""Tool-layer checks for the Torchy agent. No model call.

These are the pass criteria for questions the pattern bot cannot answer:
a cross-venue comparison, a series split, an overlap warning, and a forecast
that carries an interval, a backtest, and the no-precedent warning.
"""

import unittest

from unittest.mock import patch

from backend.agent import _join_gaps, _without_safety_judgment, brief_reply
from backend.claude import ClaudeUnavailable
from backend.agent_tools import (
    FORECAST_WARNING,
    LABEL_WARNING,
    SERIES_WARNING,
    compare_with_city,
    explain_page,
    correlate,
    describe_data,
    forecast,
    run_sql,
)
from backend.warehouse import reset


class AgentToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset()

    def test_describe_lists_the_daily_table_and_the_series_break(self):
        described = describe_data()
        names = {table["table"] for table in described["tables"]}
        self.assertIn("venue_days", names)
        self.assertIn("incidents", names)
        self.assertIn("overlap_pairs", names)
        self.assertTrue(any("March 7, 2024" in warning for warning in described["warnings"]))
        days = next(table for table in described["tables"] if table["table"] == "venue_days")
        self.assertIn("count_source", {column["name"] for column in days["columns"]})
        self.assertLessEqual(days["coverage"]["start"], "2024-03-07")
        self.assertGreaterEqual(days["coverage"]["end"], "2024-03-07")

    def test_sql_rejects_a_write_and_caps_rows(self):
        denied = run_sql("DELETE FROM venues")
        self.assertIn("error", denied)
        self.assertEqual(denied["rows"], [])
        counted = run_sql("SELECT venue_id, date, incident_count, count_source FROM venue_days")
        self.assertLessEqual(counted["row_count"], 200)
        self.assertTrue(counted["truncated"])
        shaped = run_sql("WITH days AS (SELECT venue_id FROM venues) SELECT venue_id FROM days")
        self.assertGreater(shaped["row_count"], 0)
        self.assertNotIn("error", shaped)

    def test_sql_warns_when_a_count_crosses_the_series_break(self):
        result = run_sql(
            """
            SELECT sum(incident_count) AS records
            FROM venue_days
            WHERE venue_id = 'V01'
            """
        )
        self.assertEqual(result["row_count"], 1)
        self.assertIn(SERIES_WARNING, result["warnings"])

    def test_offense_groups_keep_the_two_label_systems(self):
        result = run_sql(
            """
            SELECT series, label, records
            FROM offense_counts
            WHERE venue_id = 'V01'
            ORDER BY series, label
            """
        )
        series = {row["series"] for row in result["rows"]}
        self.assertIn("lapd_report", series)
        self.assertIn("nibrs", series)
        self.assertIn(LABEL_WARNING, result["warnings"])

    def test_city_comparison_returns_a_ratio(self):
        result = compare_with_city("V01")
        reports = result["reports_2020_2024"]
        self.assertGreater(reports["ratio"], 0)
        self.assertIn(reports["relation"], {"above", "below", "near"})

    def test_page_tool_returns_the_printed_figures_and_a_definition(self):
        page = explain_page("V01", "overview")
        labels = {entry["label"]: entry for entry in page["entries"]}
        self.assertEqual(page["venue"], "Dodger Stadium")
        self.assertIn("Incidents", labels)
        self.assertGreater(labels["Incidents"]["value"], 0)
        self.assertIn("per square kilometer", labels["Density"]["means"])
        kinds = explain_page("Dodger Stadium", "categories")
        self.assertGreater(len(kinds["entries"]), 1)
        self.assertTrue(any("March 7, 2024" in warning for warning in kinds["warnings"]))
        meaning = explain_page("", "density")
        self.assertIsNone(meaning["entries"][0]["value"])
        self.assertIn("square kilometer", meaning["entries"][0]["means"])

    def test_brief_replies_stay_short(self):
        groups = brief_reply("Which offense groups are on the Coliseum page, and what do those labels mean after March 2024?")
        self.assertLess(len(groups["answer"]), 280)
        self.assertIn("NIBRS", groups["answer"])
        self.assertNotIn("I can answer a venue", groups["answer"])
        forecast = brief_reply("If the next 30 days at Dodger Stadium are wet, what daily range does the count model give?")
        self.assertIn("times a dry day", forecast["answer"])
        self.assertIn("no Olympic precedent", forecast["answer"])
        self.assertLess(len(forecast["answer"]), 280)
        highest = brief_reply("Which venue is highest overall?")
        self.assertIn("most records", highest["answer"])
        self.assertLess(len(highest["answer"]), 160)
        counted = brief_reply("How many incidents were reported near Dodger Stadium?")
        self.assertIn("1,418", counted["answer"])
        self.assertLess(len(counted["answer"]), 220)
        city = brief_reply("How does Dodger Stadium compare with the city?")
        self.assertIn("citywide rate", city["answer"])
        self.assertIn("1,148.3", city["answer"])
        weekend = brief_reply("What does the week look like on weekends near the Coliseum?")
        self.assertIn("busiest day", weekend["answer"])

    def test_two_venues_share_one_wet_day_lead(self):
        joined = _join_gaps([
            "No clear difference. Near Dodger Stadium, wet days average 0.52 records and other days of the same weekday average 0.58, from 435 wet days.",
            "No clear difference. Near LA Memorial Coliseum, wet days average 2.76 records and other days of the same weekday average 2.81, from 421 wet days.",
        ])
        self.assertTrue(joined.startswith("No clear difference at Dodger Stadium or LA Memorial Coliseum."))
        self.assertEqual(joined.count("No clear difference"), 1)
        self.assertIn("\n\nDodger Stadium:", joined)
        self.assertIn("\n\nLA Memorial Coliseum:", joined)

    def test_correlate_wet_days_returns_n_and_an_interval(self):
        result = correlate(
            "SELECT date, incident_count FROM venue_days WHERE venue_id = 'V01' AND count_source = 'report'",
            "SELECT date, wet_day FROM venue_days WHERE venue_id = 'V01' AND count_source = 'report'",
        )
        self.assertGreaterEqual(result["n"], 30)
        self.assertIn("spearman", result)
        self.assertIsNotNone(result.get("count_model"))
        self.assertEqual(len(result["count_model"]["interval"]), 2)
        self.assertTrue(any("not a cause" in warning for warning in result["warnings"]))

    def test_forecast_includes_interval_backtest_and_the_precedent_warning(self):
        result = forecast("V01", 30, ["wet_day"], {"wet_day": 1})
        self.assertEqual(len(result["interval"]), 2)
        self.assertIsNotNone(result["backtest"]["mae"])
        self.assertGreater(result["backtest"]["holdout_days"], 0)
        self.assertIn(FORECAST_WARNING, result["warnings"])
        self.assertIn("no precedent", FORECAST_WARNING)
        self.assertIn("proxy events", FORECAST_WARNING)

    def test_a_safety_judgment_is_replaced(self):
        self.assertEqual(
            _without_safety_judgment("Dodger Stadium is safe at night."),
            "I can't call a neighborhood safe or unsafe without a number against the citywide rate.",
        )
        compared = _without_safety_judgment(
            "Compared with the citywide rate, this circle is 6.1 times higher, so it is comparatively unsafe."
        )
        self.assertIn("comparatively unsafe", compared)
        self.assertIn("6.1", compared)
        kept = _without_safety_judgment("V01 has 1,418 records. The area is unsafe.")
        self.assertIn("1,418", kept)
        self.assertNotIn("area is unsafe", kept)
        self.assertIn("without a number against the citywide rate.", kept)

    def test_auth_failure_is_an_unavailable_model(self):
        from backend.agent import agent_answer

        with patch("backend.agent._gemini_configuration", return_value=("proj", "us-west1", "gemini-2.5-flash")), \
             patch("backend.agent._access_token", side_effect=RuntimeError("reauth")):
            with self.assertRaises(ClaudeUnavailable):
                agent_answer("How many records are near Dodger Stadium?")


if __name__ == "__main__":
    unittest.main()
