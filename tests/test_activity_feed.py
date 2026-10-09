import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import (
    MainApp,
    _audio_device_for_preflight,
    _capture_prerequisite_error,
    _check_folder_preflight,
    _check_obs_preflight,
    _friendly_activity_message,
    _summarize_obs_status,
    run_worker,
)


class FriendlyActivityMessageTests(unittest.TestCase):
    def test_translates_common_capture_events(self):
        self.assertEqual(
            _friendly_activity_message("Waiting for OBS...\n"),
            "Waiting for OBS. Open OBS and start its Replay Buffer to continue.",
        )
        self.assertEqual(
            _friendly_activity_message("CLIP SAVED:\n"),
            "A highlight clip was saved.",
        )

    def test_surfaces_warnings_and_unexpected_errors(self):
        self.assertEqual(
            _friendly_activity_message(
                "--- Failed to start Capture: access denied ---\n"
            ),
            "Couldn't start Capture: access denied",
        )
        self.assertEqual(
            _friendly_activity_message("WARNING: Audio capture stalled.\n"),
            "Needs attention: Audio capture stalled.",
        )
        self.assertEqual(
            _friendly_activity_message(
                "Traceback (most recent call last):\n"
            ),
            "A task hit an unexpected error. Open technical details for more information.",
        )
        self.assertEqual(
            _friendly_activity_message("Could not open the recording folder.\n"),
            "Needs attention: Could not open the recording folder.",
        )

    def test_ignores_blank_and_unrecognized_technical_output(self):
        self.assertIsNone(_friendly_activity_message("\n"))
        self.assertIsNone(_friendly_activity_message("Whisper segment probability: 0.42\n"))


class CaptureModeTests(unittest.TestCase):
    def test_capture_buttons_describe_the_selected_mode(self):
        self.assertEqual(MainApp._capture_button_text("game_events"), "Start Game Highlights")
        self.assertEqual(MainApp._capture_button_text("ai"), "Start AI Highlights")

    @patch("game_capture.main")
    @patch("app.app_config.load_config", return_value={"capture_mode": "game_events"})
    def test_game_mode_routes_to_lightweight_worker(self, _load_config, game_capture_main):
        del _load_config
        run_worker("capture")
        game_capture_main.assert_called_once_with()


class CapturePrerequisiteTests(unittest.TestCase):
    @patch("app.shutil.which", return_value=None)
    def test_game_capture_requires_tesseract(self, _which):
        error = _capture_prerequisite_error(
            "capture",
            {"capture_mode": "game_events", "game_events_enabled": True},
        )

        self.assertIn("needs Tesseract OCR", error)
        self.assertIn("AI Highlights can still run", error)

    @patch("app.shutil.which", return_value=None)
    def test_ai_capture_does_not_require_tesseract(self, _which):
        self.assertIsNone(
            _capture_prerequisite_error(
                "capture",
                {"capture_mode": "ai", "game_events_enabled": True},
            )
        )

    def test_disabled_game_capture_reports_settings_path(self):
        error = _capture_prerequisite_error(
            "capture",
            {"capture_mode": "game_events", "game_events_enabled": False},
        )

        self.assertIn("Settings > Advanced > Game Events", error)


class ObsPreflightSummaryTests(unittest.TestCase):
    def test_reports_ready_when_replay_buffer_is_running(self):
        self.assertEqual(
            _summarize_obs_status(replay=True, recording=False, streaming=False),
            ("ready", "OBS is connected and Replay Buffer is on."),
        )

    def test_notes_replay_buffer_can_start_when_obs_is_active(self):
        level, message = _summarize_obs_status(
            replay=False,
            recording=True,
            streaming=False,
        )
        self.assertEqual(level, "warning")
        self.assertIn("Capture can try to start it automatically.", message)

    def test_explains_capture_waits_when_obs_is_idle(self):
        level, message = _summarize_obs_status(
            replay=False,
            recording=False,
            streaming=False,
        )
        self.assertEqual(level, "warning")
        self.assertIn("capture will wait until OBS is active.", message)


