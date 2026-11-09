# Policy corpus

Primary documents the Policy Researcher searches (`policy.py`). One Markdown file per document,
with a header block, then `---`, then the text:

```
title: Title IX athletics regulation (34 CFR 106.41)
source_url: https://www.ecfr.gov/current/title-34/subtitle-B/chapter-I/part-106/subpart-D/section-106.41
published: <date of the version you saved>
retrieved: <date downloaded>
notes: <optional: anything a reader of a quote should know, e.g. OCR text>
---
<document text>
```

Save the text of the primary source, not a summary of it: evidence checks match the researcher's
quotes against this text. Files are UTF-8.

## Collected (retrieved 2026-11-09)

| File | Document | Source |
|---|---|---|
| `title-ix-statute-20-usc-1681` | Title IX statute | GPO, U.S. Code 2024 ed. (section text; editorial notes left out) |
| `title-ix-regulation-34-cfr-106-41` | 34 CFR 106.41 | eCFR, current as of 2026-11-05 |
| `title-ix-policy-interpretation-1979` | 1979 Policy Interpretation (three-part test), with footnotes | ed.gov page, saved from a browser by the user (ed.gov notes: "the policy is still current") |
| `title-ix-three-part-test-clarification-1996` | 1996 OCR Clarification: The Three-Part Test | ed.gov page, saved from a browser by the user |
| `ocr-nil-fact-sheet-2025-01-rescinded` | OCR fact sheet on Title IX and NIL, Jan. 2025, **rescinded** | ed.gov PDF, saved by the user; footnote markers written as [n] |
| `ocr-nil-guidance-rescission-2025-02-12` | ED press release rescinding that fact sheet, Feb. 12, 2025 | ed.gov page, saved from a browser by the user |
| `eada-statute-20-usc-1092g` | EADA, 20 U.S.C. 1092(g) | GPO, U.S. Code 2024 ed. (subsection (g) only) |
| `eada-users-guide-2026` | EADA survey User's Guide, Sept 2026 | Dept. of Education document, copy hosted by Garrett College |
| `house-v-ncaa-settlement-agreement-2025` | Fourth Amended Stipulation and Settlement Agreement (operative) | Court filing, Dkt. 958-1, via CourtListener |
| `house-v-ncaa-injunctive-relief-settlement-2025` | Appendix A (revenue-sharing pool, NIL, roster rules) + Appendix B (roster limits) | same filing; Appendix B decoded from a PDF with a shifted font map |
| `house-v-ncaa-final-approval-order-2025` | Final approval opinion, June 6, 2025 | Court filing, Dkt. 978, via CourtListener |
| `csc-rules-and-policies` | College Sports Commission rules (NIL, revenue sharing, roster limits, enforcement) | collegesportscommission.org, page as retrieved |
| `csc-memo-third-party-nil-rules-reminder-2026-01-09` | CSC memo | collegesportscommission.org |
| `csc-memo-third-party-nil-and-revenue-share-2026-04-07` | CSC memo | collegesportscommission.org |

## ed.gov documents

ed.gov returns 403 (Azure WAF) to scripts and to WebFetch, and per CLAUDE.md we don't bypass it
with mirrors or spoofed clients. The four ed.gov documents above were saved from a browser into
`data/raw/policy/` (gitignored) and converted: print headers/footers and site navigation removed,
wrapped lines re-joined. Do the same for any other ed.gov document.

**Status of Department of Education NIL / revenue-sharing guidance (checked 2026-11-09):** the
Jan. 2025 OCR fact sheet treating school NIL and revenue-sharing payments as athletic financial
assistance under Title IX was rescinded on Feb. 12, 2025 (date from the press release itself).
Searches found no replacement guidance since; re-check ed.gov's list of rescinded and archived
OCR publications before relying on this.
