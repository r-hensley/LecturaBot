"""Parsing and tolerant source-text matching for pronunciation corrections."""

from __future__ import annotations

from collections.abc import Iterator
import re
import unicodedata

import emoji


_CUSTOM_EMOJI = re.compile(r"^<a?:([A-Za-z0-9_]+):[0-9]+>$")
_TRAILING_ANNOTATION = re.compile(r"\s*\([^()]*\)\s*$")

_COMMON_EMOJI_DESCRIPTORS = {
    "baby",
    "black",
    "blue",
    "brown",
    "button",
    "dark",
    "face",
    "facing",
    "front",
    "fruit",
    "green",
    "guide",
    "large",
    "light",
    "medium",
    "orange",
    "polar",
    "purple",
    "red",
    "service",
    "sign",
    "small",
    "symbol",
    "tone",
    "tropical",
    "water",
    "white",
    "yellow",
}
_SPANISH_EMOJI_DESCRIPTORS = {
    "amarilla",
    "amarillo",
    "azul",
    "blanca",
    "blanco",
    "botón",
    "cara",
    "de",
    "del",
    "frontal",
    "fruta",
    "guía",
    "marrón",
    "mediana",
    "mediano",
    "morada",
    "morado",
    "naranja",
    "negra",
    "negro",
    "polar",
    "pequeña",
    "pequeño",
    "roja",
    "rojo",
    "símbolo",
    "tono",
    "tropical",
    "verde",
}


def split_correction_items(raw_value: str) -> list[str]:
    """Split corrections on newlines or top-level commas.

    Commas inside parentheses are retained so annotations such as
    ``produce (noun, uncountable)`` remain one correction.
    """

    items: list[str] = []
    buffer: list[str] = []
    parenthesis_depth = 0

    def commit() -> None:
        item = " ".join("".join(buffer).split())
        if item:
            items.append(item)
        buffer.clear()

    for character in raw_value:
        if character in "\r\n":
            commit()
            parenthesis_depth = 0
            continue
        if character == "(":
            parenthesis_depth += 1
            buffer.append(character)
            continue
        if character == ")":
            parenthesis_depth = max(0, parenthesis_depth - 1)
            buffer.append(character)
            continue
        if character in {",", "，"} and parenthesis_depth == 0:
            commit()
            continue
        buffer.append(character)

    commit()
    return items


def correction_pattern(correction: str) -> str:
    """Build a case-insensitive-ready literal pattern with word boundaries."""

    normalized = " ".join(correction.split())
    pattern = r"\s+".join(re.escape(part) for part in normalized.split())
    if normalized[0].isalnum():
        pattern = rf"(?<!\w){pattern}"
    if normalized[-1].isalnum():
        pattern = rf"{pattern}(?!\w)"
    return pattern


def find_correction_target(
    body: str,
    correction: str,
    *,
    language: str,
) -> str | None:
    """Return the source-text target represented by a displayed correction."""

    for candidate in correction_candidates(correction, language=language):
        if re.search(correction_pattern(candidate), body, re.IGNORECASE) is not None:
            return candidate
    return None


def correction_candidates(correction: str, *, language: str) -> list[str]:
    """Return exact, annotation-free, punctuation-free, and emoji aliases."""

    normalized = " ".join(correction.split())
    if not normalized:
        return []

    candidates: list[str] = []
    pending = [normalized]
    while pending:
        candidate = pending.pop(0)
        candidate = " ".join(candidate.split())
        if not candidate or candidate in candidates:
            continue
        candidates.append(candidate)

        punctuation_free = _strip_edge_punctuation(candidate)
        if punctuation_free and punctuation_free not in candidates:
            pending.append(punctuation_free)

        annotation = _TRAILING_ANNOTATION.search(candidate)
        if annotation is not None:
            annotation_free = candidate[: annotation.start()].strip()
            if annotation_free and annotation_free not in candidates:
                pending.append(annotation_free)

    for candidate in tuple(candidates):
        for alias in _emoji_aliases(candidate, language=language):
            if alias and alias not in candidates:
                candidates.append(alias)
    return candidates


def _strip_edge_punctuation(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end].strip()


def _emoji_aliases(value: str, *, language: str) -> Iterator[str]:
    custom_match = _CUSTOM_EMOJI.fullmatch(value)
    if custom_match is not None:
        yield from _emoji_phrase_candidates(custom_match.group(1), language)
        return
    if not emoji.is_emoji(value):
        return

    names: list[tuple[str, str]] = []
    for code in dict.fromkeys((language, "en")):
        demojized = emoji.demojize(value, language=code)
        if (
            demojized != value
            and demojized.startswith(":")
            and demojized.endswith(":")
        ):
            names.append((demojized[1:-1], code))

    data = emoji.EMOJI_DATA.get(value, {})
    for alias in data.get("alias", []):
        names.append((str(alias).strip(":"), "en"))

    yielded: set[str] = set()
    for name, code in names:
        for candidate in _emoji_phrase_candidates(name, code):
            if candidate not in yielded:
                yielded.add(candidate)
                yield candidate


def _emoji_phrase_candidates(name: str, language: str) -> Iterator[str]:
    phrase = " ".join(name.replace("_", " ").replace("-", " ").split())
    if not phrase:
        return
    yield phrase

    stopwords = set(_COMMON_EMOJI_DESCRIPTORS)
    if language == "es":
        stopwords.update(_SPANISH_EMOJI_DESCRIPTORS)
    words = [word for word in phrase.split() if word.casefold() not in stopwords]
    filtered = " ".join(words)
    if filtered and filtered.casefold() != phrase.casefold():
        yield filtered
    if len(words) > 1:
        head = words[0] if language == "es" else words[-1]
        if head.casefold() not in stopwords:
            yield head
