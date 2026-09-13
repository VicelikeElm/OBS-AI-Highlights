# -*- coding: utf-8 -*-
"""Built-in presets for what counts as a "highlight-worthy" moment.

Each preset supplies the same set of fields the scoring engine
(highlight_engine.py) and the verification pass (verify_clips.py) read:

  strong_phrases       - phrases that strongly indicate a great moment
  application_phrases  - phrases indicating a call-to-action / takeaway
  reference_phrases    - domain-reference phrases (Scripture, game terms,
                          stream terms - whatever "citing something
                          specific" looks like for this content type)
  low_value_phrases    - phrases that indicate administrative/low-value
                          content (penalizes the score)
  pause_phrases        - phrases that suspend clip candidacy entirely
                          until pause_end_pattern matches (generalizes
                          the original prayer-detection mechanism - e.g.
                          "the streamer said they're stepping away")
  pause_end_pattern    - a regex that resumes clipping after a pause
  whisper_initial_prompt - vocabulary hint text fed to Whisper to bias
                          transcription accuracy for this domain

The "church" preset is copied byte-for-byte from the original
sermon_ai.py / process_shorts.py content (STRONG_PHRASES,
APPLICATION_PHRASES, SCRIPTURE_PHRASES, ADMIN_PHRASES,
PRAYER_START_PHRASES, WHISPER_INITIAL_PROMPT) - this is Corey's actual
tuned production behavior and must not silently drift from it.

"gaming" and "twitch" are new placeholder presets - reasonable starting
points, not tuned against real transcripts yet.

"custom" ships empty; the settings UI lets a user fill in their own
values, persisted in highlight_config.json under "custom_preset".
"""

DEFAULT_PRESET = "church"

