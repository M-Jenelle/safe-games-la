import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.nibrs import schedule_create_args
from pipeline.prepare import Step, run_missing
from pipeline.refresh import after_sync


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

    def test_schedule_is_a_daily_morning_check(self):
        args = schedule_create_args()
        self.assertIn("DAILY", args)
        self.assertIn("06:15", args)
        self.assertNotIn("WEEKLY", args)
        self.assertIn("Safe Games LA NIBRS sync", args)


if __name__ == "__main__":
    unittest.main()
