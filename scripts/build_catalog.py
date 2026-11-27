"""Build LecturaBot's active catalog from the reconciled source snapshots.

The expanded spreadsheet is the preferred editorial source.  The structured
legacy snapshot supplies readings that are absent from the spreadsheet and the
few reviewed cases where its passage is fuller or demonstrably more correct.
French and Portuguese remain visible in the source report but are held until
the bot has a complete runtime/UI policy for those languages.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, replace
import difflib
import hashlib
import json
from pathlib import Path
import re
from typing import Final
import unicodedata


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_SPREADSHEET_SOURCE: Final = (
    ROOT / "sources" / "expanded_spreadsheet_readings.json"
)
DEFAULT_LEGACY_SOURCE: Final = (
    ROOT / "sources" / "legacy_catalog_2026-07-11.json"
)
DEFAULT_RETIRED_SOURCE: Final = (
    ROOT / "sources" / "legacy_retired_readings_2026-07-20.json"
)
DEFAULT_OUTPUT: Final = ROOT / "src" / "lecturabot" / "data" / "catalog.json"
DEFAULT_REPORT: Final = ROOT / "sources" / "catalog_build_report.json"

ACTIVE_LANGUAGES: Final = {"en", "es"}
MAX_BODY_LENGTH: Final = 1_850
EXPECTED_SPREADSHEET_COUNTS: Final = {
    "ENGLISH BEGINNER": 319,
    "ENGLISH INTERMEDIATE": 387,
    "ENGLISH ADVANCED": 311,
    "SPANISH BEGINNER": 174,
    "SPANISH INTERMEDIATE": 422,
    "SPANISH ADVANCED": 198,
    "FRENCH BEGINNER": 16,
    "FRENCH INTERMEDIATE": 17,
    "FRENCH ADVANCED": 4,
    "PORTUGUESE": 235,
}
EXPECTED_ACTIVE_COUNTS: Final = {
    ("en", "beginner"): 322,
    ("en", "intermediate"): 396,
    ("en", "advanced"): 367,
    ("es", "beginner"): 179,
    ("es", "intermediate"): 423,
    ("es", "advanced"): 249,
}
EXPECTED_HELD_COUNTS: Final = {
    ("fr", "beginner"): 16,
    ("fr", "intermediate"): 17,
    ("fr", "advanced"): 4,
    ("pt", None): 235,
}
EXPECTED_EMOTION_COUNT: Final = 301
EXPECTED_EXACT_LEGACY_MATCHES: Final = 820

# These are the 50 one-to-one variants found by the reviewed comparison.  The
# spreadsheet wins by default, per the catalog policy.  The small exceptions
# below are not equivalent variants: one side contains a known error or the
# final wording deliberately combines corrections from both sources.
NEAR_VARIANT_PAIRS: Final = {
    93: "ENGLISH BEGINNER!A113",
    130: "ENGLISH BEGINNER!A135",
    170: "ENGLISH BEGINNER!A160",
    176: "ENGLISH BEGINNER!A163",
    343: "ENGLISH BEGINNER!A265",
    414: "ENGLISH BEGINNER!A308",
    421: "ENGLISH BEGINNER!A312",
    512: "ENGLISH INTERMEDIATE!A52",
    527: "ENGLISH INTERMEDIATE!A59",
    553: "ENGLISH INTERMEDIATE!A75",
    574: "ENGLISH INTERMEDIATE!A87",
    618: "ENGLISH INTERMEDIATE!A115",
    704: "ENGLISH INTERMEDIATE!A165",
    722: "ENGLISH INTERMEDIATE!A177",
    864: "ENGLISH ADVANCED!A22",
    959: "ENGLISH ADVANCED!A76",
    960: "ENGLISH ADVANCED!A77",
    1019: "ENGLISH ADVANCED!A108",
    1614: "SPANISH BEGINNER!A105",
    1615: "SPANISH BEGINNER!A106",
    1617: "SPANISH BEGINNER!A108",
    1618: "SPANISH BEGINNER!A109",
    1634: "SPANISH BEGINNER!A118",
    1638: "SPANISH BEGINNER!A119",
    1639: "SPANISH BEGINNER!A120",
    1672: "SPANISH BEGINNER!A137",
    1674: "SPANISH BEGINNER!A139",
    1702: "SPANISH BEGINNER!A154",
    1711: "SPANISH BEGINNER!A160",
    1721: "SPANISH BEGINNER!A167",
    1756: "SPANISH INTERMEDIATE!A123",
    1762: "SPANISH INTERMEDIATE!A127",
    1768: "SPANISH INTERMEDIATE!A130",
    1771: "SPANISH INTERMEDIATE!A132",
    1775: "SPANISH INTERMEDIATE!A133",
    1841: "SPANISH INTERMEDIATE!A174",
    1847: "SPANISH INTERMEDIATE!A177",
    1850: "SPANISH INTERMEDIATE!A180",
    1851: "SPANISH INTERMEDIATE!A181",
    1864: "SPANISH INTERMEDIATE!A188",
    1881: "SPANISH INTERMEDIATE!A197",
    1912: "SPANISH INTERMEDIATE!A216",
    1919: "SPANISH INTERMEDIATE!A220",
    1969: "SPANISH ADVANCED!A121",
    1974: "SPANISH ADVANCED!A123",
    1976: "SPANISH ADVANCED!A125",
    2018: "SPANISH ADVANCED!A151",
    2073: "SPANISH ADVANCED!A182",
    2077: "SPANISH ADVANCED!A183",
    2092: "SPANISH ADVANCED!A191",
}
LEGACY_VARIANT_WINNERS: Final = {527, 1617, 1618, 1634}
HYBRID_VARIANT_REPLACEMENTS: Final = {
    "ENGLISH INTERMEDIATE!A87": (
        ("songs, ”", "songs,”"),
        (
            "To prepare for the role of Abraham, Chavarria said",
            "To prepare for the role of Abraham, Chavira said",
        ),
        (
            "Chavarria said of Selena's father",
            "Chavira said of Selena's father",
        ),
    ),
    "ENGLISH ADVANCED!A108": (
        (
            "Coeliac disease (UK, celiac US)",
            "Coeliac disease (UK; celiac disease in the US)",
        ),
    ),
    "SPANISH ADVANCED!A151": (
        ("por mil millones, con la opción", "por mil millones de dólares, con la opción"),
    ),
}

# Two cells contain several complete readings.  Split only at the spreadsheet's
# own non-empty line boundaries, retaining the spreadsheet wording.
COMPOUND_SPREADSHEET_CELLS: Final = {
    "ENGLISH INTERMEDIATE!A54": (514, 515),
    "SPANISH BEGINNER!A122": (1641, 1642, 1646, 1647, 1648),
}

# In these four cases the spreadsheet cell is only an excerpt of the legacy
# passage.  Retain the complete passage instead of publishing both versions.
FULL_LEGACY_REPLACEMENTS: Final = {
    "ENGLISH INTERMEDIATE!A29": 470,
    "ENGLISH INTERMEDIATE!A32": 475,
    "SPANISH BEGINNER!A112": 1624,
    "SPANISH INTERMEDIATE!A158": 1815,
}

# These legacy readings have no usable counterpart in the expanded source and
# are intentionally retained.  Keeping the explicit set prevents a comparison
# threshold change from silently adding or dropping content.
LEGACY_ONLY_SOURCE_LINES: Final = {
    239,
    317,
    349,
    526,
    546,
    601,
    688,
    754,
    809,
    810,
    813,
    847,
    911,
    917,
    925,
    935,
    957,
    981,
    989,
    990,
    1005,
    1027,
    1028,
    1029,
    1076,
    1081,
    1084,
    1088,
    1098,
    1110,
    1122,
    1135,
    1147,
    1159,
    1174,
    1186,
    1198,
    1213,
    1225,
    1237,
    1252,
    1264,
    1276,
    1291,
    1303,
    1315,
    1330,
    1342,
    1354,
    1369,
    1381,
    1393,
    1408,
    1420,
    1432,
    1459,
    1471,
    1486,
    1498,
    1510,
    1525,
    1543,
    1555,
    1556,
    1557,
    1558,
    1559,
    1560,
    1561,
    1562,
    1563,
    1564,
    1565,
    1566,
    1567,
    1568,
    1569,
    1570,
    1690,
    1865,
    1871,
    2000,
    2025,
    2048,
    2069,
    2086,
    2107,
    2108,
    2109,
    2110,
    2111,
    2112,
    2113,
    2114,
    2115,
    2116,
    2117,
    2118,
    2119,
    2120,
    2121,
    2122,
    2123,
    2127,
    2128,
    2129,
    2130,
    2131,
    2132,
    2133,
    2134,
    2135,
    2136,
    2137,
    2138,
    2139,
    2140,
    2141,
    2179,
    2180,
    2181,
    2182,
    2183,
    2186,
    2188,
    2189,
    2190,
    2191,
    2192,
    2193,
    2194,
    2195,
    2196,
}

PREFERRED_DUPLICATE_REFS: Final = {"SPANISH ADVANCED!A177"}
EXPECTED_REMOVED_DUPLICATE_REFS: Final = {
    "ENGLISH ADVANCED!A241",
    "ENGLISH ADVANCED!A242",
    "ENGLISH ADVANCED!A243",
    "ENGLISH ADVANCED!A244",
    "ENGLISH ADVANCED!A245",
    "ENGLISH ADVANCED!A246",
    "ENGLISH ADVANCED!A247",
    "ENGLISH ADVANCED!A283",
    "ENGLISH ADVANCED!A284",
    "ENGLISH ADVANCED!A285",
    "ENGLISH ADVANCED!A286",
    "SPANISH INTERMEDIATE!A254",
    "SPANISH ADVANCED!A103",
}

EMOTION_RE: Final = re.compile(
    r"^###\s*(?:Expected Emotion|Emoci[oó]n esperada)\s*:\s*(.*?)\s*\n(.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


class CatalogBuildError(ValueError):
    """Raised when either source or a reviewed reconciliation rule drifts."""


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    language: str
    level: str
    body: str
    expected_emotion: str | None
    source_kind: str
    source_ref: str
    source_category: str | None = None
    source_line: int | None = None

    def output(self) -> dict[str, object]:
        item: dict[str, object] = {
            "language": self.language,
            "level": self.level,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
        }
        if self.source_category is not None:
            item["source_category"] = self.source_category
        if self.source_line is not None:
            item["source_line"] = self.source_line
        if self.expected_emotion is not None:
            item["expected_emotion"] = self.expected_emotion
        item["body"] = self.body
        return item


@dataclass(frozen=True, slots=True)
class CatalogBuildResult:
    records: tuple[CatalogRecord, ...]
    report: dict[str, object]


def _clean_body(raw_body: str) -> str:
    normalized = (
        unicodedata.normalize("NFC", raw_body)
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines = [" ".join(line.split()) for line in normalized.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _body_key(body: str) -> str:
    return re.sub(
        r"[\W_]+",
        "",
        unicodedata.normalize("NFKC", body).casefold(),
        flags=re.UNICODE,
    )


def _record_key(record: CatalogRecord) -> tuple[str, str, str]:
    return record.language, record.level, _body_key(record.body)


def _parse_spreadsheet_body(raw_body: str) -> tuple[str, str | None]:
    cleaned = _clean_body(raw_body)
    match = EMOTION_RE.fullmatch(cleaned)
    if match is None:
        return cleaned, None
    emotion = " ".join(match.group(1).split())
    body = _clean_body(match.group(2))
    if not emotion or not body:
        raise CatalogBuildError("empty expected-emotion label or passage")
    return body, emotion


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogBuildError(f"cannot read catalog source {path}") from error


def _load_legacy(path: Path) -> list[CatalogRecord]:
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise CatalogBuildError("legacy catalog snapshot must be a JSON array")
    records: list[CatalogRecord] = []
    for index, item in enumerate(raw):
        try:
            language = str(item["language"])
            level = str(item["level"])
            source_line = int(item["source_line"])
            body = _clean_body(str(item["body"]))
            category = str(item["source_category"])
        except (KeyError, TypeError, ValueError) as error:
            raise CatalogBuildError(
                f"invalid legacy reading at index {index}"
            ) from error
        if language not in ACTIVE_LANGUAGES or not body:
            raise CatalogBuildError(f"invalid legacy reading at index {index}")
        records.append(
            CatalogRecord(
                language=language,
                level=level,
                body=body,
                expected_emotion=None,
                source_kind="legacy_google_doc",
                source_ref=f"google_doc_readings.txt:{source_line}",
                source_category=category,
                source_line=source_line,
            )
        )
    if len(records) != 1_014:
        raise CatalogBuildError(
            f"legacy snapshot changed: expected 1014 readings, found {len(records)}"
        )
    if len({record.source_line for record in records}) != len(records):
        raise CatalogBuildError("legacy source-line references are not unique")
    return records


def _load_retired_keys(path: Path) -> set[tuple[str, str, str]]:
    raw = _load_json(path)
    if not isinstance(raw, list) or len(raw) != 13:
        raise CatalogBuildError(
            "retired catalog snapshot must contain exactly 13 readings"
        )
    keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw):
        try:
            language = str(item["language"])
            level = str(item["level"])
            body = _clean_body(str(item["body"]))
        except (KeyError, TypeError) as error:
            raise CatalogBuildError(
                f"invalid retired reading at index {index}"
            ) from error
        if language not in ACTIVE_LANGUAGES or not body:
            raise CatalogBuildError(f"invalid retired reading at index {index}")
        keys.add((language, level, _body_key(body)))
    if len(keys) != len(raw):
        raise CatalogBuildError("retired catalog snapshot contains duplicates")
    return keys


def _load_spreadsheet(
    path: Path,
) -> tuple[list[dict[str, object]], Counter[tuple[str, str | None]]]:
    raw = _load_json(path)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CatalogBuildError("unsupported spreadsheet snapshot schema")
    if raw.get("sheet_counts") != EXPECTED_SPREADSHEET_COUNTS:
        raise CatalogBuildError(
            "spreadsheet sheet counts changed: expected "
            f"{EXPECTED_SPREADSHEET_COUNTS}, found {raw.get('sheet_counts')}"
        )
    items = raw.get("readings")
    if not isinstance(items, list) or len(items) != sum(
        EXPECTED_SPREADSHEET_COUNTS.values()
    ):
        raise CatalogBuildError("spreadsheet reading count changed")

    held_counts: Counter[tuple[str, str | None]] = Counter()
    seen_refs: set[str] = set()
    for index, item in enumerate(items):
        try:
            sheet = str(item["sheet"])
            cell = str(item["cell"])
            language = str(item["language"])
            level_value = item["level"]
            level = None if level_value is None else str(level_value)
            body = str(item["body"])
        except (KeyError, TypeError) as error:
            raise CatalogBuildError(
                f"invalid spreadsheet reading at index {index}"
            ) from error
        source_ref = f"{sheet}!{cell}"
        if source_ref in seen_refs or not body.strip():
            raise CatalogBuildError(
                f"duplicate or empty spreadsheet reading at {source_ref}"
            )
        seen_refs.add(source_ref)
        if language not in ACTIVE_LANGUAGES:
            held_counts[(language, level)] += 1
    if held_counts != Counter(EXPECTED_HELD_COUNTS):
        raise CatalogBuildError(
            f"held-language counts changed: found {dict(held_counts)}"
        )
    return items, held_counts


def _apply_hybrid_replacements(source_ref: str, body: str) -> str:
    for old, new in HYBRID_VARIANT_REPLACEMENTS.get(source_ref, ()):
        if body.count(old) != 1:
            raise CatalogBuildError(
                f"reviewed replacement at {source_ref} no longer matches {old!r}"
            )
        body = body.replace(old, new)
    return body


def _spreadsheet_records(
    raw_items: list[dict[str, object]],
    legacy_by_line: dict[int, CatalogRecord],
) -> tuple[list[CatalogRecord], set[str], int]:
    near_by_ref = {ref: line for line, ref in NEAR_VARIANT_PAIRS.items()}
    records: list[CatalogRecord] = []
    emotion_count = 0
    seen_active_refs: set[str] = set()
    for item in raw_items:
        language = str(item["language"])
        if language not in ACTIVE_LANGUAGES:
            continue
        level = str(item["level"])
        source_ref = f"{item['sheet']}!{item['cell']}"
        seen_active_refs.add(source_ref)
        body, expected_emotion = _parse_spreadsheet_body(str(item["body"]))
        if expected_emotion is not None:
            emotion_count += 1

        compound_lines = COMPOUND_SPREADSHEET_CELLS.get(source_ref)
        if compound_lines is not None:
            parts = body.splitlines()
            if len(parts) != len(compound_lines):
                raise CatalogBuildError(
                    f"compound cell {source_ref} has {len(parts)} parts; "
                    f"expected {len(compound_lines)}"
                )
            for part_number, (part, source_line) in enumerate(
                zip(parts, compound_lines, strict=True),
                start=1,
            ):
                legacy = legacy_by_line[source_line]
                if _body_key(part) != _body_key(legacy.body):
                    raise CatalogBuildError(
                        f"compound part {source_ref}#{part_number} drifted "
                        f"from legacy line {source_line}"
                    )
                records.append(
                    CatalogRecord(
                        language=language,
                        level=level,
                        body=part,
                        expected_emotion=expected_emotion,
                        source_kind="spreadsheet_compound_part",
                        source_ref=f"{source_ref}#part-{part_number}",
                    )
                )
            continue

        source_kind = "spreadsheet"
        source_line: int | None = None
        source_category: str | None = None
        if source_ref in FULL_LEGACY_REPLACEMENTS:
            source_line = FULL_LEGACY_REPLACEMENTS[source_ref]
            legacy = legacy_by_line[source_line]
            if _body_key(body) not in _body_key(legacy.body):
                raise CatalogBuildError(
                    f"spreadsheet excerpt {source_ref} drifted from "
                    f"legacy line {source_line}"
                )
            body = legacy.body
            expected_emotion = legacy.expected_emotion
            source_kind = "reviewed_full_legacy"
            source_category = legacy.source_category
        elif source_ref in near_by_ref:
            source_line = near_by_ref[source_ref]
            legacy = legacy_by_line[source_line]
            similarity = difflib.SequenceMatcher(
                None,
                _body_key(body),
                _body_key(legacy.body),
                autojunk=False,
            ).ratio()
            if similarity < 0.94:
                raise CatalogBuildError(
                    f"reviewed variant {source_ref} drifted from legacy "
                    f"line {source_line}: similarity is {similarity:.3f}"
                )
            if source_line in LEGACY_VARIANT_WINNERS:
                body = legacy.body
                expected_emotion = legacy.expected_emotion
                source_kind = "reviewed_legacy_variant"
                source_category = legacy.source_category
            elif source_ref in HYBRID_VARIANT_REPLACEMENTS:
                body = _apply_hybrid_replacements(source_ref, body)
                source_kind = "reviewed_hybrid_variant"

        records.append(
            CatalogRecord(
                language=language,
                level=level,
                body=body,
                expected_emotion=expected_emotion,
                source_kind=source_kind,
                source_ref=source_ref,
                source_category=source_category,
                source_line=source_line,
            )
        )

    expected_rule_refs = (
        set(NEAR_VARIANT_PAIRS.values())
        | set(COMPOUND_SPREADSHEET_CELLS)
        | set(FULL_LEGACY_REPLACEMENTS)
    )
    if missing_refs := expected_rule_refs - seen_active_refs:
        raise CatalogBuildError(
            f"reconciliation references missing spreadsheet cells: "
            f"{sorted(missing_refs)}"
        )

    deduplicated: list[CatalogRecord] = []
    index_by_key: dict[tuple[str, str, str], int] = {}
    removed_refs: set[str] = set()
    for record in records:
        key = _record_key(record)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(deduplicated)
            deduplicated.append(record)
            continue
        if record.source_ref in PREFERRED_DUPLICATE_REFS:
            removed_refs.add(deduplicated[existing_index].source_ref)
            deduplicated[existing_index] = record
        else:
            removed_refs.add(record.source_ref)

    if removed_refs != EXPECTED_REMOVED_DUPLICATE_REFS:
        raise CatalogBuildError(
            "spreadsheet duplicate set changed: expected "
            f"{sorted(EXPECTED_REMOVED_DUPLICATE_REFS)}, "
            f"found {sorted(removed_refs)}"
        )
    if emotion_count != EXPECTED_EMOTION_COUNT:
        raise CatalogBuildError(
            f"expected {EXPECTED_EMOTION_COUNT} emotion labels, "
            f"found {emotion_count}"
        )
    return deduplicated, removed_refs, emotion_count


def build_catalog(
    spreadsheet_path: Path = DEFAULT_SPREADSHEET_SOURCE,
    legacy_path: Path = DEFAULT_LEGACY_SOURCE,
    retired_path: Path = DEFAULT_RETIRED_SOURCE,
) -> CatalogBuildResult:
    """Reconcile both snapshots and return validated active catalog records."""
    raw_spreadsheet, held_counts = _load_spreadsheet(spreadsheet_path)
    legacy_records = _load_legacy(legacy_path)
    retired_keys = _load_retired_keys(retired_path)
    legacy_by_line = {
        record.source_line: record
        for record in legacy_records
        if record.source_line is not None
    }
    if len(set(NEAR_VARIANT_PAIRS.values())) != len(NEAR_VARIANT_PAIRS):
        raise CatalogBuildError(
            "near-variant reconciliation reuses a spreadsheet cell"
        )

    explicit_groups = [
        set(NEAR_VARIANT_PAIRS),
        {
            source_line
            for lines in COMPOUND_SPREADSHEET_CELLS.values()
            for source_line in lines
        },
        set(FULL_LEGACY_REPLACEMENTS.values()),
        set(LEGACY_ONLY_SOURCE_LINES),
    ]
    for index, left in enumerate(explicit_groups):
        for right in explicit_groups[index + 1 :]:
            if overlap := left & right:
                raise CatalogBuildError(
                    f"legacy reconciliation groups overlap: {sorted(overlap)}"
                )
    explicit_lines = set().union(*explicit_groups)
    unknown_lines = explicit_lines - set(legacy_by_line)
    if unknown_lines:
        raise CatalogBuildError(
            f"reconciliation references unknown legacy lines: {sorted(unknown_lines)}"
        )

    spreadsheet_records, removed_refs, emotion_count = _spreadsheet_records(
        raw_spreadsheet,
        legacy_by_line,
    )
    spreadsheet_keys = {_record_key(record) for record in spreadsheet_records}
    exact_lines = {
        record.source_line
        for record in legacy_records
        if record.source_line not in explicit_lines
        and _record_key(record) in spreadsheet_keys
    }
    if len(exact_lines) != EXPECTED_EXACT_LEGACY_MATCHES:
        raise CatalogBuildError(
            f"expected {EXPECTED_EXACT_LEGACY_MATCHES} exact legacy matches, "
            f"found {len(exact_lines)}"
        )
    all_legacy_lines = {
        record.source_line
        for record in legacy_records
        if record.source_line is not None
    }
    covered_lines = explicit_lines | exact_lines
    if covered_lines != all_legacy_lines:
        raise CatalogBuildError(
            "legacy coverage changed; unclassified lines: "
            f"{sorted(all_legacy_lines - covered_lines)}"
        )

    records = list(spreadsheet_records)
    for legacy in legacy_records:
        if legacy.source_line not in LEGACY_ONLY_SOURCE_LINES:
            continue
        records.append(replace(legacy, source_kind="legacy_only"))

    keys = [_record_key(record) for record in records]
    if len(set(keys)) != len(keys):
        duplicates = [
            key for key, count in Counter(keys).items() if count > 1
        ]
        raise CatalogBuildError(
            f"merged catalog contains duplicate readings: {duplicates[:5]}"
        )
    if retired_overlap := set(keys) & retired_keys:
        raise CatalogBuildError(
            f"merged catalog reactivates retired readings: "
            f"{sorted(retired_overlap)[:5]}"
        )
    for record in records:
        if not record.body:
            raise CatalogBuildError(f"empty reading at {record.source_ref}")
        if len(record.body) > MAX_BODY_LENGTH:
            raise CatalogBuildError(
                f"reading at {record.source_ref} is {len(record.body)} "
                f"characters; maximum is {MAX_BODY_LENGTH}"
            )
        if "\u200b" in record.body or "\ufeff" in record.body:
            raise CatalogBuildError(
                f"reading at {record.source_ref} contains hidden characters"
            )

    active_counts = Counter(
        (record.language, record.level) for record in records
    )
    if active_counts != Counter(EXPECTED_ACTIVE_COUNTS):
        raise CatalogBuildError(
            f"active catalog counts changed: found {dict(active_counts)}"
        )

    rendered = render_catalog(records)
    report: dict[str, object] = {
        "schema_version": 1,
        "inputs": {
            "spreadsheet_snapshot": {
                "path": _report_path(spreadsheet_path),
                "sha256": _sha256_file(spreadsheet_path),
            },
            "legacy_snapshot": {
                "path": _report_path(legacy_path),
                "sha256": _sha256_file(legacy_path),
            },
            "retired_snapshot": {
                "path": _report_path(retired_path),
                "sha256": _sha256_file(retired_path),
            },
        },
        "spreadsheet": {
            "rows": len(raw_spreadsheet),
            "active_language_rows": sum(
                count
                for (language, _), count in Counter(
                    (str(item["language"]), item["level"])
                    for item in raw_spreadsheet
                ).items()
                if language in ACTIVE_LANGUAGES
            ),
            "held_language_rows": sum(held_counts.values()),
            "held_counts": [
                {
                    "language": language,
                    "level": level,
                    "count": count,
                }
                for (language, level), count in sorted(
                    held_counts.items(),
                    key=lambda item: (item[0][0], str(item[0][1])),
                )
            ],
            "duplicate_rows_removed": len(removed_refs),
            "duplicate_refs_removed": sorted(removed_refs),
            "compound_cells_split": len(COMPOUND_SPREADSHEET_CELLS),
            "compound_readings_created": sum(
                len(lines) for lines in COMPOUND_SPREADSHEET_CELLS.values()
            ),
            "expected_emotions_extracted": emotion_count,
        },
        "legacy_reconciliation": {
            "readings": len(legacy_records),
            "equivalent": len(exact_lines),
            "reviewed_variants": len(NEAR_VARIANT_PAIRS),
            "compound_parts": sum(
                len(lines) for lines in COMPOUND_SPREADSHEET_CELLS.values()
            ),
            "fuller_legacy_replacements": len(FULL_LEGACY_REPLACEMENTS),
            "legacy_only": len(LEGACY_ONLY_SOURCE_LINES),
            "historically_retired": len(retired_keys),
        },
        "active_catalog": {
            "readings": len(records),
            "counts": [
                {
                    "language": language,
                    "level": level,
                    "count": count,
                }
                for (language, level), count in sorted(active_counts.items())
            ],
            "maximum_body_length": max(len(record.body) for record in records),
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        },
    }
    return CatalogBuildResult(tuple(records), report)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def render_catalog(records: tuple[CatalogRecord, ...] | list[CatalogRecord]) -> str:
    return json.dumps(
        [record.output() for record in records],
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def render_report(report: dict[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spreadsheet-source",
        type=Path,
        default=DEFAULT_SPREADSHEET_SOURCE,
    )
    parser.add_argument(
        "--legacy-source",
        type=Path,
        default=DEFAULT_LEGACY_SOURCE,
    )
    parser.add_argument(
        "--retired-source",
        type=Path,
        default=DEFAULT_RETIRED_SOURCE,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if either committed generated file is stale",
    )
    args = parser.parse_args()

    result = build_catalog(
        args.spreadsheet_source,
        args.legacy_source,
        args.retired_source,
    )
    rendered_catalog = render_catalog(result.records)
    rendered_report = render_report(result.report)
    if args.check:
        stale = [
            path
            for path, expected in (
                (args.output, rendered_catalog),
                (args.report, rendered_report),
            )
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            raise SystemExit(
                "catalog build is stale: rebuild "
                + ", ".join(str(path) for path in stale)
            )
        print(f"catalog is current: {len(result.records)} readings")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered_catalog, encoding="utf-8")
    args.report.write_text(rendered_report, encoding="utf-8")
    print(f"wrote {len(result.records)} readings to {args.output}")
    print(f"wrote build report to {args.report}")


if __name__ == "__main__":
    main()
