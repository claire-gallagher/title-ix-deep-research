-- The two tables the agents query, cut down from the raw EADA files (raw_institutions: 169
-- columns, raw_sports: 130) to what answers questions, so the Data Researcher picks from ~75
-- columns instead of ~300. Run by build_eada.py after loading; the raw_ tables are dropped after.
-- Kept columns keep their raw names, so they match the codebook (data/eada_codebook.csv).
--
-- Left out on purpose (all recoverable from data/raw and the codebook):
-- - institution-level il_* columns: sums of the sport rows, and the il_ revenue/expense totals
--   EXCLUDE football and basketball despite their labels. Sum `sports` instead.
-- - coach breakdowns by full/part-time and university-employee status: collapsed below into
--   coaches by team gender x coach sex.
-- - per-participant / per-team averages, co-ed-only money columns, *_ratio columns: derivable.
-- - addresses, zip, opeid, codes duplicating a name (sector_cd, classificationcode, sportscode),
--   and the institution columns repeated on every sport row (join on unitid, survey_year).

CREATE TABLE institutions AS
SELECT
    survey_year, unitid, institution_name, city_txt, state_cd, classification_name, sector_name,
    -- full-time undergraduates
    efmalecount, effemalecount, eftotalcount,
    -- athletes, each counted once however many teams they're on (incl. co-ed teams)
    undup_ct_partic_men, undup_ct_partic_women,
    -- athletically related student aid and recruiting, by team gender (only reported per school)
    studentaid_men, studentaid_women, studentaid_coed, studentaid_total,
    recruitexp_men, recruitexp_women, recruitexp_coed, recruitexp_total,
    -- coach salaries, by team gender (only reported per school): average per coach, average per
    -- full-time equivalent, and the number of coaches the average covers
    hdcoach_salary_men, hdcoach_salary_women, hdcoach_salary_coed,
    hdcoach_sal_fte_men, hdcoach_sal_fte_womn, hdcoach_sal_fte_coed,
    num_hdcoach_men, num_hdcoach_women, num_hdcoach_coed,
    ascoach_salary_men, ascoach_salary_women, ascoach_salary_coed,
    ascoach_sal_fte_men, ascoach_sal_fte_womn, ascoach_sal_fte_coed,
    num_ascoach_men, num_ascoach_women, num_ascoach_coed,
    -- money not assigned to any team, and the all-in totals (= sum of sport rows + not allocated)
    tot_revenue_all_notalloc, tot_expense_all_notalloc, grnd_total_revenue, grnd_total_expense
FROM raw_institutions;

CREATE TABLE sports AS
SELECT
    survey_year, unitid, institution_name, sports,
    -- participants; partic_men / partic_women are NULL when there's no team of that sex
    partic_men, partic_women, partic_coed_men, partic_coed_women,
    sum_partic_men, sum_partic_women,            -- incl. co-ed members of that sex
    -- money, incl. co-ed members of that sex (0, not NULL, when there's no team)
    revenue_menall, revenue_womenall, total_revenue_all,
    expense_menall, expense_womenall, total_expense_all,
    -- game-day operating expenses, a subset of expenses. The raw *_opexpperteam_* columns are the
    -- team's total (one team per sex per sport row), renamed so "per team" doesn't read as an average.
    sum_opexpperteam_men AS opexp_menall,
    sum_opexpperteam_women AS opexp_womenall,
    total_opexp_inclcoed AS total_opexp_all,
    -- coaches by team gender x coach sex. Full-time + part-time = the reported total in every row
    -- but 7 of ~226,000 (school reporting errors). The raw "university employee" columns describe
    -- the same coaches' employment, not extra coaches, so they aren't added.
    coalesce(men_ftheadcoach_male, 0) + coalesce(men_ptheadcoach_male, 0) AS men_team_head_coaches_male,
    coalesce(men_ftheadcoach_fem, 0) + coalesce(men_ptheadcoach_fem, 0) AS men_team_head_coaches_female,
    coalesce(women_fthdcoach_male, 0) + coalesce(women_pthdcoach_male, 0) AS women_team_head_coaches_male,
    coalesce(women_fthdcoach_fem, 0) + coalesce(women_pthdcoach_fem, 0) AS women_team_head_coaches_female,
    coalesce(coed_fthdcoach_male, 0) + coalesce(coed_pthdcoach_male, 0) AS coed_team_head_coaches_male,
    coalesce(coed_fthdcoach_fem, 0) + coalesce(coed_pthdcoach_fem, 0) AS coed_team_head_coaches_female,
    coalesce(men_ftascoach_male, 0) + coalesce(men_ptascoach_male, 0) AS men_team_asst_coaches_male,
    coalesce(men_ftasstcoach_fem, 0) + coalesce(men_ptasstcoach_fem, 0) AS men_team_asst_coaches_female,
    coalesce(women_ftascoach_male, 0) + coalesce(women_ptascoach_male, 0) AS women_team_asst_coaches_male,
    coalesce(women_ftastcoach_fem, 0) + coalesce(women_ptastcoach_fem, 0) AS women_team_asst_coaches_female,
    coalesce(coed_ftascoach_male, 0) + coalesce(coed_ptascoach_male, 0) AS coed_team_asst_coaches_male,
    coalesce(coed_ftastcoach_fem, 0) + coalesce(coed_ptastcoach_fem, 0) AS coed_team_asst_coaches_female
FROM raw_sports;

DROP TABLE raw_institutions;
DROP TABLE raw_sports;
