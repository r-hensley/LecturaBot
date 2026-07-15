from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import warnings

from lecturabot.bot import CATALOG_RETIREMENT_RESOURCES, CATALOG_SEED_RESOURCES
from lecturabot.models import ActiveReading, Language, Level
from lecturabot.rendering import build_reading_content
from lecturabot.repository import SQLiteRepository
from scripts.build_google_doc_catalog import (
    CATALOG_EXCLUSIONS,
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    EXPECTED_SOURCE_COUNTS,
    SOURCE_CATEGORY_TO_CATALOG,
    build_catalog,
    render_catalog,
)


def test_google_doc_catalog_matches_vendored_source_snapshot() -> None:
    records = build_catalog(DEFAULT_SOURCE.read_text(encoding="utf-8-sig"))

    assert len(records) == sum(EXPECTED_SOURCE_COUNTS.values()) - len(
        CATALOG_EXCLUSIONS
    ) == 1_014
    assert DEFAULT_OUTPUT.read_text(encoding="utf-8") == render_catalog(records)


def test_google_doc_catalog_mapping_and_discord_render_limits() -> None:
    records: list[dict[str, object]] = json.loads(
        DEFAULT_OUTPUT.read_text(encoding="utf-8")
    )
    expected_counts = Counter(
        {
            (language, level, source_category): count
            for source, count in EXPECTED_SOURCE_COUNTS.items()
            for language, level, source_category in [
                SOURCE_CATEGORY_TO_CATALOG[source]
            ]
        }
    )
    actual_counts = Counter(
        (item["language"], item["level"], item["source_category"])
        for item in records
    )
    expected_counts[("en", "advanced", "super_hard")] -= len(CATALOG_EXCLUSIONS)

    assert actual_counts == expected_counts
    assert len({" ".join(str(item["body"]).split()).casefold() for item in records}) == len(
        records
    )

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
            )
            assert len(build_reading_content(reading)) <= 2_000


def test_runtime_catalog_keeps_original_seed_separate() -> None:
    original_path = Path("src/lecturabot/data/readings.json")
    original_records = json.loads(original_path.read_text(encoding="utf-8"))
    google_records = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    assert len(original_records) == 12
    assert not {
        " ".join(str(item["body"]).split()).casefold() for item in original_records
    } & {" ".join(str(item["body"]).split()).casefold() for item in google_records}


async def test_runtime_seeds_both_packaged_catalogs(tmp_path: Path) -> None:
    assert CATALOG_SEED_RESOURCES == (
        "data/readings.json",
        "data/google_doc_readings.json",
    )
    assert CATALOG_RETIREMENT_RESOURCES == ("data/retired_readings.json",)
    assert len(SQLiteRepository._read_seed_records(DEFAULT_OUTPUT)) == 1_014
    assert len(
        SQLiteRepository._read_seed_records(
            Path("src/lecturabot/data/retired_readings.json")
        )
    ) == 1

    repository = SQLiteRepository(tmp_path / "catalog.sqlite3")
    await repository.initialize()
    seed_paths = [Path("src/lecturabot") / name for name in CATALOG_SEED_RESOURCES]

    assert sum([await repository.seed_texts(path) for path in seed_paths]) == 1_026
    assert sum([await repository.seed_texts(path) for path in seed_paths]) == 0
