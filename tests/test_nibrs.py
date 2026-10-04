"""NIBRS sync decides from Socrata's rowsUpdatedAt and keeps person fields out."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.nibrs import COLUMNS, DATASETS, NibrsDataset, needs_download, sync_dataset


class NeedsDownloadTests(unittest.TestCase):
    def test_skips_when_the_update_timestamp_matches(self):
        self.assertFalse(needs_download(100, 100, force=False))

    def test_downloads_when_the_portal_timestamp_changes(self):
        self.assertTrue(needs_download(100, 200, force=False))
        self.assertTrue(needs_download(None, 200, force=False))
        self.assertTrue(needs_download(100, 100, force=True))


class SyncDatasetTests(unittest.TestCase):
    def test_extract_columns_omit_person_and_premise_fields(self):
        joined = " ".join(COLUMNS)
        for banned in ("victim", "premis", "homeless", "suspect", "arrestee", "hate", "domestic"):
            self.assertNotIn(banned, joined)
        self.assertIn("hndrdth_lat", COLUMNS)
        self.assertIn("nibr_description", COLUMNS)

    def test_unchanged_metadata_does_not_download(self):
        dataset = NibrsDataset("k7nn-b2ep", "current", "nibrs_current.csv")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            manifest = {
                "datasets": {
                    "k7nn-b2ep": {
                        "rows_updated_at": 50,
                        "rows": 3,
                        "filename": "nibrs_current.csv",
                    }
                }
            }
            calls = {"download": 0}

            def fetch_meta(view_id: str) -> dict:
                return {"view_id": view_id, "name": "LAPD NIBRS Offenses Dataset", "rows_updated_at": 50}

            def download(dataset_arg, dest):
                calls["download"] += 1
                raise AssertionError("download should not run")

            record = sync_dataset(
                dataset,
                directory,
                manifest,
                force=False,
                fetch_meta=fetch_meta,
                download=download,
            )
        self.assertEqual(record["status"], "unchanged")
        self.assertEqual(calls["download"], 0)

    def test_newer_metadata_writes_the_manifest(self):
        dataset = DATASETS[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)

            def fetch_meta(view_id: str) -> dict:
                return {"view_id": view_id, "name": "LAPD NIBRS Offenses Dataset", "rows_updated_at": 1790709516}

            def download(dataset_arg, dest):
                dest.write_text("caseno\n1\n", encoding="utf-8")
                return 1

            manifest = {"datasets": {}}
            record = sync_dataset(
                dataset,
                directory,
                manifest,
                force=False,
                fetch_meta=fetch_meta,
                download=download,
            )
        self.assertEqual(record["status"], "downloaded")
        self.assertEqual(record["rows"], 1)
        self.assertEqual(json.loads(json.dumps(manifest))["datasets"]["k7nn-b2ep"]["rows_updated_at"], 1790709516)


if __name__ == "__main__":
    unittest.main()
