"""Parsing and tolerant source-text matching for pronunciation corrections."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


_TRAILING_ANNOTATION = re.compile(r"\s*\([^()]*\)\s*$")
_CUSTOM_EMOJI_MARKUP = re.compile(
    r"<a?:[A-Za-z0-9_]+:[0-9]+>"
    r"|(?<!\w):[A-Za-z0-9_]+:(?:[0-9]+)?(?!\w)"
)
_WORD_TOKEN = re.compile(r"[^\W_]+(?:[\u2019'][^\W_]+)*", re.UNICODE)
_INLINE_MARKDOWN_MARKER = re.compile(r"[_\\~|*`]")

_MIN_FUZZY_SIMILARITY = 0.82
_MIN_FUZZY_MARGIN = 0.06
_MIN_SINGLE_WORD_FUZZY_LENGTH = 5
_MIN_PHRASE_FUZZY_LENGTH = 8


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
        if character in {",", "\uff0c"} and parenthesis_depth == 0:
            commit()
            continue
        buffer.append(character)

    commit()
    return items


def split_correction_annotation(value: str) -> tuple[str, str]:
    """Separate a trailing parenthetical comment from its correction text.

    The returned comment retains its leading whitespace. Fully parenthesized
    feedback has no base correction, while unbalanced or non-trailing
    parentheses remain part of the correction text.
    """
    parenthesis_depth = 0
    annotation_start: int | None = None
    for index, character in enumerate(value):
        if character == "(":
            if parenthesis_depth == 0:
                annotation_start = index
            parenthesis_depth += 1
        elif character == ")" and parenthesis_depth:
            parenthesis_depth -= 1
            if parenthesis_depth == 0 and value[index + 1 :].strip():
                annotation_start = None

    if annotation_start is None or parenthesis_depth:
        return value, ""

    correction = value[:annotation_start].rstrip()
    return correction, value[len(correction) :]


def correction_base_text(value: str) -> str:
    """Return the base used to compare corrections with optional comments."""
    correction, annotation = split_correction_annotation(value)
    return correction if correction and annotation else value


def correction_pattern(correction: str) -> str:
    """Build a case-insensitive-ready literal pattern with word boundaries."""

    normalized = " ".join(correction.split())
    if not normalized:
        return r"(?!)"
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
    """Return the exact source substring most likely meant by a correction.

    Exact matches always win.  When no exact target is present, a conservative
    fuzzy pass compares correction words with source n-grams containing the
    same number of words.  Weak or ambiguous matches deliberately return
    ``None`` so free-form feedback is not highlighted against the wrong text.
    """

    candidates = correction_candidates(correction, language=language)
    for candidate in candidates:
        match = re.search(correction_pattern(candidate), body, re.IGNORECASE)
        if match is not None:
            return match.group(0)

    source_tokens = list(_WORD_TOKEN.finditer(body))
    if not source_tokens:
        return None

    possible_matches: dict[str, tuple[float, str]] = {}
    for candidate in _fuzzy_candidates(candidates):
        candidate_words = _words(candidate)
        if not _is_fuzzy_eligible(candidate_words):
            continue

        word_count = len(candidate_words)
        candidate_key = " ".join(word.casefold() for word in candidate_words)
        for start in range(len(source_tokens) - word_count + 1):
            source_group = source_tokens[start : start + word_count]
            source_key = " ".join(
                token.group(0).casefold() for token in source_group
            )
            similarity = SequenceMatcher(
                None,
                candidate_key,
                source_key,
                autojunk=False,
            ).ratio()
            if not _is_at_most_one_edit_apart(candidate_key, source_key):
                continue
            if (
                similarity < _MIN_FUZZY_SIMILARITY
                and not _is_adjacent_transposition(candidate_key, source_key)
            ):
                continue

            source_text = body[source_group[0].start() : source_group[-1].end()]
            previous = possible_matches.get(source_key)
            if previous is None or similarity > previous[0]:
                possible_matches[source_key] = (similarity, source_text)

    ranked = sorted(
        possible_matches.values(),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < _MIN_FUZZY_MARGIN:
        return None
    return ranked[0][1]


def correction_candidates(correction: str, *, language: str) -> list[str]:
    """Return exact and annotation-free forms without interpreting emojis.

    Discord markdown is presentation metadata rather than part of the source
    word.  Strip it before matching so a correction such as ``mira__d__a``
    targets ``mirada`` while the submitted spelling remains available for the
    correction summary.
    """

    # Retained in the public API because callers already know the room language;
    # matching itself is deliberately language-independent.
    del language

    normalized = " ".join(correction.split())
    emoji_free = _remove_custom_emoji_markup(normalized)
    emoji_free = " ".join(_INLINE_MARKDOWN_MARKER.sub("", emoji_free).split())
    if not normalized or not _WORD_TOKEN.search(emoji_free):
        return []

    candidates: list[str] = []
    # Custom emoji markup is commentary, never a source-text candidate.
    pending = [emoji_free]
    while pending:
        candidate = " ".join(pending.pop(0).split())
        if not candidate or candidate in candidates:
            continue
        candidates.append(candidate)

        without_custom_emoji = _remove_custom_emoji_markup(candidate)
        if without_custom_emoji and without_custom_emoji not in candidates:
            pending.append(without_custom_emoji)

        if _is_fully_parenthesized(without_custom_emoji):
            inner = without_custom_emoji[1:-1].strip()
            # A lone word inside parentheses is a defensible source target.
            # A sentence is free-form feedback whose leading word may be
            # incidental, so leave it unmatched rather than guessing.
            inner_words = _words(inner)
            if len(inner_words) == 1 and inner_words[0] not in candidates:
                pending.append(inner_words[0])
        else:
            punctuation_free = _strip_edge_punctuation(without_custom_emoji)
            if (
                punctuation_free
                and _has_balanced_parentheses(punctuation_free)
                and punctuation_free not in candidates
            ):
                pending.append(punctuation_free)

        annotation = _TRAILING_ANNOTATION.search(candidate)
        if annotation is not None:
            annotation_free = candidate[: annotation.start()].strip()
            if annotation_free and annotation_free not in candidates:
                pending.append(annotation_free)

    return candidates


def _fuzzy_candidates(candidates: list[str]) -> list[str]:
    """Normalize candidates for similarity checks and remove annotation markup."""

    normalized: list[str] = []
    for candidate in candidates:
        if _is_fully_parenthesized(candidate) and len(_words(candidate)) > 1:
            continue
        cleaned = _strip_edge_punctuation(_remove_custom_emoji_markup(candidate))
        if (
            cleaned
            and _has_balanced_parentheses(cleaned)
            and cleaned not in normalized
        ):
            normalized.append(cleaned)
    return normalized


def _words(value: str) -> list[str]:
    return [match.group(0) for match in _WORD_TOKEN.finditer(value)]


def _is_fuzzy_eligible(words: list[str]) -> bool:
    if not words or not any(
        any(character.isalpha() for character in word) for word in words
    ):
        return False
    character_count = sum(len(word) for word in words)
    if len(words) == 1:
        return character_count >= _MIN_SINGLE_WORD_FUZZY_LENGTH
    return character_count >= _MIN_PHRASE_FUZZY_LENGTH


def _is_adjacent_transposition(left: str, right: str) -> bool:
    """Recognize one swapped letter pair without lowering the general threshold."""

    if len(left) != len(right) or len(left) < _MIN_SINGLE_WORD_FUZZY_LENGTH:
        return False
    differences = [
        index for index, (a, b) in enumerate(zip(left, right)) if a != b
    ]
    return (
        len(differences) == 2
        and differences[1] == differences[0] + 1
        and left[differences[0]] == right[differences[1]]
        and left[differences[1]] == right[differences[0]]
    )


def _is_at_most_one_edit_apart(left: str, right: str) -> bool:
    """Allow one insertion, deletion, substitution, or adjacent transposition."""

    if left == right:
        return True
    length_difference = len(left) - len(right)
    if abs(length_difference) > 1:
        return False
    if length_difference == 0:
        differences = sum(a != b for a, b in zip(left, right))
        return differences == 1 or _is_adjacent_transposition(left, right)

    shorter, longer = (left, right) if length_difference < 0 else (right, left)
    short_index = 0
    long_index = 0
    skipped = False
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        if skipped:
            return False
        skipped = True
        long_index += 1
    return True


def _is_fully_parenthesized(value: str) -> bool:
    value = value.strip()
    if len(value) < 2 or not value.startswith("(") or not value.endswith(")"):
        return False

    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _has_balanced_parentheses(value: str) -> bool:
    depth = 0
    for character in value:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _remove_custom_emoji_markup(value: str) -> str:
    return " ".join(_CUSTOM_EMOJI_MARKUP.sub(" ", value).split())


def _strip_edge_punctuation(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end].strip()
