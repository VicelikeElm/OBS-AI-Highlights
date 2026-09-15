# -*- coding: utf-8 -*-
"""Deterministic, local "ready to post" metadata for a finished clip -
suggested title, short description, hashtags, and the strongest
transcript quote. No cloud AI, no LLM calls: the "quote" is just the
sentence in the clip's own verified transcript that matches the most
phrases from the active preset's own phrase lists (the same phrase
lists driving detection in the first place), and everything else is
built from that quote with plain text rules.
"""

import re

SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")

HASHTAG_PATTERN = re.compile(r"[^a-z0-9]+")


def split_sentences(text):
    text = (text or "").strip()

    if not text:
        return []

    return [s.strip() for s in SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _score_sentence(sentence, phrase_lists):
    lower = sentence.lower()
    return sum(1 for phrases in phrase_lists for phrase in phrases if phrase in lower)


def extract_quote(text, phrase_lists):
    """The sentence most representative of why this clip was worth
    saving - the one matching the most preset phrases. Ties break
    toward the longer sentence (more context). Falls back to the whole
    text if it doesn't look like separate sentences, so this never
    returns nothing for a non-empty transcript."""
    sentences = split_sentences(text)

    if not sentences:
        return (text or "").strip()

    scored = [(_score_sentence(s, phrase_lists), len(s), s) for s in sentences]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    return scored[0][2]


def build_title(quote, max_length=70):
    title = quote.strip().strip(".!?").strip()

    title = " ".join(
        word.capitalize() if word.islower() else word
        for word in title.split()
    )

    if len(title) > max_length:
        truncated = title[:max_length].rsplit(" ", 1)[0]
        title = (truncated or title[:max_length]).rstrip(",;:") + "..."

    return title


def build_description(text, max_length=200):
    text = (text or "").strip()

    if len(text) <= max_length:
        return text

    truncated = text[:max_length].rsplit(" ", 1)[0]

    return (truncated or text[:max_length]).rstrip(",;:") + "..."


def slugify_hashtag(phrase):
    return HASHTAG_PATTERN.sub("", phrase.lower())


def build_hashtags(preset_hashtags, matched_phrases, limit=8):
    tags = []
    seen = set()

    for source in (preset_hashtags or []), (matched_phrases or []):
        for phrase in source:
            slug = slugify_hashtag(phrase)

            if slug and 2 <= len(slug) <= 24 and slug not in seen:
                seen.add(slug)
                tags.append(slug)

            if len(tags) >= limit:
                break

        if len(tags) >= limit:
            break

    return [f"#{tag}" for tag in tags]


def build_metadata(text, phrase_lists, preset_hashtags):
    """phrase_lists is an iterable of phrase lists (e.g. [STRONG_PHRASES,
    APPLICATION_PHRASES, SCRIPTURE_PHRASES]) to search for the quote and
    to derive phrase-based hashtags from."""
    quote = extract_quote(text, phrase_lists)

    quote_lower = quote.lower()
    matched_in_quote = [
        phrase
        for phrases in phrase_lists
        for phrase in phrases
        if phrase in quote_lower
    ]

    return {
        "title": build_title(quote),
        "description": build_description(text),
        "quote": quote,
        "hashtags": build_hashtags(preset_hashtags, matched_in_quote),
    }
