# Data

## EADA (Equity in Athletics Disclosure Act)

- **Source:** U.S. Department of Education, Office of Postsecondary Education,
  https://ope.ed.gov/athletics/#/datafile/list
- **Download:** one folder per year into `data/raw/eada/<year>/` (gitignored), e.g.
  `data/raw/eada/2023/<files>`. Use the folder name for the year the files are labelled with.
- **Download script:** `uv run download_eada.py` fetches 2019-20 through the latest year from the
  site's JSON API (`https://ope.ed.gov/athletics/api/dataFiles/fileList`); `--from 2016` for more,
  `--list` to see what's there. Downloaded so far: 2016-2025 folders (academic years 2015-16 to 2024-25).
- **Year label = END of the academic year:** folder `2025` holds "Data for academic year 2024-25"
  (the API's own `Year` field), so `survey_year = 2025` means 2024-25.
- **Files per year** (Excel, from `EADA_YYYY-YYYY.zip`; SAS/SPSS copies not kept):
  - `InstLevel.xlsx`: one row per institution (~2,000/yr), 167-168 columns. Codebook: `InstlevelDataDoc<year>.doc`.
  - `Schools.xlsx`: despite the name, one row per institution *and sport* (~17,500-18,000/yr),
    128-129 columns. Codebook: `SchoolsDoc<year>.doc`.
  - **2019-20 sports file has 62,168 rows**: 44,556 of them have zero participants in every
    column (sports the school doesn't sponsor); only 17,612 have participants. Every other year
    lists sponsored sports only. **Decision (2026-11-09): `build_eada.py` drops sport rows with
    no participants in any column (`drop_unsponsored`)**, so in every year of the `sports` table a
    row is a sponsored team. The raw files are unchanged.
  - **2020-21 is small** (1,844 schools, 14,381 sport rows vs. ~2,050 / ~17,700 in other years):
    the COVID year. Don't read a drop from 2019-20 to 2020-21 as a trend without saying so.
  - Column drift: `opeid` added from 2021-22; `classification_name` is `classificationname` in
    2018-19 (mapped in `STANDARD_NAMES`); otherwise the same columns in every year.
- **TODO after downloading:**
  - [x] Record here which years are available and the exact file names and formats
  - [x] Read the codebooks (`.doc` in each year folder; same layout 2015-16 and 2024-25) and note
        what each file contains: see `column_notes.json`. The biggest trap: institution-level
        `il_revenue_*` / `il_expense_*` totals exclude football and basketball.
  - [x] Confirm whether the year label is the academic year's start or end
  - [ ] `uv run build_eada.py --inspect`, then fill in `FILE_TABLES` and `STANDARD_NAMES` in
        `build_eada.py`, the placeholders in `sql/views.sql`, and `column_notes.json`
- **Build:** `uv run build_eada.py` writes `data/eada.duckdb` and `data/eada_mappings.json`.

## eada_codebook.csv (data dictionary)

The variable tables of every codebook (`InstlevelDataDoc<year>.doc`, `SchoolsDoc<year>.doc`), one
row per year, file and variable: `survey_year, file, variable, label`. Extracted cell by cell with
Word (COM) on 2026-11-09, since the `.doc` files are old binary Word. Every data column has a label
and labels only differ cosmetically between years. `build_eada.py` uses the latest year's label as
the column description unless `column_notes.json` has a hand-written note. Codebook labels can
mislead: the `IL_*` revenue/expense "Total ... per Team" columns exclude football and basketball
(see the gotchas). Re-extract when adding years.

## nil_facts.csv (curated)

Aggregate NIL and revenue-sharing figures, hand-extracted with provenance. One row per figure.

| column | meaning |
|---|---|
| fact_id | stable id, e.g. `csc-2025-12-cleared-value` |
| source_org, report | who published the figure, and in what |
| source_type | official_body, industry_report, policy_document, news, estimate |
| retrieved_via | `primary` if read in the original report; otherwise where it was read |
| published | publication date of the report (from the document, not a site header) |
| period_covered | the period the figure describes |
| metric, sport, gender | what is measured, for whom |
| value_low, value_high | the figure (equal unless the source gives a range) |
| unit | percent, USD millions, deals, athletes |
| method_note | how the source produced it and what it does NOT measure |
| url | where it can be checked |
| verified_against_primary | `true` once checked in the original report |

The seed rows came from secondary coverage found during planning: verify each against the primary
report (Opendorse report PDFs, College Sports Commission releases), update `retrieved_via`,
`published` and `url`, and set `verified_against_primary` to `true`.

## sources.json

Domain → source type and typical access, used to rank search results and label findings.

## policy/

Primary policy documents for the Policy Researcher. See `policy/README.md`.

## column_notes.json

Hand-written notes from the EADA data dictionary: gotchas and column descriptions. Merged into
`eada_mappings.json` by the build.
