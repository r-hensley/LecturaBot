"""Build the offline reading catalog from the vendored Google Doc export.

The source document is intentionally not fetched at runtime.  This script reads
the committed plain-text snapshot, repairs the document's few malformed code
fences, maps its source sections to LecturaBot's three levels, and writes the
derived JSON seed file used by the bot.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE: Final = ROOT / "sources" / "google_doc_readings.txt"
DEFAULT_OUTPUT: Final = (
    ROOT / "src" / "lecturabot" / "data" / "google_doc_readings.json"
)
FENCE: Final = "```"
MAX_BODY_LENGTH: Final = 1_600

SOURCE_CATEGORY_TO_CATALOG: Final = {
    ("ENGLISH", "EASY"): ("en", "beginner", "easy"),
    ("ENGLISH", "MEDIUM"): ("en", "intermediate", "medium"),
    ("ENGLISH", "HARD"): ("en", "advanced", "hard"),
    ("ENGLISH", "SUPER HARD"): ("en", "advanced", "super_hard"),
    ("ENGLISH", "HALLOWEEN"): ("en", "advanced", "halloween"),
    ("SPANISH", "EASY"): ("es", "beginner", "easy"),
    ("SPANISH", "MEDIUM"): ("es", "intermediate", "medium"),
    ("SPANISH", "HARD"): ("es", "advanced", "hard"),
    ("SPANISH", "SUPER HARD"): ("es", "advanced", "super_hard"),
    ("SPANISH", "HALLOWEEN"): ("es", "advanced", "halloween"),
}

EXPECTED_SOURCE_COUNTS: Final = {
    ("ENGLISH", "EASY"): 220,
    ("ENGLISH", "MEDIUM"): 238,
    ("ENGLISH", "HARD"): 163,
    ("ENGLISH", "SUPER HARD"): 34,
    ("ENGLISH", "HALLOWEEN"): 17,
    ("SPANISH", "EASY"): 75,
    ("SPANISH", "MEDIUM"): 117,
    ("SPANISH", "HARD"): 104,
    ("SPANISH", "SUPER HARD"): 32,
    ("SPANISH", "HALLOWEEN"): 15,
}

# These six source passages have one missing fence delimiter.  The line
# numbers make each deliberate repair visible and make unexpected source drift
# fail loudly instead of silently changing passage boundaries.
EXPECTED_FENCE_REPAIRS: Final = {
    ("missing_close", 325),
    ("missing_close", 356),
    ("missing_open", 520),
    ("missing_close", 928),
    ("missing_close", 1811),
    ("missing_open", 1740),
}

SECTION_RE: Final = re.compile(
    r"^(ENGLISH|SPANISH) (EASY|MEDIUM|HARD|SUPER HARD)$"
)
HALLOWEEN_RE: Final = re.compile(
    r"^SFW HALLOWEEN TEXTS - (ENGLISH|SPANISH)$"
)


class CatalogBuildError(ValueError):
    """Raised when the source snapshot cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class SourceReading:
    category: tuple[str, str]
    body: str
    source_line: int


def _source_category(line: str) -> tuple[str, str] | None:
    section = SECTION_RE.fullmatch(line)
    if section is not None:
        return section.group(1), section.group(2)
    halloween = HALLOWEEN_RE.fullmatch(line)
    if halloween is not None:
        return halloween.group(1), "HALLOWEEN"
    return None


def _clean_body(raw_body: str) -> str:
    lines = []
    for raw_line in raw_body.replace("\ufeff", "").replace("\u200b", "").splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines).strip().strip("`").strip()


def _append_reading(
    readings: list[SourceReading],
    *,
    category: tuple[str, str] | None,
    body: str,
    source_line: int,
) -> None:
    if category is None:
        raise CatalogBuildError(
            f"reading at source line {source_line} appears before a category heading"
        )
    cleaned = _clean_body(body)
    if not cleaned:
        raise CatalogBuildError(f"empty reading at source line {source_line}")
    readings.append(SourceReading(category, cleaned, source_line))


