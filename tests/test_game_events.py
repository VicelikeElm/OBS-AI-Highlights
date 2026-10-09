import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import game_detector
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
