"""Track ordinary OBS recordings and attach recording-relative game markers."""

import time
from datetime import datetime

import session_stats


class RecordingTimeline:
    def __init__(self, obs_client=None, embed_chapters=False):
        self.session_id = None
        self._duration_at_observation = 0.0
        self._observed_at = None
        self._wall_observed_at = None
        self._recording_file = ""
        self.obs_client = obs_client
        self.embed_chapters = bool(embed_chapters)
        self.last_chapter_error = None

    def update(self, status, game_name="", profile_name="", now=None):
        """Start/end a timeline at OBS recording transitions."""
        now = time.monotonic() if now is None else float(now)
        recording = bool(status.get("recording"))
        if recording and self.session_id is None:
            try:
                duration = max(0.0, float(status.get("record_duration_seconds") or 0.0))
            except (TypeError, ValueError):
                duration = 0.0
            self._duration_at_observation = duration
            self._observed_at = now
            self._wall_observed_at = time.time()
            self._recording_file = str(status.get("recording_file", "") or "")
            self.session_id = session_stats.start_recording_timeline(
                game_name,
                profile_name,
                self._recording_file,
                self.embed_chapters,
            )
        elif recording and self.session_id is not None:
            duration_seconds = status.get("record_duration_seconds")
            if duration_seconds is not None:
                try:
                    self._duration_at_observation = max(0.0, float(duration_seconds))
                    self._observed_at = now
                    self._wall_observed_at = time.time()
                except (TypeError, ValueError):
                    pass
            self._recording_file = str(
                status.get("recording_file", "") or self._recording_file
            )
            session_stats.update_recording_timeline(
                self.session_id,
                self._recording_file,
                game_name,
                profile_name,
                self.elapsed_seconds(now),
            )
        elif not recording and self.session_id is not None:
            return self.close()
        return None

    def elapsed_seconds(self, now=None):
        if self.session_id is None or self._observed_at is None:
            return None
        now = time.monotonic() if now is None else float(now)
        return self._duration_at_observation + max(0.0, now - self._observed_at)

    def add_marker(
        self,
        event_type,
        label,
        game_name="",
        profile_name="",
        now=None,
        event_timestamp=None,
    ):
        elapsed = self.elapsed_seconds(now)
        if elapsed is None:
            return False
        if event_timestamp and self._wall_observed_at is not None:
            try:
                event_wall_time = datetime.fromisoformat(event_timestamp).timestamp()
                elapsed = max(
                    0.0,
                    self._duration_at_observation
                    + event_wall_time
                    - self._wall_observed_at,
                )
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        marker_type = str(event_type or "MANUAL").upper()
        marker_label = str(label or "Marker")
        recorded = session_stats.record_timeline_marker(
            self.session_id,
            elapsed,
            marker_type,
            marker_label,
            game_name,
            profile_name,
        )
        self.last_chapter_error = None
        if recorded and self.embed_chapters and self.obs_client is not None:
            try:
                self.obs_client.create_record_chapter(
                    chapter_name=f"{marker_type}: {marker_label}"[:120]
                )
                session_stats.record_native_chapter(self.session_id)
            except Exception as error:
                self.last_chapter_error = str(error)
                session_stats.record_native_chapter_failure(
                    self.session_id,
                    self.last_chapter_error,
                )
        return recorded

    def close(self):
        if self.session_id is None:
            return None
        session_id = self.session_id
        session_stats.update_recording_timeline(
            session_id,
            self._recording_file,
            recording_duration_seconds=self.elapsed_seconds(),
        )
        session_stats.end_session(session_id)
        record = session_stats.get_session(session_id)
        if self.embed_chapters and not record.get("timeline_markers"):
            session_stats.set_chapter_embedding_status(
                session_id,
                "no_markers",
                "No native chapters were added during this recording.",
            )
        self.session_id = None
        self._observed_at = None
        self._wall_observed_at = None
        self._duration_at_observation = 0.0
        self._recording_file = ""
        return None
