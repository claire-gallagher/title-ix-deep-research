# Data

## EADA (Equity in Athletics Disclosure Act)

- **Source:** U.S. Department of Education, Office of Postsecondary Education,
  https://ope.ed.gov/athletics/#/datafile/list
- **Download:** one folder per year into `data/raw/eada/<year>/` (gitignored), e.g.
  `data/raw/eada/2023/<files>`. Use the folder name for the year the files are labelled with.
- **TODO after downloading:**
  - [ ] Record here which years are available and the exact file names and formats
  - [ ] Save the data dictionary (or its link) here and note what each file contains
  - [ ] Confirm whether the year label is the academic year's start or end
  - [ ] `uv run build_eada.py --inspect`, then fill in `FILE_TABLES` and `STANDARD_NAMES` in
        `build_eada.py`, the placeholders in `sql/views.sql`, and `column_notes.json`
- **Build:** `uv run build_eada.py` writes `data/eada.duckdb` and `data/eada_mappings.json`.

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
