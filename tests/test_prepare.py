import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.nibrs_cloud import SCHEDULE_CRON, SCHEDULE_ZONE
from pipeline.prepare import Step, ensure_schedule, run_missing
from pipeline.refresh import after_sync, rebuild_derived


class PrepareTests(unittest.TestCase):
    def test_unittest_does_not_prepare_on_import(self):
        from backend.main import _skip_prepare

        self.assertTrue(_skip_prepare())

    def test_existing_output_is_not_rebuilt(self):
        with TemporaryDirectory() as directory:
            ready = Path(directory) / "done.json"
            ready.write_text("{}", encoding="utf-8")
            calls = []
            notes = run_missing([
                Step("buffers", (ready,), lambda: calls.append("ran"), lambda: True, ""),
            ])
        self.assertEqual(calls, [])
        self.assertEqual(notes, ["ready  buffers"])

    def test_missing_output_runs_once(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "done.json"
            calls = []

            def build():
                calls.append("ran")
                target.write_text("{}", encoding="utf-8")

            notes = run_missing([
                Step("buffers", (target,), build, lambda: True, ""),
            ])
        self.assertEqual(calls, ["ran"])
        self.assertEqual(notes, ["build  buffers"])

    def test_missing_input_is_skipped(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "done.json"
            notes = run_missing([
                Step("buffers", (target,), lambda: None, lambda: False, "crime file is missing"),
            ])
        self.assertEqual(notes, ["skip   buffers: crime file is missing"])
        self.assertFalse(target.exists())

    def test_unchanged_nibrs_does_not_rebuild(self):
        with TemporaryDirectory() as directory:
            derived = Path(directory) / "crime_merged.json"
            source = Path(directory) / "nibrs_current.csv"
            derived.write_text("{}", encoding="utf-8")
            source.write_text("caseno\n", encoding="utf-8")
            calls = []
            ran = after_sync(
                {"datasets": {"k7nn-b2ep": {"status": "unchanged"}}},
                rebuild=lambda: calls.append("rebuilt"),
                outputs=(derived,),
                nibrs_path=source,
            )
        self.assertFalse(ran)
        self.assertEqual(calls, [])

    def test_new_nibrs_download_rebuilds(self):
        with TemporaryDirectory() as directory:
            derived = Path(directory) / "crime_merged.json"
            source = Path(directory) / "nibrs_current.csv"
            source.write_text("caseno\n", encoding="utf-8")
            calls = []
            ran = after_sync(
                {"datasets": {"k7nn-b2ep": {"status": "downloaded"}}},
                rebuild=lambda: calls.append("rebuilt"),
                outputs=(derived,),
                nibrs_path=source,
            )
        self.assertTrue(ran)
        self.assertEqual(calls, ["rebuilt"])

    def test_schedule_is_tuesday_evening_pacific(self):
        self.assertEqual(SCHEDULE_CRON, "0 18 * * 2")
        self.assertEqual(SCHEDULE_ZONE, "America/Los_Angeles")
        self.assertEqual(
            ensure_schedule(),
            "ready  NIBRS schedule (Cloud Scheduler, Tuesdays 18:00 America/Los_Angeles)",
        )

    def test_rebuild_refreshes_daily_crime_counts(self):
        from unittest.mock import patch

        with (
            patch("pipeline.merge_crime.main") as merge,
            patch("pipeline.nibrs_charts.main") as charts,
            patch("pipeline.city_baseline.main") as baseline,
            patch("pipeline.refresh.refresh_venue_days") as days,
        ):
            rebuild_derived()
        merge.assert_called_once()
        charts.assert_called_once()
        baseline.assert_called_once()
        days.assert_called_once()


if __name__ == "__main__":
    unittest.main()
