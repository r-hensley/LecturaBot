from __future__ import annotations

import pytest

from lecturabot.corrections import find_correction_target, split_correction_items


def test_split_corrections_keeps_parenthetical_commas_in_one_item() -> None:
    raw_value = (
        "receiving, (stress, please :peepoPray:), moment\n"
        "(a whole sentence, explaining the pronunciation)"
    )

    assert split_correction_items(raw_value) == [
        "receiving",
        "(stress, please :peepoPray:)",
        "moment",
        "(a whole sentence, explaining the pronunciation)",
    ]


@pytest.mark.parametrize(
    "correction",
    [
        "(stress :peepoPray:)",
        "(stress <:peepoPray:922638020035883058>)",
        "(stress 🍎)",
    ],
)
def test_parenthesized_custom_emoji_annotation_finds_its_source_word(
    correction: str,
) -> None:
    assert (
        find_correction_target(
            "This situation can cause significant stress.",
            correction,
            language="en",
        )
        == "stress"
    )


def test_exact_annotation_match_returns_source_spelling_and_case() -> None:
    assert (
        find_correction_target(
            "The market has fresh Produce today.",
            "produce (noun)",
            language="en",
        )
        == "Produce"
    )


def test_markdown_formatted_letters_are_ignored_when_matching_source() -> None:
    body = (
        "Esperó creyendo en su mirada; murió decapitado. Lo acusó de ser "
        "despiadado con los indígenas, los conquistadores y las ciudades doradas."
    )
    raw_value = (
        "esper__ó__ (tilde), creyendo, mira__d__a (d), murió, "
        "decapita__d__o (**d**), acus__ó__ (tilde), "
        "despiada__d__o (d), ind__í__genas (tilde), "
        "conquista__d__ores (d), ciuda__d__es dora__d__as (d)"
    )

    assert [
        find_correction_target(body, item, language="es")
        for item in split_correction_items(raw_value)
    ] == [
        "Esperó",
        "creyendo",
        "mirada",
        "murió",
        "decapitado",
        "acusó",
        "despiadado",
        "indígenas",
        "conquistadores",
        "ciudades doradas",
    ]


def test_parenthesized_explanatory_sentence_is_not_guessed_from_first_word() -> None:
    assert (
        find_correction_target(
            "The stress made the sentence difficult.",
            "(stress should be on the first syllable :peepoPray:)",
            language="en",
        )
        is None
    )


@pytest.mark.parametrize(
    ("correction", "expected"),
    [
        ("recieving", "receiving"),
        ("momnet", "moment"),
        ("abotu", "about"),
        ("health insurence", "health insurance"),
    ],
)
def test_confident_typos_find_the_exact_source_substring(
    correction: str,
    expected: str,
) -> None:
    body = "She is receiving health insurance at the moment and thinking about it."

    assert find_correction_target(body, correction, language="en") == expected


@pytest.mark.parametrize(
    ("body", "correction"),
    [
        ("The caller stood taller.", "faller"),
        ("The cat sat nearby.", "cta"),
        ("She received a diagnosis.", "venga venga, tu puedes!"),
    ],
)
def test_ambiguous_short_or_unrelated_feedback_is_not_fuzzy_matched(
    body: str,
    correction: str,
) -> None:
    assert find_correction_target(body, correction, language="en") is None


def test_related_but_distinct_longer_word_is_not_treated_as_a_typo() -> None:
    assert (
        find_correction_target(
            "The movement was graceful.",
            "moment",
            language="en",
        )
        is None
    )


@pytest.mark.parametrize(
    "correction",
    [
        ":whatCat:",
        ":peepoPray:922638020035883058",
        "<:peepoPray:922638020035883058>",
        "<a:peepoPray:922638020035883058>",
        "🍎",
    ],
)
def test_emoji_only_feedback_has_no_source_target(correction: str) -> None:
    assert (
        find_correction_target(
            "The apple fell during a stressful moment.",
            correction,
            language="en",
        )
        is None
    )
