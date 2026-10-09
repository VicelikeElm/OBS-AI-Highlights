import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import clip_manager
import config
import game_capture
import game_detector
import session_stats


class FakeProcess:
    def __init__(self, name):
        self.info = {"name": name}


class GameProfileSettingsTests(unittest.TestCase):
    def test_load_config_migrates_flat_values_to_the_selected_game(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(
                json.dumps({
                    "game_selection": "Escape from Tarkov",
                    "game_player_name": "TarkovName",
                    "game_clip_before_seconds": 18,
                    "game_clip_after_seconds": 4,
                }),
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", path):
                loaded = config.load_config()

        profile = loaded["game_profiles"]["Escape from Tarkov"]
        self.assertEqual(profile["player_name"], "TarkovName")
        self.assertEqual(profile["clip_before_seconds"], 18)
        self.assertEqual(profile["clip_after_seconds"], 4)
        self.assertEqual(
            config.get_game_profile_settings(loaded, "Rainbow Six Siege")["player_name"],
            "",
        )

    def test_legacy_game_settings_migrate_and_profiles_stay_isolated(self):
        saved_config = {
            "game_selection": "Rainbow Six Siege",
            "game_player_name": "LegacyName",
            "game_ocr_left": 0.55,
            "game_profiles": {},
        }

        siege = config.get_game_profile_settings(saved_config)
        self.assertEqual(siege["player_name"], "LegacyName")
        self.assertEqual(siege["ocr_left"], 0.55)

        config.save_game_profile_settings(
            saved_config,
            "Rainbow Six Siege",
            {"process_names": ["CustomSiege.exe"]},
        )
        saved_config["game_selection"] = "Escape from Tarkov"

        tarkov = config.get_game_profile_settings(saved_config)
        self.assertEqual(tarkov["player_name"], "")
        self.assertNotEqual(tarkov["ocr_left"], 0.55)
        self.assertEqual(
            config.get_game_profile_settings(saved_config, "Rainbow Six Siege")["process_names"],
            ["CustomSiege.exe"],
        )

    def test_process_name_override_is_used_for_builtin_game(self):
        detected = game_detector.detect_running_game(
            [FakeProcess("MySiege.exe")],
            selected_game="Rainbow Six Siege",
            profile_overrides={
                "Rainbow Six Siege": {"process_names": ["MySiege.exe"]}
            },
        )
        self.assertEqual(detected["process_name"], "MySiege.exe")
        self.assertIsNone(
            game_detector.detect_running_game(
                [FakeProcess("RainbowSix.exe")],
                selected_game="Rainbow Six Siege",
                profile_overrides={
                    "Rainbow Six Siege": {"process_names": ["MySiege.exe"]}
                },
            )
        )


class GameClipFilterTests(unittest.TestCase):
    def test_game_and_event_filters_preserve_unannotated_clip_behavior(self):
        clips = [
            {"base_name": "siege", "game_context": {"game": "Siege", "tags": ["KILL"]}},
            {"base_name": "tarkov", "game_context": {"game": "Tarkov", "tags": ["HEADSHOT"]}},
            {"base_name": "audio", "game_context": None},
        ]

        self.assertEqual(
            [clip["base_name"] for clip in clip_manager.filter_game_clips(clips)],
            ["siege", "tarkov", "audio"],
        )
        self.assertEqual(
            [clip["base_name"] for clip in clip_manager.filter_game_clips(clips, "Siege")],
            ["siege"],
        )
        self.assertEqual(
            [
                clip["base_name"]
                for clip in clip_manager.filter_game_clips(clips, "All", "HEADSHOT")
            ],
            ["tarkov"],
        )


class GameClipTimingTests(unittest.TestCase):
    def test_clip_context_keeps_configured_window(self):
        context = game_capture.build_clip_context(
            {
                "game": "Siege",
                "profile": "Siege",
                "round": 1,
                "score": 30,
                "events": [],
                "achievements": [],
            },
            [{"type": "KILL", "text": "player eliminated"}],
            before_seconds=20,
            after_seconds=5,
        )
        self.assertEqual(
            context["clip_window"],
            {"before_seconds": 20, "after_seconds": 5},
        )
        self.assertTrue(
            game_capture.should_trigger_event_clip(
                {"type": "kill"},
                {"KILL", "ROUND_WIN"},
            )
        )
        self.assertFalse(
            game_capture.should_trigger_event_clip(
                {"type": "DEATH"},
                {"KILL", "ROUND_WIN"},
            )
        )

    def test_save_deadline_waits_for_post_event_window(self):
        self.assertEqual(game_capture.clip_save_deadline(100, 5), 105)
        self.assertEqual(game_capture.clip_save_deadline(100, 0), 100)


class GameSessionStatsTests(unittest.TestCase):
    def test_game_session_records_events_rounds_achievements_and_clips(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                session_id = session_stats.start_game_session("Siege", "Siege")
                session_stats.record_game_event(
                    session_id,
                    {"type": "ROUND_WIN"},
                    {"achievements": ["Round Victory"]},
                )
                session_stats.record_game_clip(session_id, Path(temp_dir) / "clip.mp4")
                record = session_stats.get_session(session_id)

        self.assertEqual(record["capture_mode"], "game_events")
        self.assertEqual(record["game_event_counts"], {"ROUND_WIN": 1})
        self.assertEqual(record["rounds_completed"], 1)
        self.assertEqual(record["notable_achievements"], ["Round Victory"])
        self.assertEqual(record["game_clips_saved"], 1)
        self.assertEqual(record["saved_count"], 1)
        self.assertEqual(len(record["saved_clips"]), 1)

    def test_ai_session_can_include_game_event_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(session_stats, "_sessions_dir", return_value=Path(temp_dir)):
                session_id = session_stats.start_session("Gaming")
                session_stats.set_game_session_profile(session_id, "Siege", "Siege")
                session_stats.record_game_event(
                    session_id,
                    {"type": "KILL"},
                    {"achievements": []},
                )
                session_stats.record_game_clip_count(session_id)
                record = session_stats.get_session(session_id)

        self.assertEqual(record["thoughts_analyzed"], 0)
        self.assertEqual(record["game_name"], "Siege")
        self.assertEqual(record["game_event_counts"], {"KILL": 1})
        self.assertEqual(record["game_clips_saved"], 1)


if __name__ == "__main__":
    unittest.main()
