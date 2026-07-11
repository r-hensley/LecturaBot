# Reading catalog source snapshot

`google_doc_readings.txt` is the plain-text export downloaded on 2026-07-11
from the community's shared **Texts for Sesión de Lectura** document:

<https://docs.google.com/document/d/1O2KZYIn1S5xcWHAOvSo3bN2Wx-f-D1qKd9mMW6U5DhM/edit>

The bot never contacts Google Docs at runtime. The committed source snapshot is
converted into `src/lecturabot/data/google_doc_readings.json` with:

```bash
/mnt/c/Users/ryry0/Documents/Python/.venv/bin/python \
  scripts/build_google_doc_catalog.py
```

The source document contains repeated headings and six malformed fence
boundaries. The build script repairs only those explicitly recorded locations,
validates all category counts and body lengths, and fails if the snapshot's
structure changes unexpectedly.

The three-level bot interface uses this mapping:

| Source category | LecturaBot level |
| --- | --- |
| Easy / Fácil | Beginner / Principiante |
| Medium / Intermedio | Intermediate / Intermedio |
| Hard / Difícil | Advanced / Avanzado |
| Super Hard / Super Difícil | Advanced / Avanzado |
| SFW Halloween | Advanced / Avanzado |
