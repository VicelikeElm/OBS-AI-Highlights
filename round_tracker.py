# -*- coding: utf-8 -*-
"""Accumulate game events into round scores and explainable achievements."""

from copy import deepcopy


EVENT_POINTS = {
    "KILL": 30,
    "DEATH": -15,
    "HEADSHOT": 20,
    "PLANT": 25,
    "DEFUSE": 30,
    "ROUND_WIN": 40,
    "CLUTCH": 35,
    "FLAWLESS_ROUND": 50,
    "MATCH_POINT": 0,
    "OVERTIME": 0,
}

ACHIEVEMENT_POINTS = {
    "Double Kill": 25,
    "Triple Kill": 100,
    "Quad Kill": 125,
    "Ace": 150,
    "Plant and Win": 20,
    "Defuse and Win": 25,
    "Round Victory": 0,
    "Clutch": 50,
    "Flawless Round": 50,
    "Match Point Victory": 40,
    "Overtime Victory": 40,
}


class RoundTracker:
    def __init__(self, game_name, profile_name):
        self.game_name = game_name
        self.profile_name = profile_name
        self.round_number = 1
        self.current_round = self._new_round()
        self._last_timestamp = None

    def _new_round(self):
        return {
            "round": self.round_number,
            "kills": 0,
            "deaths": 0,
            "headshots": 0,
            "plants": 0,
            "defuses": 0,
            "won": False,
            "score": 0,
            "events": [],
            "achievements": [],
            "closed": False,
            "match_point": False,
            "overtime": False,
        }

    def _award(self, achievement):
        if achievement in self.current_round["achievements"]:
            return
        self.current_round["achievements"].append(achievement)
        self.current_round["score"] += ACHIEVEMENT_POINTS[achievement]

    def apply_event(self, event):
        """Record one event and return a snapshot of its round state."""
        event_type = str(event.get("type", "")).upper()
        if event_type not in EVENT_POINTS:
            return None

        if self.current_round["closed"]:
            self.round_number += 1
            self.current_round = self._new_round()

        state = self.current_round
        state["events"].append({
            "type": event_type,
            "text": str(event.get("text", "")),
            "timestamp": event.get("timestamp"),
            "points": EVENT_POINTS[event_type],
        })
        state["score"] += EVENT_POINTS[event_type]
        self._last_timestamp = event.get("timestamp")

        if event_type == "KILL":
            state["kills"] += 1
            if state["kills"] == 2:
                self._award("Double Kill")
            elif state["kills"] == 3:
                self._award("Triple Kill")
            elif state["kills"] == 4:
                self._award("Quad Kill")
            elif state["kills"] >= 5:
                self._award("Ace")
        elif event_type == "DEATH":
            state["deaths"] += 1
        elif event_type == "HEADSHOT":
            state["headshots"] += 1
        elif event_type == "PLANT":
            state["plants"] += 1
        elif event_type == "DEFUSE":
            state["defuses"] += 1
        elif event_type == "MATCH_POINT":
            state["match_point"] = True
        elif event_type == "OVERTIME":
            state["overtime"] = True
        elif event_type == "CLUTCH":
            self._award("Clutch")
        elif event_type == "FLAWLESS_ROUND":
            self._award("Flawless Round")
        elif event_type == "ROUND_WIN":
            state["won"] = True
            self._award("Round Victory")
            if state["plants"]:
                self._award("Plant and Win")
            if state["defuses"]:
                self._award("Defuse and Win")
            if state["match_point"]:
                self._award("Match Point Victory")
            if state["overtime"]:
                self._award("Overtime Victory")
            state["closed"] = True

        snapshot = deepcopy(state)
        snapshot["game"] = self.game_name
        snapshot["profile"] = self.profile_name
        return snapshot

    @staticmethod
    def should_trigger(snapshot, threshold=70):
        """Only trigger on meaningful game events once the round score qualifies."""
        if snapshot is None or snapshot["score"] < threshold:
            return False
        if snapshot["closed"]:
            return True
        return snapshot["events"][-1]["type"] in {
            "KILL", "HEADSHOT", "PLANT", "DEFUSE", "CLUTCH", "FLAWLESS_ROUND"
        }

    @staticmethod
    def combine_score(content_score, game_context, max_score=300):
        """Combine the existing 0-100 transcript/audio score with game context."""
        game_score = min(max(0, int((game_context or {}).get("score", 0))), max_score)
        total = min(int(content_score) + game_score, max_score)
        reasons = []
        if game_score:
            reasons.append(f"game events (+{game_score})")
        reasons.extend(
            str(achievement)
            for achievement in (game_context or {}).get("achievements", [])
        )
        return total, reasons