def parse_source(source_text: str) -> tuple[list[SourceReading], set[tuple[str, int]]]:
    """Extract fenced passages and report deliberate malformed-fence repairs."""
    lines = source_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    category: tuple[str, str] | None = None
    readings: list[SourceReading] = []
    repairs: set[tuple[str, int]] = set()
    buffer: list[str] | None = None
    buffer_category: tuple[str, str] | None = None
    buffer_start = 0

    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        heading = _source_category(stripped)

        if buffer is not None and heading is not None:
            _append_reading(
                readings,
                category=buffer_category,
                body="\n".join(buffer),
                source_line=buffer_start,
            )
            repairs.add(("missing_close", buffer_start))
            buffer = None
            category = heading
            continue

        if buffer is None and heading is not None:
            category = heading
            continue

        fence_count = stripped.count(FENCE)

        if buffer is not None:
            if fence_count >= 2:
                _append_reading(
                    readings,
                    category=buffer_category,
                    body="\n".join(buffer),
                    source_line=buffer_start,
                )
                repairs.add(("missing_close", buffer_start))
                buffer = None
                first_fence = stripped.find(FENCE)
                last_fence = stripped.rfind(FENCE)
                _append_reading(
                    readings,
                    category=category,
                    body=stripped[first_fence + len(FENCE) : last_fence],
                    source_line=line_number,
                )
            elif fence_count == 1 and stripped.endswith(FENCE):
                buffer.append(stripped[: -len(FENCE)])
                _append_reading(
                    readings,
                    category=buffer_category,
                    body="\n".join(buffer),
                    source_line=buffer_start,
                )
                buffer = None
            else:
                buffer.append(raw_line)
            continue

        if fence_count >= 2:
            first_fence = stripped.find(FENCE)
            last_fence = stripped.rfind(FENCE)
            prefix = stripped[:first_fence].replace("\u200b", "").strip()
            suffix = stripped[last_fence + len(FENCE) :].replace("\u200b", "").strip()
            if prefix not in {"", "."} or suffix not in {"", "."}:
                raise CatalogBuildError(
                    f"unexpected text outside fences at source line {line_number}"
                )
            _append_reading(
                readings,
                category=category,
                body=stripped[first_fence + len(FENCE) : last_fence],
                source_line=line_number,
            )
        elif fence_count == 1:
            fence_position = stripped.find(FENCE)
            before = stripped[:fence_position].replace("\u200b", "").strip()
            after = stripped[fence_position + len(FENCE) :]
            if after and before in {"", "."}:
                buffer = [after]
                buffer_category = category
                buffer_start = line_number
            elif before and not after:
                _append_reading(
                    readings,
                    category=category,
                    body=before,
                    source_line=line_number,
                )
                repairs.add(("missing_open", line_number))
            else:
                raise CatalogBuildError(
                    f"ambiguous fence at source line {line_number}"
                )

    if buffer is not None:
        raise CatalogBuildError(
            f"unclosed reading beginning at source line {buffer_start}"
        )
    return readings, repairs


def build_catalog(source_text: str) -> list[dict[str, object]]:
    """Build validated JSON-ready records in document order."""
    readings, repairs = parse_source(source_text)
    if repairs != EXPECTED_FENCE_REPAIRS:
        raise CatalogBuildError(
            f"source fence repairs changed: expected {sorted(EXPECTED_FENCE_REPAIRS)}, "
            f"found {sorted(repairs)}"
        )

    counts = Counter(reading.category for reading in readings)
    if counts != Counter(EXPECTED_SOURCE_COUNTS):
        raise CatalogBuildError(
            f"source category counts changed: expected {EXPECTED_SOURCE_COUNTS}, "
            f"found {dict(counts)}"
        )

    bodies: set[str] = set()
    records: list[dict[str, object]] = []
    for reading in readings:
        comparison_body = " ".join(reading.body.split()).casefold()
        if comparison_body in bodies:
            raise CatalogBuildError(
                f"duplicate reading at source line {reading.source_line}"
            )
        bodies.add(comparison_body)
        if len(reading.body) > MAX_BODY_LENGTH:
            raise CatalogBuildError(
                f"reading at source line {reading.source_line} is "
                f"{len(reading.body)} characters; maximum is {MAX_BODY_LENGTH}"
            )
        if FENCE in reading.body:
            raise CatalogBuildError(
                f"reading at source line {reading.source_line} contains a fence"
            )

        language, level, source_category = SOURCE_CATEGORY_TO_CATALOG[
            reading.category
        ]
        records.append(
            {
                "language": language,
                "level": level,
                "source_category": source_category,
                "source_line": reading.source_line,
                "body": reading.body,
            }
        )
    return records


def render_catalog(records: list[dict[str, object]]) -> str:
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed output differs from the source snapshot",
    )
    args = parser.parse_args()

    records = build_catalog(args.source.read_text(encoding="utf-8-sig"))
    rendered = render_catalog(records)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"catalog is stale: rebuild {args.output}")
        print(f"catalog is current: {len(records)} readings")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {len(records)} readings to {args.output}")


if __name__ == "__main__":
    main()