PRESETS = {
    "church": {
        "label": "Church / Sermon",
        "strong_phrases": [
            "the point is",
            "here's the point",
            "here is the point",
            "listen to this",
            "listen carefully",
            "understand this",
            "don't miss this",
            "do not miss this",
            "remember this",
            "we need to understand",
            "what this means",
            "this means",
            "the truth is",
            "the reality is",
            "the gospel",
            "god's grace",
            "god's mercy",
            "god's word",
            "the word of god",
            "scripture tells us",
            "the bible tells us",
            "christian life",
            "as christians",
            "we are called",
            "you are called",
            "we must",
            "you must",
            "we cannot",
            "you cannot",
            "the question is",
            "ask yourself",
            "think about this",
            "what will you do",
            "what are you going to do",
        ],
        "application_phrases": [
            "in your life",
            "in our lives",
            "for us today",
            "for you today",
            "this week",
            "when you leave here",
            "when we leave here",
            "how do we",
            "how should we",
            "what should we",
            "we ought to",
            "we should",
            "you should",
            "we need to",
            "you need to",
            "apply this",
            "application",
        ],
        "reference_phrases": [
            "the text says",
            "the passage says",
            "scripture says",
            "the bible says",
            "verse",
            "chapter",
            "matthew",
            "mark",
            "luke",
            "john",
            "acts",
            "romans",
            "corinthians",
            "galatians",
            "ephesians",
            "philippians",
            "colossians",
            "thessalonians",
            "timothy",
            "titus",
            "hebrews",
            "james",
            "peter",
            "jude",
            "revelation",
            "psalm",
            "proverbs",
            "genesis",
            "exodus",
            "isaiah",
            "jeremiah",
        ],
        "low_value_phrases": [
            "good morning",
            "welcome",
            "announcement",
            "announcements",
            "bulletin",
            "offering",
            "silence your phone",
            "turn your phone",
            "let's stand",
            "let us stand",
        ],
        "pause_phrases": [
            "let us pray",
            "let's pray",
            "lets pray",
            "bow your heads",
            "bow our heads",
            "go to prayer",
            "go to the lord in prayer",
        ],
        "pause_end_pattern": r"\bamen\b",
        "whisper_initial_prompt": """
Christian sermon and Bible teaching.

Common biblical and theological terminology includes:

Jesus Christ,
God,
Holy Spirit,
Scripture,
Gospel,
grace,
mercy,
faith,
repentance,
righteousness,
justification,
sanctification,
salvation,
redemption,
resurrection,
disciples,
Pharisees,
apostles,
covenant,
kingdom of God,
Son of Man,
Son of God.

Bible books include:

Genesis,
Exodus,
Leviticus,
Numbers,
Deuteronomy,
Joshua,
Judges,
Ruth,
Samuel,
Kings,
Chronicles,
Ezra,
Nehemiah,
Esther,
Job,
Psalms,
Proverbs,
Ecclesiastes,
Isaiah,
Jeremiah,
Lamentations,
Ezekiel,
Daniel,
Hosea,
Joel,
Amos,
Obadiah,
Jonah,
Micah,
Nahum,
Habakkuk,
Zephaniah,
Haggai,
Zechariah,
Malachi,
Matthew,
Mark,
Luke,
John,
Acts,
Romans,
Corinthians,
Galatians,
Ephesians,
Philippians,
Colossians,
Thessalonians,
Timothy,
Titus,
Philemon,
Hebrews,
James,
Peter,
Jude,
Revelation.
""",
    },
    "gaming": {
        "label": "Gaming",
        "strong_phrases": [
            "let's go",
            "lets go",
            "no way",
            "are you kidding me",
            "that's insane",
            "thats insane",
            "unbelievable",
            "did you see that",
            "oh my god",
            "what a play",
            "clean",
            "clutch",
            "ez clap",
            "no shot",
            "that was crazy",
            "i can't believe that",
            "i cant believe that",
        ],
        "application_phrases": [
            "chat did you see that",
            "let me know",
            "drop a",
            "in the chat",
            "that's crazy right",
            "thats crazy right",
            "am i wrong",
            "tell me that wasn't",
        ],
        "reference_phrases": [
            "boss fight",
            "final round",
            "clutch play",
            "game winning",
            "last hit",
            "headshot",
            "ace",
            "pentakill",
            "victory royale",
            "checkpoint",
            "speedrun",
            "new personal best",
        ],
        "low_value_phrases": [
            "loading screen",
            "let me check my settings",
            "one second",
            "give me a minute",
            "afk",
            "patch notes",
            "menu screen",
        ],
        "pause_phrases": [
            "brb",
            "hold on",
            "give me a second",
            "one sec",
            "bathroom break",
            "be right back",
        ],
        "pause_end_pattern": r"\b(i'?m back|back now|okay back|alright back|and we'?re back)\b",
        "whisper_initial_prompt": """
Video game livestream commentary.

Common gaming terminology includes:

clutch, ace, headshot, pentakill, victory royale, respawn, cooldown,
ultimate, nerf, buff, meta, speedrun, checkpoint, boss fight, loot,
inventory, spawn, teamfight, gank, rotate, farm, gg, ez, no scope,
one shot, wallbang, frag, kill streak, comeback, clutch play.
""",
    },
    "twitch": {
        "label": "Twitch / Streaming",
        "strong_phrases": [
            "thanks for the sub",
            "thanks for the follow",
            "welcome raiders",
            "let's raid",
            "new record",
            "first time",
            "chat went crazy",
            "that's a new sub",
            "thats a new sub",
            "shoutout to",
            "appreciate the support",
        ],
        "application_phrases": [
            "smash that follow",
            "hit the subscribe",
            "drop a follow",
            "let me know in chat",
            "what do you guys think",
            "what do you all think",
        ],
        "reference_phrases": [
            "donation",
            "bit train",
            "raid",
            "hype train",
            "sub goal",
            "follower goal",
            "clip that",
            "mod",
            "vip",
        ],
        "low_value_phrases": [
            "technical difficulties",
            "let me restart",
            "one moment please",
            "stream lag",
            "buffering",
            "brb stream",
        ],
        "pause_phrases": [
            "brb",
            "give me a second",
            "hold on chat",
            "one moment",
            "stepping away",
        ],
        "pause_end_pattern": r"\b(i'?m back|back now|okay we'?re back|alright we'?re back)\b",
        "whisper_initial_prompt": """
Live streaming commentary and viewer interaction.

Common streaming terminology includes:

subscribe, follow, raid, host, hype train, bit train, donation, mod,
VIP, emote, clip, chat, stream, viewers, subscriber, follower,
shoutout, giveaway.
""",
    },
    "custom": {
        "label": "Custom",
        "strong_phrases": [],
        "application_phrases": [],
        "reference_phrases": [],
        "low_value_phrases": [],
        "pause_phrases": [],
        "pause_end_pattern": "",
        "whisper_initial_prompt": "",
    },
}


def get_preset(name, custom_override=None):
    """Return the preset dict for `name`.

    If name == "custom" and custom_override is given (e.g. loaded from
    highlight_config.json), it's merged over the empty custom template.
    Unknown names fall back to DEFAULT_PRESET rather than raising, since
    this is read on every startup and a bad/missing config value
    shouldn't crash the engine.
    """
    if name == "custom":
        merged = dict(PRESETS["custom"])
        if custom_override:
            merged.update(custom_override)
        return merged

    return PRESETS.get(name, PRESETS[DEFAULT_PRESET])
