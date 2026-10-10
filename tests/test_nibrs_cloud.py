import json
import os
import unittest
from unittest.mock import patch

from pipeline.nibrs_cloud import (
    MANIFEST_OBJECT,
    REQUIRED,
    prepare_job,
    publish_and_roll,
    pull_if_newer,
    stamp,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def get(self, bucket: str, name: str) -> bytes | None:
        return self.objects.get((bucket, name))

    def put(self, bucket: str, name: str, payload: bytes) -> None:
        self.objects[(bucket, name)] = payload


def _manifest(updated: int, status: str = "unchanged") -> dict:
    return {
        "datasets": {
            "k7nn-b2ep": {
                "status": status,
                "rows_updated_at": updated,
            }
        }
    }


class NibrsCloudTests(unittest.TestCase):
    def test_stamp_reads_the_current_view(self):
        self.assertEqual(stamp(_manifest(1790709516)), 1790709516)
        self.assertIsNone(stamp({}))

    def test_pull_skips_when_local_is_current(self):
        store = MemoryStorage()
        store.put("bucket", MANIFEST_OBJECT, json.dumps(_manifest(10)).encode())
        with (
            patch.dict(os.environ, {"NIBRS_BUCKET": "bucket"}),
            patch("pipeline.nibrs_cloud._read_manifest", return_value=_manifest(10)),
            patch("pipeline.nibrs_cloud._write_bytes") as write,
        ):
            self.assertFalse(pull_if_newer(store))
        write.assert_not_called()

    def test_pull_writes_required_files_before_the_manifest(self):
        store = MemoryStorage()
        store.put("bucket", MANIFEST_OBJECT, json.dumps(_manifest(20)).encode())
        for name in REQUIRED:
            store.put("bucket", name, b"new")
        written: list[str] = []

        def capture(name: str, payload: bytes) -> None:
            written.append(name)
            self.assertTrue(payload)

        with (
            patch.dict(os.environ, {"NIBRS_BUCKET": "bucket"}),
            patch("pipeline.nibrs_cloud._read_manifest", return_value=_manifest(10)),
            patch("pipeline.nibrs_cloud._write_bytes", side_effect=capture),
        ):
            self.assertTrue(pull_if_newer(store))
        self.assertEqual(written[-1], MANIFEST_OBJECT)
        self.assertEqual(set(written[:-1]), set(REQUIRED))

    def test_prepare_job_adopts_the_published_manifest(self):
        store = MemoryStorage()
        store.put("bucket", MANIFEST_OBJECT, json.dumps(_manifest(30)).encode())
        with (
            patch.dict(os.environ, {"NIBRS_BUCKET": "bucket"}),
            patch("pipeline.nibrs_cloud._read_manifest", return_value=_manifest(10)),
            patch("pipeline.nibrs_cloud._write_bytes") as write,
        ):
            prepare_job(store)
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], MANIFEST_OBJECT)

    def test_unchanged_check_does_not_upload(self):
        store = MemoryStorage()
        with (
            patch.dict(os.environ, {"NIBRS_BUCKET": "bucket"}, clear=False),
            patch("pipeline.nibrs_cloud.roll_service") as roll,
        ):
            os.environ.pop("NIBRS_ROLL_SERVICE", None)
            publish_and_roll(_manifest(10, "unchanged"), False, storage=store)
        self.assertEqual(store.objects, {})
        roll.assert_not_called()

    def test_new_download_uploads_and_reloads_the_service(self):
        store = MemoryStorage()
        calls: list[str] = []

        def roll(generation: str, session=None) -> None:
            calls.append(generation)

        with (
            patch.dict(
                os.environ,
                {"NIBRS_BUCKET": "bucket", "NIBRS_ROLL_SERVICE": "safe-games-la"},
            ),
            patch("pipeline.nibrs_cloud.upload_artifacts", side_effect=lambda storage=None: calls.append("upload")),
            patch("pipeline.nibrs_cloud.roll_service", side_effect=roll),
        ):
            publish_and_roll(_manifest(40, "downloaded"), True, storage=store)
        self.assertEqual(calls, ["upload", "40"])


if __name__ == "__main__":
    unittest.main()
