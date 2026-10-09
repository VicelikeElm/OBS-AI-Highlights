import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
from unittest.mock import patch

import game_timeline
import session_stats


class RecordingTimelineTests(unittest.TestCase):
    def test_native_obs_chapter_is_added_when_enabled(self):
        class FakeObsClient:
            def __init__(self):
                self.chapters = []

            def create_record_chapter(self, chapter_name=None):
                self.chapters.append(chapter_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                client = FakeObsClient()
                timeline = game_timeline.RecordingTimeline(
                    client,
                    embed_chapters=True,
                )
                timeline.update({"recording": True}, now=10.0)

                self.assertTrue(
                    timeline.add_marker("KILL", "Eliminated Rival", now=12.0)
                )
                record = session_stats.get_session(timeline.session_id)

        self.assertEqual(client.chapters, ["KILL: Eliminated Rival"])
        self.assertEqual(record["chapter_embedding"], "embedded")
        self.assertEqual(record["embedded_chapters_count"], 1)

    def test_native_obs_chapter_failure_is_recorded_and_does_not_lose_marker(self):
        class UnsupportedObsClient:
            def create_record_chapter(self, chapter_name=None):
                raise RuntimeError("Hybrid MP4 is required")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                timeline = game_timeline.RecordingTimeline(
                    UnsupportedObsClient(),
                    embed_chapters=True,
                )
                timeline.update({"recording": True}, now=10.0)
                self.assertTrue(
                    timeline.add_marker("DEATH", "Player down", now=11.0)
                )
                record = session_stats.get_session(timeline.session_id)

        self.assertEqual(record["chapter_embedding"], "failed")
        self.assertEqual(record["chapter_embedding_error"], "Hybrid MP4 is required")
        self.assertEqual(record["timeline_markers"][0]["label"], "Player down")

    def test_native_obs_chapter_is_not_added_when_disabled(self):
        class FakeObsClient:
            def create_record_chapter(self, chapter_name=None):
                raise AssertionError("chapter embedding is disabled")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                timeline = game_timeline.RecordingTimeline(
                    FakeObsClient(),
                    embed_chapters=False,
                )
                timeline.update({"recording": True}, now=10.0)
                self.assertTrue(
                    timeline.add_marker("KILL", "Eliminated Rival", now=12.0)
                )
                record = session_stats.get_session(timeline.session_id)

        self.assertEqual(record["chapter_embedding"], "disabled")

    def test_recording_events_and_manual_markers_use_recording_offsets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                timeline = game_timeline.RecordingTimeline()
                timeline.update(
                    {
                        "recording": True,
                        "record_duration_seconds": 125.0,
                        "recording_file": r"C:\Videos\match.mkv",
                    },
                    "Rainbow Six Siege",
                    "Siege",
                    now=10.0,
                )

                self.assertTrue(
                    timeline.add_marker(
                        "KILL",
                        "Player eliminated Rival",
                        "Rainbow Six Siege",
                        "Siege",
                        now=12.4,
                    )
                )
                record = session_stats.get_session(timeline.session_id)
                self.assertEqual(record["capture_mode"], "recording_timeline")
                self.assertEqual(record["recording_file"], r"C:\Videos\match.mkv")
                self.assertEqual(record["timeline_markers"][0]["elapsed_seconds"], 127.4)
                self.assertEqual(record["timeline_markers"][0]["time"], "00:02:07")

                timeline.update({"recording": False}, now=13.0)
                self.assertIsNone(timeline.session_id)
                self.assertIsNotNone(record["session_id"])
                self.assertTrue(session_stats.get_session(record["session_id"])["ended_at"])

    def test_timeline_csv_export_contains_marker_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                session_id = session_stats.start_recording_timeline("Minecraft", "Minecraft")
                session_stats.record_timeline_marker(
                    session_id,
                    42,
                    "DEATH",
                    "You died",
                    "Minecraft",
                    "Minecraft",
                )
                destination = Path(temp_dir) / "timeline.csv"

                self.assertTrue(session_stats.export_timeline_csv(session_id, destination))
                with destination.open(encoding="utf-8-sig", newline="") as source:
                    rows = list(csv.DictReader(source))

        self.assertEqual(rows, [{
            "time": "00:00:42",
            "elapsed_seconds": "42.0",
            "type": "DEATH",
            "label": "You died",
            "game": "Minecraft",
        }])

    def test_timeline_exports_text_final_cut_premiere_and_resolve_formats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                session_id = session_stats.start_recording_timeline(
                    "Rainbow Six Siege",
                    "Siege",
                )
                session_stats.record_timeline_marker(
                    session_id,
                    42,
                    "KILL",
                    "Eliminated Rival",
                    "Rainbow Six Siege",
                    "Siege",
                )
                outputs = {}
                for export_format, extension in (
                    ("Text", ".txt"),
                    ("Final Cut Pro XML", ".fcpxml"),
                    ("Premiere Pro XML", ".xml"),
                    ("DaVinci Resolve EDL", ".edl"),
                ):
                    destination = Path(temp_dir) / f"markers{extension}"
                    self.assertTrue(
                        session_stats.export_timeline(
                            session_id,
                            destination,
                            export_format,
                        )
                    )
                    outputs[export_format] = destination.read_text(encoding="utf-8")

        self.assertIn("00:00:42 KILL: Eliminated Rival", outputs["Text"])
        fcpxml_root = ET.fromstring(outputs["Final Cut Pro XML"])
        self.assertEqual(fcpxml_root.tag, "fcpxml")
        self.assertEqual(
            fcpxml_root.find(".//marker").get("value"),
            "KILL: Eliminated Rival",
        )
        premiere_root = ET.fromstring(outputs["Premiere Pro XML"])
        self.assertEqual(premiere_root.tag, "xmeml")
        self.assertEqual(
            premiere_root.find(".//sequence/marker/name").text,
            "KILL: Eliminated Rival",
        )
        self.assertIn("FCM: NON-DROP FRAME", outputs["DaVinci Resolve EDL"])
        self.assertIn("* COMMENT: KILL: Eliminated Rival", outputs["DaVinci Resolve EDL"])
        self.assertIn("00:00:42:00", outputs["DaVinci Resolve EDL"])

    def test_unsupported_timeline_export_format_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                session_id = session_stats.start_recording_timeline()
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    session_stats.export_timeline(
                        session_id,
                        Path(temp_dir) / "markers.unknown",
                        "Unknown",
                    )

    def test_ocr_event_timestamp_is_used_instead_of_processing_delay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                event_wall_time = datetime.now()
                with patch(
                    "game_timeline.time.time",
                    return_value=event_wall_time.timestamp() - 5.0,
                ):
                    timeline = game_timeline.RecordingTimeline()
                    timeline.update(
                        {"recording": True, "record_duration_seconds": 30},
                        now=1.0,
                    )
                    timeline.add_marker(
                        "KILL",
                        "Eliminated",
                        now=50.0,
                        event_timestamp=event_wall_time.isoformat(),
                    )
                    markers = session_stats.get_session(
                        timeline.session_id
                    )["timeline_markers"]

        self.assertEqual(markers[0]["elapsed_seconds"], 35.0)


if __name__ == "__main__":
    unittest.main()
