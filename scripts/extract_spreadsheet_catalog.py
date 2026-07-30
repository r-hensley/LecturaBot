"""Extract a deterministic reading-source snapshot from the shared workbook.

The bot does not read an XLSX file at runtime.  This importer validates the
workbook's duplicated A/B text columns and writes the reviewable JSON snapshot
consumed by ``scripts/build_catalog.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Final
import xml.etree.ElementTree as ET
import zipfile


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final = ROOT / "sources" / "expanded_spreadsheet_readings.json"
DEFAULT_SOURCE_URL: Final = (
    "https://docs.google.com/spreadsheets/d/"
    "13K89RT43GZj1fvrTym9eoSZDlnDwzEFH/edit"
)
MAIN_NS: Final = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS: Final = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
NS: Final = {"m": MAIN_NS}
CELL_RE: Final = re.compile(r"([A-Z]+)(\d+)")
SHEET_MAP: Final = {
    "ENGLISH BEGINNER": ("en", "beginner"),
    "ENGLISH INTERMEDIATE": ("en", "intermediate"),
    "ENGLISH ADVANCED": ("en", "advanced"),
    "SPANISH BEGINNER": ("es", "beginner"),
    "SPANISH INTERMEDIATE": ("es", "intermediate"),
    "SPANISH ADVANCED": ("es", "advanced"),
    "FRENCH BEGINNER": ("fr", "beginner"),
    "FRENCH INTERMEDIATE": ("fr", "intermediate"),
    "FRENCH ADVANCED": ("fr", "advanced"),
    "PORTUGUESE": ("pt", None),
}


class SpreadsheetExtractError(ValueError):
    """Raised when the workbook cannot be converted safely."""


def _read_cell(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("m:v", NS)
    inline = cell.find("m:is", NS)
    if cell_type == "s" and value is not None:
        return shared_strings[int(value.text)]
    if cell_type == "inlineStr" and inline is not None:
        return "".join(
            element.text or ""
            for element in inline.iter(f"{{{MAIN_NS}}}t")
        )
    return (value.text or "") if value is not None else ""


def extract_snapshot(
    workbook_path: Path,
    *,
    source_url: str,
    downloaded_on: str,
) -> dict[str, object]:
    """Return a JSON-ready workbook snapshot after structural validation."""
    readings: list[dict[str, object]] = []
    sheet_counts: dict[str, int] = {}
    with zipfile.ZipFile(workbook_path) as archive:
        try:
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relations = ET.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
        except (KeyError, ET.ParseError) as error:
            raise SpreadsheetExtractError(
                f"{workbook_path} is not a readable XLSX workbook"
            ) from error

        shared_strings = [
            "".join(
                element.text or ""
                for element in item.iter(f"{{{MAIN_NS}}}t")
            )
            for item in shared_root.findall("m:si", NS)
        ]
        targets = {
            item.attrib["Id"]: item.attrib["Target"] for item in relations
        }
        sheets = workbook.find("m:sheets", NS)
        if sheets is None:
            raise SpreadsheetExtractError("workbook has no worksheets")

        actual_sheet_names = [sheet.attrib["name"] for sheet in sheets]
        if set(actual_sheet_names) != set(SHEET_MAP):
            raise SpreadsheetExtractError(
                "worksheet set changed: expected "
                f"{sorted(SHEET_MAP)}, found {sorted(actual_sheet_names)}"
            )

        for sheet in sheets:
            sheet_name = sheet.attrib["name"]
            language, level = SHEET_MAP[sheet_name]
            relation_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[relation_id].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            try:
                worksheet = ET.fromstring(archive.read(target))
            except (KeyError, ET.ParseError) as error:
                raise SpreadsheetExtractError(
                    f"cannot read worksheet {sheet_name}"
                ) from error

            cells_by_row: dict[int, dict[str, str]] = {}
            for cell in worksheet.findall(".//m:c", NS):
                reference = cell.attrib.get("r", "")
                match = CELL_RE.fullmatch(reference)
                if match is None:
                    continue
                column, row_text = match.groups()
                if column not in {"A", "B"}:
                    continue
                cells_by_row.setdefault(int(row_text), {})[column] = _read_cell(
                    cell,
                    shared_strings,
                )

            count = 0
            for row_number in sorted(cells_by_row):
                if row_number == 1:
                    continue
                row = cells_by_row[row_number]
                body = row.get("A", "")
                duplicate = row.get("B", "")
                if not body.strip() and not duplicate.strip():
                    continue
                if body != duplicate:
                    raise SpreadsheetExtractError(
                        f"{sheet_name}!A{row_number} does not match "
                        f"{sheet_name}!B{row_number}"
                    )
                readings.append(
                    {
                        "sheet": sheet_name,
                        "cell": f"A{row_number}",
                        "language": language,
                        "level": level,
                        "body": body,
                    }
                )
                count += 1
            sheet_counts[sheet_name] = count

    return {
        "schema_version": 1,
        "source": {
            "url": source_url,
            "downloaded_on": downloaded_on,
            "sha256": hashlib.sha256(workbook_path.read_bytes()).hexdigest(),
        },
        "sheet_counts": sheet_counts,
        "readings": readings,
    }


def render_snapshot(snapshot: dict[str, object]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--downloaded-on", required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed snapshot differs from this workbook",
    )
    args = parser.parse_args()

    rendered = render_snapshot(
        extract_snapshot(
            args.workbook,
            source_url=args.source_url,
            downloaded_on=args.downloaded_on,
        )
    )
    if args.check:
        if (
            not args.output.exists()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            raise SystemExit(f"spreadsheet snapshot is stale: rebuild {args.output}")
        print(f"spreadsheet snapshot is current: {args.output}")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote spreadsheet snapshot to {args.output}")


if __name__ == "__main__":
    main()
