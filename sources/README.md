# Reading catalog sources

LecturaBot's runtime catalog is built entirely from committed snapshots. The
bot never contacts Google Drive while running.

## Maintained inputs

`expanded_spreadsheet_readings.json` is the column-A extraction of the expanded
community workbook downloaded on 2026-07-29:

<https://docs.google.com/spreadsheets/d/13K89RT43GZj1fvrTym9eoSZDlnDwzEFH/edit>

The snapshot includes the source sheet and cell for every entry. Its metadata
records the original XLSX SHA-256. The importer also verifies that each source
text in column A is identical to the workbook's duplicate column B.

`legacy_catalog_2026-07-11.json` is the structured snapshot of the 1,014
previously active English and Spanish readings. It supplies the 133 passages
that are absent from the expanded spreadsheet and the four cases where the
legacy passage is complete while the spreadsheet contains only an excerpt.

`google_doc_readings.txt` is retained as the historical raw export behind that
legacy snapshot. Its malformed fence boundaries and repeated headings are no
longer part of the active build.

<https://docs.google.com/document/d/1O2KZYIn1S5xcWHAOvSo3bN2Wx-f-D1qKd9mMW6U5DhM/edit>

`legacy_retired_readings_2026-07-20.json` preserves the 13 passages retired
before this merge. It is an exclusion guard and historical record rather than
a runtime seed.

## Reconciliation policy

The build checks every legacy reading against one and only one reviewed group:

- 820 equivalent passages represented by the spreadsheet
- 50 near-equivalent passages reviewed individually
- 7 passages contained in two compound spreadsheet cells
- 4 complete legacy passages replacing spreadsheet excerpts
- 133 legacy-only passages retained to prevent content loss

For equivalent alternatives, the spreadsheet version wins. A legacy or hybrid
version is used only where review found a material grammar, spelling, meaning,
or completeness issue. Two compound cells are split only at their existing
line/story boundaries so every resulting Discord passage remains renderable.

The source inventory also contains 37 French and 235 Portuguese readings.
They are reported as held rather than silently discarded: French needs a full
picker/runtime language slice, and the Portuguese sheet does not assign levels.

## Build and verification

Regenerate the runtime catalog and deterministic report with:

```bash
/mnt/c/Users/ryry0/Documents/Python/.venv/bin/python \
  scripts/build_catalog.py
```

Verify that both generated files are current:

```bash
/mnt/c/Users/ryry0/Documents/Python/.venv/bin/python \
  scripts/build_catalog.py --check
```

The outputs are:

- `src/lecturabot/data/catalog.json`
- `sources/catalog_build_report.json`

To refresh the committed snapshot from a newly downloaded workbook:

```bash
/mnt/c/Users/ryry0/Documents/Python/.venv/bin/python \
  scripts/extract_spreadsheet_catalog.py /path/to/readings.xlsx \
  --downloaded-on YYYY-MM-DD
```

Review and update the reconciliation constants in `scripts/build_catalog.py`
when source content changes. The builder intentionally fails on count,
reference, duplicate, coverage, hidden-character, or body-length drift.
`tests/test_catalog_data.py` additionally renders every active passage and
enforces Discord's 2,000-character message limit.
