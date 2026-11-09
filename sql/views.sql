-- Derived views, run by build_eada.py after the raw tables load. The Data Researcher queries these
-- instead of re-deriving tricky metrics each time.
--
-- TODO: every column name below in <ANGLE_BRACKETS> is a placeholder. Replace it with the real
-- column from `uv run build_eada.py --inspect` and the EADA data dictionary, then remove this note.
-- build_eada.py skips this file while placeholders remain, so the build still works before then.

-- Proportionality (prong 1 of the three-part test): female share of undergraduates vs. female
-- share of athletes. gap_pp > 0 means women are underrepresented among athletes.
CREATE OR REPLACE VIEW v_proportionality AS
SELECT
    unitid,
    institution_name,
    survey_year,
    <FEMALE_UNDERGRAD_COL> AS female_undergrads,
    <TOTAL_UNDERGRAD_COL> AS total_undergrads,
    <FEMALE_ATHLETES_UNDUP_COL> AS female_athletes,      -- unduplicated count, not sport-level sums
    <TOTAL_ATHLETES_UNDUP_COL> AS total_athletes,
    round(100.0 * <FEMALE_UNDERGRAD_COL> / nullif(<TOTAL_UNDERGRAD_COL>, 0), 1) AS female_undergrad_pct,
    round(100.0 * <FEMALE_ATHLETES_UNDUP_COL> / nullif(<TOTAL_ATHLETES_UNDUP_COL>, 0), 1) AS female_athlete_pct,
    round(
        100.0 * <FEMALE_UNDERGRAD_COL> / nullif(<TOTAL_UNDERGRAD_COL>, 0)
        - 100.0 * <FEMALE_ATHLETES_UNDUP_COL> / nullif(<TOTAL_ATHLETES_UNDUP_COL>, 0), 1
    ) AS gap_pp
FROM institutions;

-- Spending by gender: total and per participant, so schools of different sizes compare fairly.
CREATE OR REPLACE VIEW v_spending_by_gender AS
SELECT
    unitid,
    institution_name,
    survey_year,
    <MEN_EXPENSES_COL> AS men_expenses,
    <WOMEN_EXPENSES_COL> AS women_expenses,
    round(<WOMEN_EXPENSES_COL> / nullif(<MEN_EXPENSES_COL> + <WOMEN_EXPENSES_COL>, 0) * 100, 1) AS women_expense_pct,
    round(<MEN_EXPENSES_COL> / nullif(<MEN_ATHLETES_UNDUP_COL>, 0)) AS men_expense_per_athlete,
    round(<WOMEN_EXPENSES_COL> / nullif(<FEMALE_ATHLETES_UNDUP_COL>, 0)) AS women_expense_per_athlete
FROM institutions;

-- TODO ideas: recruiting spend by gender, athletic aid by gender, head-coach salary by team gender,
-- year-over-year change per school, conference rollups.
