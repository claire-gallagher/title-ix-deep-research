-- Derived views, run by build_eada.py after the raw tables load. The Data Researcher queries these
-- instead of re-deriving tricky metrics each time. Column meanings come from the EADA codebooks
-- (InstlevelDataDoc<year>.doc, SchoolsDoc<year>.doc in data/raw/eada/<year>/) and were checked
-- against the data for 2015-16..2024-25.

-- Proportionality (prong 1 of the three-part test): female share of undergraduates vs. female
-- share of athletes. gap_pp > 0 means women are underrepresented among athletes.
-- Enrollment (ef*count) is full-time undergraduates, the population EADA reports (20 U.S.C. 1092(g)).
-- Athletes are the UNDUPLICATED counts (one athlete on three teams counts once), including co-ed
-- team members, not the per-team participation sums.
CREATE OR REPLACE VIEW v_proportionality AS
SELECT
    unitid,
    institution_name,
    survey_year,
    effemalecount AS female_undergrads,
    eftotalcount AS total_undergrads,
    undup_ct_partic_women AS female_athletes,
    undup_ct_partic_men + undup_ct_partic_women AS total_athletes,
    round(100.0 * effemalecount / nullif(eftotalcount, 0), 1) AS female_undergrad_pct,
    round(100.0 * undup_ct_partic_women / nullif(undup_ct_partic_men + undup_ct_partic_women, 0), 1) AS female_athlete_pct,
    round(
        100.0 * effemalecount / nullif(eftotalcount, 0)
        - 100.0 * undup_ct_partic_women / nullif(undup_ct_partic_men + undup_ct_partic_women, 0), 1
    ) AS gap_pp
FROM institutions;

-- Spending by gender: total and per athlete, so schools of different sizes compare fairly.
-- Summed from the SPORT rows, not the institution file: the institution file's il_expense_* and
-- il_revenue_* totals EXCLUDE football and basketball (they match the sport rows minus those two
-- for every school-year), so they would understate men's spending at most schools.
-- expense_menall / expense_womenall include co-ed team members of that sex.
-- Expenses not allocated to a team (facilities, administration...) are in unallocated_expenses,
-- not in either gender's figure: men_expenses + women_expenses + unallocated = grand total.
CREATE OR REPLACE VIEW v_spending_by_gender AS
WITH team AS (
    SELECT unitid, survey_year,
           sum(expense_menall) AS men_expenses,
           sum(expense_womenall) AS women_expenses
    FROM sports
    GROUP BY unitid, survey_year
)
SELECT
    i.unitid,
    i.institution_name,
    i.survey_year,
    t.men_expenses,
    t.women_expenses,
    round(t.women_expenses / nullif(t.men_expenses + t.women_expenses, 0) * 100, 1) AS women_expense_pct,
    round(t.men_expenses / nullif(i.undup_ct_partic_men, 0)) AS men_expense_per_athlete,
    round(t.women_expenses / nullif(i.undup_ct_partic_women, 0)) AS women_expense_per_athlete,
    i.tot_expense_all_notalloc AS unallocated_expenses,
    i.grnd_total_expense AS grand_total_expenses
FROM institutions i
JOIN team t USING (unitid, survey_year);

-- Ideas for more views: recruiting spend by gender (recruitexp_*), athletic aid by gender
-- (studentaid_*), head-coach salary by team gender (hdcoach_salary_*), year-over-year change per
-- school, conference rollups (needs a conference source; EADA has only classification_name).
