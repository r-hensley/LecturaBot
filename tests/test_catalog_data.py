from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import warnings

from lecturabot.bot import CATALOG_RESOURCE
from lecturabot.models import ActiveReading, Language, Level
from lecturabot.rendering import build_reading_content
from lecturabot.repository import SQLiteRepository
from scripts.build_catalog import (
    DEFAULT_LEGACY_SOURCE,
    DEFAULT_OUTPUT,
    DEFAULT_REPORT,
    EXPECTED_ACTIVE_COUNTS,
    EXPECTED_EMOTION_COUNT,
    EXPECTED_HELD_COUNTS,
    MAX_BODY_LENGTH,
    build_catalog,
    render_catalog,
    render_report,
)


def _body_key(body: object) -> str:
    return "".join(character for character in str(body).casefold() if character.isalnum())


def test_merged_catalog_matches_both_vendored_source_snapshots() -> None:
    result = build_catalog()

    assert len(result.records) == sum(EXPECTED_ACTIVE_COUNTS.values()) == 1_936
    assert DEFAULT_OUTPUT.read_text(encoding="utf-8") == render_catalog(
        result.records
    )
    assert DEFAULT_REPORT.read_text(encoding="utf-8") == render_report(
        result.report
    )
    assert result.report["legacy_reconciliation"] == {
        "readings": 1_014,
        "equivalent": 820,
        "reviewed_variants": 50,
        "compound_parts": 7,
        "fuller_legacy_replacements": 4,
        "legacy_only": 133,
        "historically_retired": 13,
    }


def test_catalog_counts_provenance_and_discord_render_limits() -> None:
    records: list[dict[str, object]] = json.loads(
        DEFAULT_OUTPUT.read_text(encoding="utf-8")
    )
    actual_counts = Counter(
        (str(item["language"]), str(item["level"])) for item in records
    )

    assert actual_counts == Counter(EXPECTED_ACTIVE_COUNTS)
    assert not {str(item["language"]) for item in records} & {"fr", "pt"}
    assert len({_body_key(item["body"]) for item in records}) == len(records)
    assert sum("expected_emotion" in item for item in records) == (
        EXPECTED_EMOTION_COUNT
    )
    assert max(len(str(item["body"])) for item in records) <= MAX_BODY_LENGTH
    assert all(item.get("source_kind") and item.get("source_ref") for item in records)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for item in records:
            reading = ActiveReading(
                reader_id=1,
                reader_display_name="Catalog verification reader",
                language=Language(str(item["language"])),
                level=Level(str(item["level"])),
                body=str(item["body"]),
                started_at=1,
                expected_emotion=(
                    None
                    if item.get("expected_emotion") is None
                    else str(item["expected_emotion"])
                ),
            )
            assert len(build_reading_content(reading)) <= 2_000


def test_source_inventory_preserves_held_french_and_portuguese() -> None:
    report = json.loads(DEFAULT_REPORT.read_text(encoding="utf-8"))
    held = {
        (item["language"], item["level"]): item["count"]
        for item in report["spreadsheet"]["held_counts"]
    }

    assert held == EXPECTED_HELD_COUNTS
    assert report["spreadsheet"]["held_language_rows"] == 272


async def test_runtime_sync_reconciles_an_existing_legacy_catalog(
    tmp_path: Path,
) -> None:
    assert CATALOG_RESOURCE == "data/catalog.json"
    assert len(SQLiteRepository._read_seed_records(DEFAULT_OUTPUT)) == 1_936

    repository = SQLiteRepository(tmp_path / "catalog.sqlite3")
    await repository.initialize()
    assert await repository.seed_texts(DEFAULT_LEGACY_SOURCE) == 1_014

    first = await repository.sync_texts(DEFAULT_OUTPUT)
    assert (
        first.inserted,
        first.reenabled,
        first.updated,
        first.disabled,
    ) == (989, 0, 0, 67)
    second = await repository.sync_texts(DEFAULT_OUTPUT)
    assert (second.inserted, second.reenabled, second.updated, second.disabled) == (
        0,
        0,
        0,
        0,
    )

    active_count = sum(
        [
            len(await repository.list_texts(language=language, level=level))
            for language in Language
            for level in Level
        ]
    )
    assert active_count == 1_936