class ObsPreflightConnectionTests(unittest.TestCase):
    @patch("app.app_config.get_obs_password", return_value="test-password")
    @patch("app.app_config.load_config", return_value={"obs_host": "localhost", "obs_port": 4455})
    @patch("obsws_python.ReqClient")
    def test_reads_obs_output_status_and_disconnects(
        self,
        req_client,
        _load_config,
        _get_password,
    ):
        del _load_config, _get_password
        client = req_client.return_value
        client.get_replay_buffer_status.return_value = SimpleNamespace(output_active=True)
        client.get_record_status.return_value = SimpleNamespace(output_active=False)
        client.get_stream_status.return_value = SimpleNamespace(output_active=False)
        devices = [{
            "name": "Game Audio",
            "maxInputChannels": 2,
            "isLoopbackDevice": True,
        }]
        audio = SimpleNamespace(
            get_device_count=lambda: len(devices),
            get_device_info_by_index=lambda index: devices[index],
            terminate=lambda: None,
        )
        fake_config = {
            "obs_host": "localhost",
            "obs_port": 4455,
            "recording_folder": tempfile.gettempdir(),
            "output_folder": tempfile.gettempdir(),
            "audio_source_type": "loopback",
            "capture_mode": "ai",
            "audio_loopback_device_name": "Game Audio",
            "game_events_enabled": False,
        }

        with patch.dict("sys.modules", {"pyaudio": SimpleNamespace(PyAudio=lambda: audio)}):
            result = _check_obs_preflight(fake_config)

        checks = {check[0]: (check[2], check[3], check[4]) for check in result}
        self.assertEqual(checks["obs"], ("ready", "OBS is connected and Replay Buffer is on.", ""))
        self.assertEqual(checks["audio"][0], "ready")
        self.assertEqual(checks["recording"][0], "ready")
        self.assertEqual(checks["output"][0], "ready")
        self.assertEqual(checks["ocr"][0], "optional")
        req_client.assert_called_once_with(
            host="localhost",
            port=4455,
            password="test-password",
            timeout=3,
        )
        client.disconnect.assert_called_once_with()

    @patch("app._check_audio_preflight", side_effect=AssertionError("audio must stay unused"))
    @patch("app._check_tesseract_preflight", return_value=("optional", "not installed"))
    @patch("obsws_python.ReqClient")
    def test_game_mode_skips_audio_and_requires_ocr(
        self,
        req_client,
        _tesseract_check,
        _audio_check,
    ):
        del _tesseract_check, _audio_check
        client = req_client.return_value
        client.get_replay_buffer_status.return_value = SimpleNamespace(output_active=True)
        client.get_record_status.return_value = SimpleNamespace(output_active=False)
        client.get_stream_status.return_value = SimpleNamespace(output_active=False)
        fake_config = {
            "capture_mode": "game_events",
            "obs_host": "localhost",
            "obs_port": 4455,
            "recording_folder": tempfile.gettempdir(),
            "output_folder": tempfile.gettempdir(),
            "game_events_enabled": True,
        }

        result = _check_obs_preflight(fake_config)
        checks = {check[0]: (check[2], check[3]) for check in result}

        self.assertEqual(checks["audio"][0], "optional")
        self.assertEqual(checks["ocr"][0], "warning")
        self.assertIn("needs Tesseract", checks["ocr"][1])
        client.disconnect.assert_called_once_with()

    @patch("app.app_config.load_config", return_value={"obs_host": "localhost", "obs_port": 4455})
    @patch("obsws_python.ReqClient", side_effect=OSError("connection refused"))
    def test_returns_actionable_message_when_obs_is_unavailable(
        self,
        _req_client,
        _load_config,
    ):
        del _req_client, _load_config
        fake_config = {
            "obs_host": "localhost",
            "obs_port": 4455,
            "recording_folder": tempfile.gettempdir(),
            "output_folder": tempfile.gettempdir(),
            "audio_source_type": "loopback",
            "capture_mode": "ai",
            "game_events_enabled": False,
        }
        devices = []
        audio = SimpleNamespace(
            get_device_count=lambda: len(devices),
            get_device_info_by_index=lambda index: devices[index],
            terminate=lambda: None,
        )
        with patch.dict("sys.modules", {"pyaudio": SimpleNamespace(PyAudio=lambda: audio)}):
            result = _check_obs_preflight(fake_config)

        checks = {check[0]: (check[2], check[3], check[4]) for check in result}
        self.assertEqual(checks["obs"][0], "warning")
        self.assertIn("Check that OBS is open", checks["obs"][1])
        self.assertIn("Capture can still wait for OBS.", checks["obs"][1])
        self.assertEqual(checks["obs"][2], "connection refused")


class FolderPreflightTests(unittest.TestCase):
    def test_existing_writable_folder_is_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(
                _check_folder_preflight("Output folder", folder),
                ("ready", f"Folder is available: {Path(folder)}"),
            )

    def test_missing_output_folder_can_be_created_later(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / "not-created-yet"
            level, message = _check_folder_preflight("Output folder", folder, allow_create=True)
        self.assertEqual(level, "ready")
        self.assertIn("Folder will be created when needed", message)

    def test_missing_required_folder_needs_attention(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / "missing"
            level, message = _check_folder_preflight("OBS recording folder", folder)
        self.assertEqual(level, "warning")
        self.assertIn("isn't available", message)


class AudioDevicePreflightTests(unittest.TestCase):
    def test_prefers_configured_device(self):
        devices = [
            {"name": "Default mic", "maxInputChannels": 1},
            {"name": "Configured loopback", "maxInputChannels": 2},
        ]
        selected = _audio_device_for_preflight(devices, "Configured loopback", False, 0)
        self.assertEqual(selected["name"], "Configured loopback")

    def test_uses_loopback_fallback_pattern(self):
        devices = [
            {
                "name": "Headphones High Definition Audio Device Loopback",
                "maxInputChannels": 2,
            }
        ]
        selected = _audio_device_for_preflight(devices, "", False, 99)
        self.assertEqual(selected["name"], "Headphones High Definition Audio Device Loopback")


if __name__ == "__main__":
    unittest.main()
