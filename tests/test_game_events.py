import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import game_detector
import game_capture
from game_capture import build_clip_context
from game_events import GameEventMonitor, parse_game_events
from round_tracker import RoundTracker
import verify_clips


class FakeProcess:
    def __init__(self, name):
        self.info = {"name": name}


class GameEventTests(unittest.TestCase):
    def test_parse_siege_hud_events_and_player_kill(self):
        profile = game_detector.load_game_profiles()[0]
        events = parse_game_events(
            "AcePlayer eliminated Rival - Headshot\n"
            "Rival eliminated AcePlayer\n"
            "AcePlayer M4 Rival\n"
            "Rival M4 AcePlayer\n"
            "AcePlayer match point\n"
            "Defuser planted\n"
            "Round victory",
            player_name="AcePlayer",
            profile=profile,
        )

        self.assertEqual(
            [event["type"] for event in events],
            [
                "KILL",
                "HEADSHOT",
                "DEATH",
                "KILL",
                "DEATH",
                "MATCH_POINT",
                "PLANT",
                "ROUND_WIN",
            ],
        )

    def test_monitor_suppresses_repeated_ocr_event(self):
        monitor = GameEventMonitor(
            {"profile": {}},
            player_name="",
        )
        samples = iter(("Round victory", "Round victory\nextra noise"))
        monitor.capture_text = lambda: next(samples)

        self.assertEqual(len(monitor.poll_events()), 1)
        self.assertEqual(monitor.poll_events(), [])

    def test_siege_process_detection_and_unknown_process(self):
        self.assertEqual(
            game_detector.detect_running_game([FakeProcess("RainbowSix.exe")])["profile_name"],
            "Siege",
        )
        self.assertIsNone(
            game_detector.detect_running_game([FakeProcess("unrelated.exe")])
        )

    def test_game_selection_filters_process_detection(self):
        process = [FakeProcess("RainbowSix.exe")]

        self.assertEqual(
            game_detector.detect_running_game(
                process,
                selected_game="Rainbow Six Siege",
            )["game_name"],
            "Rainbow Six Siege",
        )
        self.assertIsNone(
            game_detector.detect_running_game(
                process,
                selected_game="Another Game",
            )
        )

    def test_tarkov_process_is_detected(self):
        detected = game_detector.detect_running_game(
            [FakeProcess("EscapeFromTarkov.exe")],
            selected_game="Escape from Tarkov",
        )

        self.assertEqual(detected["profile_name"], "Tarkov")

    def test_event_clip_context_includes_tags_and_trigger_events(self):
        tracker = RoundTracker("Rainbow Six Siege", "Siege")
        event = {
            "type": "KILL",
            "text": "AcePlayer eliminated Rival",
            "timestamp": "2026-10-08T21:00:00",
        }
        snapshot = tracker.apply_event(event)

        context = build_clip_context(snapshot, [event])

        self.assertEqual(context["tags"], ["KILL"])
        self.assertEqual(context["trigger_events"], [event])
        self.assertEqual(context["capture_source"], "game_event_ocr")

    def test_event_clip_is_saved_with_game_tags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recording_folder = Path(temp_dir) / "recordings"
            recording_folder.mkdir()
            output_folder = Path(temp_dir) / "output"
            replay_file = recording_folder / "obs-replay.mp4"
            replay_file.write_bytes(b"test video")
            context = {
                "game": "Rainbow Six Siege",
                "profile": "Siege",
                "tags": ["KILL"],
                "trigger_events": [{"type": "KILL", "text": "eliminated"}],
            }

            class FakeObsClient:
                def save_replay_buffer(self):
                    self.saved = True

            client = FakeObsClient()
            with patch.object(game_capture, "_wait_for_replay", return_value=replay_file):
                clip_path = game_capture.save_event_clip(
                    client,
                    recording_folder,
                    output_folder,
                    context,
                    1,
                )

            self.assertTrue(client.saved)
            self.assertTrue(clip_path.is_file())
            sidecar = output_folder / "Transcripts" / f"{clip_path.stem}_game.json"
            self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), context)
            with patch.object(verify_clips, "LIVE_TRANSCRIPT_FOLDER", str(output_folder / "Transcripts")):
                self.assertEqual(
                    verify_clips.load_game_context(clip_path.stem),
                    context,
                )

    def test_game_context_survives_candidate_sidecar_load(self):
        context = {
            "game": "Rainbow Six Siege",
            "profile": "Siege",
            "score": 85,
            "achievements": ["Double Kill"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            sidecar = Path(temp_dir) / "clip_game.json"
            sidecar.write_text(json.dumps(context), encoding="utf-8")
            with patch.object(verify_clips, "LIVE_TRANSCRIPT_FOLDER", temp_dir):
                self.assertEqual(verify_clips.load_game_context("clip"), context)

    def test_round_tracker_awards_achievements_and_combines_scores(self):
        tracker = RoundTracker("Rainbow Six Siege", "Siege")
        snapshot = tracker.apply_event({"type": "KILL", "text": "kill one"})
        snapshot = tracker.apply_event({"type": "KILL", "text": "kill two"})
        snapshot = tracker.apply_event({"type": "KILL", "text": "kill three"})

        self.assertEqual(snapshot["kills"], 3)
        self.assertIn("Double Kill", snapshot["achievements"])
        self.assertIn("Triple Kill", snapshot["achievements"])
        self.assertTrue(RoundTracker.should_trigger(snapshot))

        total, reasons = RoundTracker.combine_score(45, snapshot)
        self.assertEqual(total, 260)
        self.assertIn("game events (+215)", reasons)
        self.assertIn("Triple Kill", reasons)

    def test_round_win_awards_objective_achievement_and_resets_next_round(self):
        tracker = RoundTracker("Rainbow Six Siege", "Siege")
        tracker.apply_event({"type": "PLANT", "text": "planted"})
        snapshot = tracker.apply_event({"type": "ROUND_WIN", "text": "round won"})

        self.assertTrue(snapshot["won"])
        self.assertIn("Plant and Win", snapshot["achievements"])
        self.assertTrue(RoundTracker.should_trigger(snapshot))

        next_round = tracker.apply_event({"type": "KILL", "text": "next round"})
        self.assertEqual(next_round["round"], 2)
        self.assertEqual(next_round["kills"], 1)


if __name__ == "__main__":
    unittest.main()
