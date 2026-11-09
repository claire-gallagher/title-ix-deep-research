"""Download EADA data files into data/raw/eada/<year>/, ready for build_eada.py.

The data file page (https://ope.ed.gov/athletics/#/datafile/list) is a JavaScript app; the list
behind it is a public JSON API. We take the per-year "EADA_YYYY-YYYY.zip" (an institution-level
and a sport-level Excel file, plus Word codebooks) and skip the "All Data Combined" zips, which
repeat the same data as one wide row per school.

Folders are named by the year the API labels the file with, which is the END of the academic
year: 2025 = 2024-25. build_eada.py uses the folder name as survey_year.

  uv run download_eada.py                  2019-20 through the latest year
  uv run download_eada.py --from 2016      more history
  uv run download_eada.py --list           show what's available, download nothing
"""

import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

API = "https://ope.ed.gov/athletics/api/dataFiles"
RAW = Path(__file__).parent / "data" / "raw" / "eada"
# Only these leave the zip: the Excel data build_eada.py reads and the codebooks a human reads.
# SAS/SPSS copies of the same data are left out.
KEEP = (".xlsx", ".xls", ".csv", ".doc", ".docx", ".pdf")
HEADERS = {"User-Agent": "title-ix-deep-research/0.1 (course project; EADA bulk download)"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=300) as r:
        return r.read()


def file_list() -> list[dict]:
    files = json.loads(get(f"{API}/fileList"))
    return [f for f in files if "combined" not in f["FileName"].lower()]


def download(entry: dict) -> list[str]:
    year_dir = RAW / str(entry["Year"])
    if year_dir.exists() and any(year_dir.iterdir()):
        return [f"skipped, {year_dir} already has files"]
    url = f"{API}/file?fileName={urllib.parse.quote(entry['FileName'])}"
    data = get(url)
    kept = []
    year_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            # Flatten: keep the base name only, so nothing in the zip can write outside year_dir.
            name = Path(info.filename).name
            if info.is_dir() or not name.lower().endswith(KEEP):
                continue
            (year_dir / name).write_bytes(z.read(info))
            kept.append(f"{name} ({info.file_size / 1e6:.1f} MB)")
    return kept


if __name__ == "__main__":
    args = sys.argv[1:]
    start = int(args[args.index("--from") + 1]) if "--from" in args else 2020
    entries = [f for f in file_list() if f["Year"] >= start]
    for f in entries:
        print(f"{f['Year']}  {f['LinkName']}  ({f['FileName']})")
    if "--list" in args:
        sys.exit()
    for f in entries:
        print(f"\n== {f['Year']}: {f['FileName']}")
        for line in download(f):
            print("  ", line)
