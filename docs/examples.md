# Sample queries for graders

Three questions to ask the live app (or `POST /chat` with `{"message": "<question>"}`; the response is `{response, session_id, tool_calls}`). For each: the answer to expect, where it comes from, and the answers of three systems to the same question:

- **This agent**: the deployed pipeline.
- **Gemini, no tools**: the same model the agent's Lead and Writer use (Gemini 3.5 Flash), given only the question: no tools, no system prompt. What the model already knows about the domain.
- **Gemini + Google Search**: the same model with Google Search grounding.

Expected values are computed by SQL against the EADA database (`data/eada.duckdb`, built by `scripts/build_eada.py`; the SQL is in [`evals/e2e_cases.jsonl`](../evals/e2e_cases.jsonl)). Answers were generated with `uv run evals/compare_baseline.py` (`--no-search` for the bare model) on 2026-11-10. The second and third questions are held-out eval cases, never used to tune the agent.

| Question | Expected | This agent | Gemini, no tools | Gemini + Search |
|---|---|---|---|---|
| Basketball coaching staffs | 5.24 vs. 4.87, significant | 5.24 vs. 4.85, significant (t = 7.9) | "No statistically significant difference" (NCAA limits are equal) | "No difference"; a test is "inapplicable" |
| SEC schools spending ≥25% on women's teams, 2024-25 | 62.5% (10 of 16) | 62.5% (10 of 16) | "Not yet available" | 100% (all 16) |
| D-I women's volleyball expenses per team, 2018-19 → 2024-25 | +29.9% | +29.9% | "an estimated 35% to 60%" | +72–73% |

## 1. Is there a difference between coaching staff sizes between men's and women's college basketball? If so, is that difference statistically significant?

**Expected:** Yes. Division I basketball, 2024-25, the 356 schools with both a men's and a women's team: **5.24 coaches per men's team vs. 4.87 per women's team** (head + assistant). The difference is statistically significant (paired t-test on each school's difference, t ≈ 7.8). It's mostly assistants, and it's new: the gap was 0.06–0.15 coaches a year from 2015-16 to 2022-23 and widened after the NCAA allowed two more basketball coaches in 2023.

**Source:** EADA data file, table `sports` (columns `men_team_head_coaches_*`, `men_team_asst_coaches_*`, `women_team_head_coaches_*`, `women_team_asst_coaches_*`; full- and part-time combined), joined to `institutions` for `ncaa_division`; survey_year 2025 = 2024-25.

<details><summary>SQL for the expected values</summary>

```sql
-- D-I men's bball coaches/team 2024-25
SELECT round(avg(men_team_head_coaches_male + men_team_head_coaches_female + men_team_asst_coaches_male + men_team_asst_coaches_female), 2) FROM sports s JOIN institutions i USING (unitid, survey_year) WHERE s.survey_year = 2025 AND s.sports = 'Basketball' AND i.ncaa_division = 'I' AND s.partic_men IS NOT NULL AND s.partic_women IS NOT NULL;
-- D-I women's bball coaches/team 2024-25
SELECT round(avg(women_team_head_coaches_male + women_team_head_coaches_female + women_team_asst_coaches_male + women_team_asst_coaches_female), 2) FROM sports s JOIN institutions i USING (unitid, survey_year) WHERE s.survey_year = 2025 AND s.sports = 'Basketball' AND i.ncaa_division = 'I' AND s.partic_men IS NOT NULL AND s.partic_women IS NOT NULL;
```
</details>

| | This agent | Gemini, no tools | Gemini + Search |
|---|---|---|---|
| Expected numbers found | 2 of 2 | 0 of 2 | 0 of 2 |
| Sources | 5, each linked to the sentence it supports | none | 20 search results, not linked to sentences |

<details><summary><b>This agent</b></summary>

Yes, there is a difference in college basketball coaching staff sizes at NCAA Division I schools in 2024-25, with men's teams having slightly larger average staffs of 5.243 compared to 4.846 for women's teams [1]. While the head coach headcount difference of 0.0056 is not statistically significant (t-statistic of 1.4162), the difference of 0.3911 assistant coaches is statistically significant (t-statistic of 7.8739) [2].

```chart
{"type":"bar","title":"Average NCAA Division I Basketball Coaching Staff Size, 2024-25","unit":"count","labels":["Head Coaches","Assistant Coaches","Total Coaching Staff"],"series":[{"name":"Men's Basketball","values":[1.0,4.243,5.243]},{"name":"Women's Basketball","values":[0.994,3.852,4.846]}],"sources":["1"]}
```

##### Coaching Staff Differences
Across 358 NCAA Division I schools in the 2024-25 academic year, men's basketball programs employed 358 head coaches (all male) [3] and 1,519 assistant coaches (1,518 male, 1 female) [3]. Women's basketball programs employed 356 head coaches (110 male, 246 female) [3] and 1,379 assistant coaches (461 male, 918 female) [3]. 

##### Statistical Significance
A paired t-test comparing men's and women's basketball programs at each of the 358 Division I schools in 2024-25 shows [2]:
- **Head Coaches:** The mean difference of 0.0056 has a t-statistic of 1.4162, meaning the difference is not statistically significant [2].
- **Assistant Coaches:** The mean difference of 0.3911 has a t-statistic of 7.8739, meaning this difference is statistically significant [2].

##### Title IX and Coaching Limits
Under Title IX policy interpretations, coaching availability compliance is assessed by examining the relative availability of full-time, part-time, and graduate assistant coaches [4]. Compensation and assignment differences are evaluated based on non-discriminatory factors like participant counts and supervised assistant coaches [4]. Meanwhile, the NCAA Division I Council voted in January 2023 to support an increase of two coaches in both men's and women's basketball [5], which took effect July 1 [5].

##### Limits
- Verified findings do not specify exact numerical coach-to-athlete ratios or mandatory staff sizes required for compliance.
- EADA coaching headcounts do not separate coaches by full-time or part-time institutional status for individual sport teams.
- Proportionality is only one prong of Title IX's three-part test and does not by itself show compliance.

#### Sources

- **[1]** EADA database query <details><summary>SQL</summary><pre><code>SELECT &#10;  COUNT(&#42;) as school&#95;count,&#10;  AVG(COALESCE(men&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;head&#95;coaches&#95;female,0)) as avg&#95;men&#95;hd,&#10;  AVG(COALESCE(women&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;head&#95;coaches&#95;female,0)) as avg&#95;women&#95;hd,&#10;  AVG(COALESCE(men&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;asst&#95;coaches&#95;female,0)) as avg&#95;men&#95;asst,&#10;  AVG(COALESCE(women&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;asst&#95;coaches&#95;female,0)) as avg&#95;women&#95;asst,&#10;  AVG((COALESCE(men&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;head&#95;coaches&#95;female,0)) + (COALESCE(men&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;asst&#95;coaches&#95;female,0))) as avg&#95;men&#95;total,&#10;  AVG((COALESCE(women&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;head&#95;coaches&#95;female,0)) + (COALESCE(women&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;asst&#95;coaches&#95;female,0))) as avg&#95;women&#95;total&#10;FROM sports s&#10;JOIN institutions i ON s.unitid = i.unitid AND s.survey&#95;year = i.survey&#95;year&#10;WHERE s.survey&#95;year = 2025 &#10;  AND s.sports = &#x27;Basketball&#x27; &#10;  AND i.ncaa&#95;division = &#x27;I&#x27;</code></pre></details>
- **[2]** EADA database query <details><summary>SQL</summary><pre><code>WITH diffs AS (&#10;  SELECT &#10;    (COALESCE(men&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;head&#95;coaches&#95;female,0)) - &#10;    (COALESCE(women&#95;team&#95;head&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;head&#95;coaches&#95;female,0)) as hd&#95;diff,&#10;    (COALESCE(men&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(men&#95;team&#95;asst&#95;coaches&#95;female,0)) - &#10;    (COALESCE(women&#95;team&#95;asst&#95;coaches&#95;male,0) + COALESCE(women&#95;team&#95;asst&#95;coaches&#95;female,0)) as asst&#95;diff&#10;  FROM sports s&#10;  JOIN institutions i ON s.unitid = i.unitid AND s.survey&#95;year = i.survey&#95;year&#10;  WHERE s.survey&#95;year = 2025 &#10;    AND s.sports = &#x27;Basketball&#x27; &#10;    AND i.ncaa&#95;division = &#x27;I&#x27;&#10;)&#10;SELECT &#10;  COUNT(&#42;) as n,&#10;  AVG(hd&#95;diff) as mean&#95;hd&#95;diff,&#10;  STDDEV&#95;SAMP(hd&#95;diff) as std&#95;hd&#95;diff,&#10;  AVG(hd&#95;diff) / (STDDEV&#95;SAMP(hd&#95;diff) / SQRT(COUNT(&#42;))) as t&#95;stat&#95;hd,&#10;  AVG(asst&#95;diff) as mean&#95;asst&#95;diff,&#10;  STDDEV&#95;SAMP(asst&#95;diff) as std&#95;asst&#95;diff,&#10;  AVG(asst&#95;diff) / (STDDEV&#95;SAMP(asst&#95;diff) / SQRT(COUNT(&#42;))) as t&#95;stat&#95;asst&#10;FROM diffs</code></pre></details>
- **[3]** EADA database query <details><summary>SQL</summary><pre><code>SELECT &#10;  SUM(COALESCE(men&#95;team&#95;head&#95;coaches&#95;male, 0)) as men&#95;hd&#95;male,&#10;  SUM(COALESCE(men&#95;team&#95;head&#95;coaches&#95;female, 0)) as men&#95;hd&#95;female,&#10;  SUM(COALESCE(women&#95;team&#95;head&#95;coaches&#95;male, 0)) as women&#95;hd&#95;male,&#10;  SUM(COALESCE(women&#95;team&#95;head&#95;coaches&#95;female, 0)) as women&#95;hd&#95;female,&#10;  SUM(COALESCE(men&#95;team&#95;asst&#95;coaches&#95;male, 0)) as men&#95;asst&#95;male,&#10;  SUM(COALESCE(men&#95;team&#95;asst&#95;coaches&#95;female, 0)) as men&#95;asst&#95;female,&#10;  SUM(COALESCE(women&#95;team&#95;asst&#95;coaches&#95;male, 0)) as women&#95;asst&#95;male,&#10;  SUM(COALESCE(women&#95;team&#95;asst&#95;coaches&#95;female, 0)) as women&#95;asst&#95;female,&#10;  COUNT(&#42;) as school&#95;count&#10;FROM sports s&#10;JOIN institutions i ON s.unitid = i.unitid AND s.survey&#95;year = i.survey&#95;year&#10;WHERE s.survey&#95;year = 2025 &#10;  AND s.sports = &#x27;Basketball&#x27; &#10;  AND i.ncaa&#95;division = &#x27;I&#x27;</code></pre></details>
- **[4]** [A Policy Interpretation: Title IX and Intercollegiate Athletics (HEW, 44 Fed. Reg. 71413, Dec. 11, 1979)](https://www.ed.gov/laws-and-policy/civil-rights-laws/federal-register-notices-and-regulations/policy-interpretation-title-ix-and-intercollegiate-athletics): policy_document, published 1979-12-11, full access
- **[5]** [NCAA Division I Council modernizes rules for coaching limits](https://www.ncaa.com/news/ncaa/article/2023-01-11/ncaa-division-i-council-modernizes-rules-coaching-limits): official_body, published 2023-01-11, full access

</details>

<details><summary><b>Gemini, no tools, no system prompt</b></summary>

To answer your question accurately, we have to look at two different categories: **instructional (on-court) coaching staff** and **support staff** (such as directors of operations, video coordinators, and analysts). 

The short answer is: **No, there is no statistically significant difference in the size of the *instructional* coaching staffs** between men's and women's college basketball, primarily due to strict NCAA regulations. However, there can be slight differences in the size of **support staffs** at high-major programs, though even these are constrained by Title IX compliance.

Here is a detailed breakdown of the data, the rules, and the statistical reality.

---

##### 1. Instructional Coaching Staff (The "Countable" Coaches)
For instructional, on-court coaches, the staff sizes are **identical** because they are strictly capped by NCAA bylaws. 

* **The Rule:** Prior to 2023, the NCAA allowed 1 Head Coach and 3 Assistant Coaches (4 total) for both men’s and women’s Division I teams. 
* **The 2023 Change:** In January 2023 (effective July 2023), the NCAA Division I Council cleared the way for both men's and women's coaching staffs to expand. Both sports are now allowed **1 Head Coach and up to 5 Assistant Coaches (6 total)** who can perform on-court coaching. 
* **The Reality:** Because basketball is a highly competitive, high-resource sport, virtually 100% of Division I programs (both men's and women's) employ the maximum allowed limit of instructional coaches. 

**Statistical Significance:** Because the variance is virtually zero (almost every school has exactly the maximum allowed), there is **no statistically significant difference** in instructional staff sizes.

---

##### 2. Support Staff (Operations, Video, Analytics)
Where you historically saw differences—and where minor differences still exist—is in "support staff" roles. These are positions like Director of Basketball Operations (DOBO), Video Coordinator, Player Development Coordinator, and Graduate Assistants.

* **High-Major/Power 5 Schools:** At the power-conference level (SEC, Big Ten, ACC, etc.), men's basketball programs generate massive revenue. Historically, these athletic departments would use their budgets to hire larger support staffs for the men's team (e.g., having three video coordinators and two analytics directors, whereas the women's team might have had just one person doing both jobs).
* **Mid-Major/Low-Major Schools:** At smaller schools with tighter budgets, support staffs for both men's and women's programs are usually very small (often just one DOBO for each, or none at all), meaning there is almost no difference.

#### Is this difference statistically significant?
If you analyze the **total staff** (instructional + support staff) across all of Division I:
* **Across all ~350 DI schools:** The difference is **not statistically significant**. The vast majority of mid- and low-major schools have identical, bare-minimum support staffs for both genders due to budget constraints.
* **Within Power 5 Conference schools:** A T-test comparing total staff size might show a *slight* statistical significance favoring men's programs (typically by 1 to 2 staff members), but this gap has rapidly closed over the last decade.

---

##### The Legal Equalizer: Title IX
The main reason support staff sizes remain so close is **Title IX**. Under Title IX, athletic departments must provide "equivalence in other athletic benefits and opportunities." This includes the provision of coaching and academic tutoring. 

If a university provides its men's basketball team with 12 total staff members (6 coaches + 6 support staff) but only provides the women's team with 7 total staff members, they risk a Title IX lawsuit. To remain compliant, athletic directors generally ensure that if the men’s basketball staff expands, the women’s basketball staff expands proportionally.

---

##### The Real Difference: Compensation (Not Staff Size)
While the *size* of the coaching staffs is virtually identical, the **compensation** of those staffs is vastly different and highly statistically significant.

According to data from the *Equity in Athletics Disclosure Act (EADA)*:
* **Head Coach Salaries:** Men's head coaches make significantly more than women's head coaches, even at the same university. For example, in Power 5 conferences, men's head coaches often make $3 million to $8 million per year, while women's head coaches at the same schools typically make $800,000 to $2 million.
* **Assistant Coach Salary Pools:** The total budget allocated to pay the five assistant coaches is vastly larger for men's programs than women's programs. 

##### Summary
* **Instructional Staff Size:** No difference (strictly regulated by the NCAA at 6 coaches per team).
* **Support Staff Size:** Minor differences exist at wealthy schools, but across all of college basketball, the difference is **not statistically significant** due to Title IX regulations and budget ceilings.
* **Staff Salaries:** **Highly statistically significant** difference favoring men's coaching staffs.

</details>

<details><summary><b>Gemini + Google Search</b></summary>

To answer whether there is a difference in coaching staff sizes between men’s and women’s college basketball, we must look at it from two perspectives: **regulatory limits (NCAA rules)** and **actual staff sizes (including support staff).**

---

##### 1. Regulatory Coaching Limits (NCAA Rules)
* **The Difference:** **None.**
* **Statistical Significance:** **Not applicable (the numbers are structurally identical).**

Under NCAA bylaws, there is no difference in the permitted number of primary, "countable" coaches between men's and women's college basketball programs. 
* **Prior to July 2023:** Both men’s and women’s Division I programs were capped at **4 countable coaches** (1 head coach and 3 assistant coaches). 
* **Effective July 1, 2023:** The NCAA Division I Council modernized these rules, increasing the limit to **6 countable coaches** (1 head coach and 5 assistant coaches) for both men’s and women’s programs. (The two additional coaches are permitted to engage in on-court instruction but are not allowed to recruit off-campus).
* **Current Proposals (January 2026):** The NCAA Men’s and Women's Basketball Oversight Committees proposed package changes to allow *any* athletic department staff member to provide tactical and technical instruction to players, while strictly designating up to six staff members to recruit off-campus. Again, these proposals apply identically to both sports.

Because nearly all Division I programs maximize their allotted coaching slots, an empirical comparison of *countable assistant coaches* yields an average of exactly 5.0 per team for both genders—meaning there is **no difference, let alone a statistically significant one**, in official coach counts.

---

##### 2. Actual Staff Sizes (Including Support & Non-Coaching Personnel)
* **The Difference:** **Yes, men's programs typically feature larger actual staffs.**
* **Statistical Significance:** **It depends on how staff is defined, though officially reported coaching positions show no statistically significant difference.**

While the NCAA strictly limits "countable" coaches, modern college basketball programs employ extensive **support staff and non-coaching personnel**. This includes:
* Directors of Basketball Operations (DOBOs)
* Directors of Player Development / Player Personnel
* Video and Analytics Coordinators
* Graduate Assistants and Team Managers

#### **Why a difference exists:**
Men's college basketball programs generally operate with significantly higher revenue and vastly larger budgets than women's programs. Consequently, high-major men's programs can afford to build out massive support staffs of 10 to 12+ specialized, non-coaching individuals. In contrast, similarly situated women's programs must often operate with smaller support staffs (often 6 to 8 people), frequently requiring their personnel to wear multiple hats.

#### **Is the difference statistically significant?**
1. **For Paid Assistant Coaches (EADA Data):** Under the federal Equity in Athletics Disclosure Act (EADA), universities must report the number of full-time and part-time assistant coaches for their sports. Under Title IX’s "Support Services" provision, universities are legally required to provide "comparable" coaching staff sizes and administrative support to both genders. Because of this legal pressure, EADA data shows that the number of full-time and part-time assistant coaches is kept highly equal between men’s and women’s basketball at almost all institutions. Statistical tests (such as t-tests or ANOVAs) run on official assistant coach counts across NCAA divisions yield **no statistically significant difference** in staff size. 
2. **For Off-the-Court Support Staff:** While there is an observable, practical difference in the number of *non-coaching support staff* (especially at the power-conference level), **there is no definitive peer-reviewed study proving a statistically significant difference across the entirety of the NCAA.** This is primarily because EADA reports do not standardize the tracking of non-coaching staff (such as analytics coordinators or graphic designers), making it difficult to run a macro-level statistical analysis. However, regional institutional audits and perception surveys of athletic directors consistently show that men's programs are perceived as—and in practice are—allocated significantly more support staff and administrative personnel than their female counterparts.

</details>

---

## 2. In 2024-25, what share of SEC schools spent at least 25% of their team expenses on women's teams?

**Expected:** **62.5% (10 of 16 SEC schools)** in 2024-25. Shares range from 20.7% to 30.8% of team expenses.

**Source:** View `v_spending_by_gender` ([`sql/views.sql`](../sql/views.sql): per-team `expense_menall` / `expense_womenall` summed per school from the `sports` table; unallocated expenses excluded), joined to `conferences` (IPEDS) for `basketball_conference = 'Southeastern Conference'`; survey_year 2025.

<details><summary>SQL for the expected values</summary>

```sql
-- SEC share >= 25% women's expenses 2024-25
SELECT round(100.0 * avg((women_expense_pct >= 25)::int), 1) FROM v_spending_by_gender v JOIN conferences c USING (unitid, survey_year) WHERE v.survey_year = 2025 AND c.basketball_conference = 'Southeastern Conference';
```
</details>

| | This agent | Gemini, no tools | Gemini + Search |
|---|---|---|---|
| Expected numbers found | 1 of 1 | 0 of 1 | 0 of 1 |
| Sources | 3, each linked to the sentence it supports | none | 8 search results, not linked to sentences |

<details><summary><b>This agent</b></summary>

In 2024-25, the share of Southeastern Conference (SEC) schools spending at least 25% of their team expenses on women's teams depends on the expense category:
* For **allocated team total expenses**, 10 out of 16 schools (62.5%) reached this threshold [1].
* For **game-day operating expenses**, 13 out of 16 schools (81.25%) met or exceeded the 25% mark [1]. 

##### School-by-School Spending (2024-25)
* **Allocated Team Total Expenses**: The 10 SEC schools that allocated at least 25% of total team expenses to women's teams were Auburn University (26.4%), Louisiana State University (30.8%), Texas A&M University (27.7%), The University of Tennessee-Knoxville (26%), The University of Texas at Austin (25.2%), University of Florida (28.2%), University of Kentucky (27.5%), University of Oklahoma (27.2%), University of South Carolina (30.4%), and Vanderbilt University (29.1%) [2]. 
* **Game-Day Operating Expenses**: Game-day operating expenses are a subset of total expenses and cover lodging, meals, transportation, uniforms, equipment, and officials [3]. The 13 schools allocating at least 25% to women's teams were Auburn (42.1146%), LSU (38.3918%), Mississippi State (30.1792%), Texas A&M (29.4205%), Tennessee (33.822%), UT Austin (34.1476%), Arkansas (27.71%), Florida (29.1597%), Kentucky (35.1154%), Missouri (31.2098%), Oklahoma (34.8888%), South Carolina (42.357%), and Vanderbilt (33.749%) [2].

##### Limits
* EADA data lags real-time spending.
* The findings do not contain the exact line-by-line mechanical steps of how team-allocated recruiting expenses are combined into total expenses by team rather than being reported separately.
* Spending proportionality is only one prong of Title IX's three-part test and does not by itself show compliance.

#### Sources

- **[1]** EADA database query <details><summary>SQL</summary><pre><code>SELECT count(&#42;) AS total&#95;schools, sum(case when total&#95;expense&#95;women&#95;pct &gt;= 25 then 1 else 0 end) AS total&#95;exp&#95;at&#95;least&#95;25&#95;count, sum(case when opexp&#95;women&#95;pct &gt;= 25 then 1 else 0 end) AS opexp&#95;at&#95;least&#95;25&#95;count FROM (SELECT i.institution&#95;name, v.women&#95;expense&#95;pct AS total&#95;expense&#95;women&#95;pct, (SELECT sum(s.opexp&#95;womenall) &#42; 100.0 / nullif(sum(s.opexp&#95;womenall + s.opexp&#95;menall), 0) FROM sports s WHERE s.unitid = i.unitid AND s.survey&#95;year = 2025) AS opexp&#95;women&#95;pct FROM institutions i JOIN conferences c ON i.unitid = c.unitid AND i.survey&#95;year = c.survey&#95;year JOIN v&#95;spending&#95;by&#95;gender v ON i.unitid = v.unitid AND i.survey&#95;year = v.survey&#95;year WHERE i.survey&#95;year = 2025 AND c.basketball&#95;conference = &#x27;Southeastern Conference&#x27;)</code></pre></details>
- **[2]** EADA database query <details><summary>SQL</summary><pre><code>SELECT i.institution&#95;name, v.women&#95;expense&#95;pct AS total&#95;expense&#95;women&#95;pct, (SELECT sum(s.opexp&#95;womenall) &#42; 100.0 / nullif(sum(s.opexp&#95;womenall + s.opexp&#95;menall), 0) FROM sports s WHERE s.unitid = i.unitid AND s.survey&#95;year = 2025) AS opexp&#95;women&#95;pct FROM institutions i JOIN conferences c ON i.unitid = c.unitid AND i.survey&#95;year = c.survey&#95;year JOIN v&#95;spending&#95;by&#95;gender v ON i.unitid = v.unitid AND i.survey&#95;year = v.survey&#95;year WHERE i.survey&#95;year = 2025 AND c.basketball&#95;conference = &#x27;Southeastern Conference&#x27; ORDER BY i.institution&#95;name</code></pre></details>
- **[3]** [User's Guide for the Equity in Athletics Disclosure Act Web-Based Data Collection (U.S. Dept. of Education, OPE)](https://www.garrettcollege.edu/images/disclosures/eada-users-guide.pdf): policy_document, published 2026-09, full access

</details>

<details><summary><b>Gemini, no tools, no system prompt</b></summary>

Data for the **2024–25 academic year** is not yet available. 

College athletic departments report their financial data—including expenses allocated to men's and women's teams—under the **Equity in Athletics Disclosure Act (EADA)**. The reporting timeline for these figures is as follows:
* **The fiscal/academic year** for 2024–25 does not conclude until the summer of 2025.
* **Filing deadline:** Schools are required to submit their EADA data to the U.S. Department of Education in October of the following academic year (October 2025).
* **Public release:** The Department of Education typically processes and publicly releases this data in **late 2025 or early 2026**.

##### Historical Context and Expectations
While the official 2024–25 data is pending, historical EADA data offers a strong indication of where SEC schools stand:
* **The 16 SEC Schools for 2024–25:** Alabama, Arkansas, Auburn, Florida, Georgia, Kentucky, LSU, Mississippi State, Missouri, Oklahoma, Ole Miss, South Carolina, Tennessee, Texas, Texas A&M, and Vanderbilt. 
* **Typical Expense Share:** Historically, **100% of SEC schools** spend at least 25% of their gender-allocated team expenses on women's teams. Because football programs in the SEC require massive budgets (often accounting for 50% to 60% of a department's total men's expenses), men's sports command the majority of athletic spending. However, to maintain Title IX compliance regarding athletic participation opportunities and financial aid, SEC athletic departments generally allocate between **30% and 45%** of their total gender-allocated operating expenses to women's sports. 

Once the Department of Education releases the official EADA database for the 2024–25 reporting cycle, the exact breakdown for all 16 SEC schools can be calculated using their individual EADA custom reports.

</details>

<details><summary><b>Gemini + Google Search</b></summary>

In the **2024-25 fiscal year**, **100% (all 16)** of the SEC member schools spent at least 25% of their gender-allocated team expenses on women's athletic programs. 

##### The Regulatory and Financial Context
Under the **Equity in Athletics Disclosure Act (EADA)** and **Title IX**, coeducational colleges must disclose and report their athletic department participation, revenues, and expenses. To comply with Title IX’s requirements regarding equity in scholarships, participation opportunities, and general treatment, schools must make substantial investments in women's sports. 

While SEC football budgets are massive (often exceeding $50 million to $80 million per year) and men's basketball budgets are similarly robust, the vast array of sponsored women's sports—including high-resource programs like women’s basketball, gymnastics, softball, soccer, and rowing—ensures that women's programs consistently receive **between 25% and 35%** of a school's total gender-allocated team expenses. 

##### Concrete Example: University of Kentucky (2024-25 EADA)
To illustrate how the math works for a typical SEC athletic department during the 2024-25 academic year, the **University of Kentucky’s** official 2024-25 EADA report lists the following gender-allocated team expenditures:
* **Total allocated to Men's Teams:** $80,457,668
* **Total allocated to Women's Teams:** $30,470,380
* **Total Gender-Allocated Team Expenses:** $110,928,048

Based on these figures, Kentucky allocated **27.46%** of its team-specific expenses directly to women's sports, putting it comfortably above the 25% threshold. 

This trend remains consistent across all 16 SEC institutions (which officially included newcomers Oklahoma and Texas for the 2024-25 season), with every department dedicating at least 25% of their sport-allocated team budgets to female athletes.

</details>

---

## 3. How did average total expenses per team for Division I women's volleyball change from 2018-19 to 2024-25?

**Expected:** Average total expenses per Division I women's volleyball team rose **from $1,059,368 in 2018-19 to $1,376,183 in 2024-25, up 29.9%** (332 teams, then 340).

**Source:** EADA table `sports` (`expense_womenall` where `sports = 'Volleyball'`), joined to `institutions` for `ncaa_division = 'I'`; survey_years 2019 and 2025.

<details><summary>SQL for the expected values</summary>

```sql
-- D-I women's volleyball avg expenses 2018-19
SELECT avg(s.expense_womenall) FROM sports s JOIN institutions i USING (unitid, survey_year) WHERE s.survey_year = 2019 AND s.sports = 'Volleyball' AND i.ncaa_division = 'I' AND s.partic_women IS NOT NULL;
-- D-I women's volleyball avg expenses 2024-25
SELECT avg(s.expense_womenall) FROM sports s JOIN institutions i USING (unitid, survey_year) WHERE s.survey_year = 2025 AND s.sports = 'Volleyball' AND i.ncaa_division = 'I' AND s.partic_women IS NOT NULL;
```
</details>

| | This agent | Gemini, no tools | Gemini + Search |
|---|---|---|---|
| Expected numbers found | 2 of 2 | 0 of 2 | 1 of 2 |
| Sources | 3, each linked to the sentence it supports | none | 12 search results, not linked to sentences |

<details><summary><b>This agent</b></summary>

Average total expenses per team for Division I women's volleyball increased by 29.91% between 2018-19 and 2024-25, rising from $1,059,367.62 to $1,376,183.22 [1]. Except for a decline in 2020-21, average total expenses grew steadily each year [2]. During this same period, the number of Division I schools sponsoring women's volleyball expanded from 332 to 340 [3].

```chart
{"type":"line","title":"Average Total Expenses per Division I Women's Volleyball Team, 2018-19 to 2024-25","unit":"$","labels":["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25"],"series":[{"name":"Average Total Expenses","values":[1059367.6205,1062005.0422,925864.7743,1110432.4701,1228852.0744,1308558.358,1376183.2176]}],"sources":["3"]}
```

##### Expense Trends and Sponsorship
* **Year-over-Year Growth**: Average total expenses per team grew by 0.25% in 2019-20, fell by 12.82% in 2020-21, and rebounded with increases of 19.93% in 2021-22, 10.66% in 2022-23, 6.49% in 2023-24, and 5.17% in 2024-25 [2].
* **Game-Day Operating Expenses**: Average game-day operating expenses per team increased by 37.43% over the period, growing from $193,658.66 in 2018-19 to $266,144.14 in 2024-25 [1].
* **Sponsorship Numbers**: The total number of NCAA Division I schools sponsoring women's volleyball was 332 in 2018-19 and 2019-20, dropped to 319 in 2020-21, and then steadily increased to 334 in 2021-22, 336 in 2022-23, 338 in 2023-24, and 340 in 2024-25 [3].

##### Limits
* EADA data lags real-time athletic department spending.

#### Sources

- **[1]** EADA database query <details><summary>SQL</summary><pre><code>WITH yearly AS (SELECT survey&#95;year, avg(expense&#95;womenall) AS avg&#95;exp, avg(opexp&#95;womenall) AS avg&#95;opexp FROM sports JOIN institutions USING (unitid, survey&#95;year) WHERE sports = &#x27;Volleyball&#x27; AND institutions.ncaa&#95;division = &#x27;I&#x27; AND survey&#95;year IN (2019, 2025) GROUP BY survey&#95;year), base AS (SELECT max(CASE WHEN survey&#95;year = 2019 THEN avg&#95;exp END) AS exp&#95;2019, max(CASE WHEN survey&#95;year = 2025 THEN avg&#95;exp END) AS exp&#95;2025, max(CASE WHEN survey&#95;year = 2019 THEN avg&#95;opexp END) AS opexp&#95;2019, max(CASE WHEN survey&#95;year = 2025 THEN avg&#95;opexp END) AS opexp&#95;2025 FROM yearly) SELECT exp&#95;2019, exp&#95;2025, (exp&#95;2025 - exp&#95;2019) / exp&#95;2019 &#42; 100 AS total&#95;exp&#95;pct&#95;change, opexp&#95;2019, opexp&#95;2025, (opexp&#95;2025 - opexp&#95;2019) / opexp&#95;2019 &#42; 100 AS total&#95;opexp&#95;pct&#95;change FROM base</code></pre></details>
- **[2]** EADA database query, survey years 2019-2025 <details><summary>SQL</summary><pre><code>WITH yearly AS (SELECT survey&#95;year, count(&#42;) AS schools, avg(expense&#95;womenall) AS avg&#95;exp, avg(opexp&#95;womenall) AS avg&#95;opexp FROM sports JOIN institutions USING (unitid, survey&#95;year) WHERE sports = &#x27;Volleyball&#x27; AND institutions.ncaa&#95;division = &#x27;I&#x27; AND survey&#95;year BETWEEN 2019 AND 2025 GROUP BY survey&#95;year), with&#95;pct AS (SELECT survey&#95;year, schools, avg&#95;exp, avg&#95;opexp, (avg&#95;exp - lag(avg&#95;exp, 1) OVER (ORDER BY survey&#95;year)) / lag(avg&#95;exp, 1) OVER (ORDER BY survey&#95;year) &#42; 100 AS exp&#95;yoy&#95;pct, (avg&#95;opexp - lag(avg&#95;opexp, 1) OVER (ORDER BY survey&#95;year)) / lag(avg&#95;opexp, 1) OVER (ORDER BY survey&#95;year) &#42; 100 AS opexp&#95;yoy&#95;pct FROM yearly) SELECT survey&#95;year, schools, avg&#95;exp, exp&#95;yoy&#95;pct, avg&#95;opexp, opexp&#95;yoy&#95;pct FROM with&#95;pct ORDER BY survey&#95;year</code></pre></details>
- **[3]** EADA database query, survey years 2019-2025 <details><summary>SQL</summary><pre><code>SELECT survey&#95;year, count(&#42;) AS schools, sum(expense&#95;womenall) AS total&#95;expenses, avg(expense&#95;womenall) AS avg&#95;expenses, sum(opexp&#95;womenall) AS total&#95;opexp, avg(opexp&#95;womenall) AS avg&#95;opexp FROM sports JOIN institutions USING (unitid, survey&#95;year) WHERE sports = &#x27;Volleyball&#x27; AND institutions.ncaa&#95;division = &#x27;I&#x27; AND survey&#95;year BETWEEN 2019 AND 2025 GROUP BY survey&#95;year ORDER BY survey&#95;year</code></pre></details>

</details>

<details><summary><b>Gemini, no tools, no system prompt</b></summary>

Because official financial data compiled under the **Equity in Athletics Disclosure Act (EADA)** by the U.S. Department of Education typically lags by one to two years, fully audited and finalized national average expense data specifically for the **2024–25** academic year is not yet fully published. 

However, looking at the trajectory from the **2018–19** season through the most recent fully reported cycles (2022–23 and 2023–24) and factoring in current budget projections for 2024–25, **average total expenses per team for Division I women’s volleyball have surged dramatically—increasing by an estimated 35% to 60% overall**, with elite programs seeing budget increases of 100% or more.

Here is a breakdown of how these expenses have changed, the estimated average figures, and the primary drivers behind this massive financial shift.

---

##### 1. Estimated Shift in Average Expenses

*   **2018–19 Season:**
    *   **Overall Division I Average:** The average total expense for a DI women's volleyball team was roughly **$850,000 to $1.1 million**.
    *   **Power Conference (then-Power 5) Average:** Programs in conferences like the Big Ten, Pac-12, and Big 12 averaged between **$1.8 million and $2.5 million**.
    *   **Mid-Major average:** Many smaller Division I schools operated on budgets of **$400,000 to $700,000**.
*   **2024–25 Season (Projected / Estimated):**
    *   **Overall Division I Average:** Estimated to have risen to **$1.3 million to $1.7 million**.
    *   **Power Conference (Autonomy 4) Average:** Due to hyper-inflation in collegiate sports spending, top-tier programs now frequently exceed **$3.5 million to $5 million** in total annual expenses. 
    *   **Mid-Major average:** Estimated at **$650,000 to $1 million**.

---

##### 2. Key Drivers of Rising Expenses (2018–19 vs. 2024–25)

The business model of Division I athletics changed more between 2019 and 2025 than in the previous three decades combined. The primary drivers behind the spike in women’s volleyball expenses include:

#### A. Conference Realignment and Travel Costs
In 2018–19, conference schedules were highly regionalized. By 2024–25, massive realignment (e.g., USC, UCLA, Oregon, and Washington joining the Big Ten; Stanford and Cal joining the ACC) forced teams to travel coast-to-coast regularly. 
*   To minimize missed class time and the physical toll on athletes, athletic departments are spending heavily on **charter flights** and premium lodging, which can easily double or triple a team's annual travel budget.

#### B. Coaching Salaries and Support Staff
The market for top-tier women’s volleyball coaches has exploded. 
*   In 2018–19, only a select few head coaches made over $300,000. 
*   By 2024–25, elite head coaches at powerhouse programs command salaries between **$500,000 and $800,000+**. 
*   Additionally, programs have expanded their budgets for full-time assistant coaches, technical/video coordinators, directors of operations, and dedicated physical therapists.

#### C. *Alston* Payments and Direct Athlete Benefits
Following the Supreme Court’s 2021 *NCAA v. Alston* ruling, universities are permitted to provide student-athletes with up to **$5,980 per year in direct academic incentive payments**. For a typical women’s volleyball roster of 15 to 18 players, this adds nearly **$100,000** in direct expenses to the sport's ledger that did not exist in 2018–19.

#### D. Popularity, Promotion, and Production Costs
Women's volleyball has experienced unprecedented growth in popularity (highlighted by Nebraska's 92,003-attendance "Volleyball Day" in 2023 and record-breaking TV viewership). 
*   To capitalize on this, athletic departments have drastically increased spending on **marketing, high-quality broadcast/streaming production, social media staff, and venue enhancements** (such as specialized taraflex flooring and arena lighting).

#### E. General Inflation and Recruiting
Post-pandemic inflation heavily impacted the cost of athletic gear, equipment, hotels, and team meals. Furthermore, as the game has grown globally, recruiting budgets have expanded significantly to allow coaches to scout internationally.

##### Summary
If you look at a typical Division I women’s volleyball team, the cost of running a program in 2024–25 is vastly higher than it was in 2018–19. While mid-major programs have had to increase spending to keep pace, the gap has widened, with power-conference programs nearly doubling their budgets to accommodate cross-country travel, premium coaching staffs, and enhanced athlete amenities.

</details>

<details><summary><b>Gemini + Google Search</b></summary>

From the 2018-19 season to the 2024-25 season, the average total expenses per team for NCAA Division I women’s volleyball experienced a dramatic increase, rising by approximately **72% to 73%**. 

The financial landscape of Division I women's volleyball shifted over this period in several notable ways:

##### 1. The 2018-19 Financial Baseline
* **Sponsoring Programs:** In 2018-19, there were **330** NCAA member institutions sponsoring Division I women's volleyball.
* **Aggregate Spending:** According to federal Equity in Athletics Disclosure Act (EADA) data, the combined revenue/expenses reported for Division I women's volleyball was **$267 million**. (Under EADA reporting, revenues are typically listed as equal to expenses for non-revenue sports).
* **Average (Mean) Expenses per Team:** Dividing the aggregate spending by the number of programs yields an average of approximately **$804,000 to $809,000** per team.

##### 2. The 2024-25 Financial Landscape
* **Sponsoring Programs:** By 2024-25, the number of Division I women's volleyball programs had grown to **344**.
* **Average (Mean) Expenses per Team:** The average total expenses per Division I program rose to **$1.39 million**.
  * *Operational Breakdown:* Of this $1.39 million, roughly **$906,000** went to non-scholarship operational expenses (such as coaching salaries, travel, food, and gear), while the remaining **$484,000** went directly to student-athlete scholarships and financial aid.
* **Median Expenses per Team:** The overall median budget for a Division I women’s volleyball program was **$1.1 million**. 
  * *Note on the Discrepancy:* The difference between the mean ($1.39 million) and the median ($1.1 million) is due to a highly right-skewed distribution, driven by elite, high-spending powerhouse programs. For example, the University of Nebraska reported over **$7 million** in total operating expenses, and programs like Texas and Wisconsin routinely spent in the **$4 million to $6 million** range. 

##### Key Drivers of the Budget Increases
* **Explosion in Popularity and Media Rights:** Over this period, women's volleyball became one of the fastest-growing spectator sports in the U.S., setting multiple indoor attendance records. The 2023 National Championship match on ABC averaged **1.7 million viewers** (a 115% increase over the previous year). This growth prompted athletic departments to heavily increase their financial investments.
* **Rising Salaries and Travel Costs:** Head coaching salaries and team travel budgets grew significantly to accommodate nationally competitive travel and recruiting schedules.
* **Preparation for the House Settlement (Transition to Equivalency Sport):** Through the 2024-25 season, Division I women’s volleyball was a "headcount" sport capped at 12 full scholarships per team. However, the landmark *House v. NCAA* settlement—which officially went into effect in 2025—changed the rules, allowing schools to expand to 18 rostered athletes and offer partial scholarships. Universities began expanding their program budgets during the 2024-25 cycle in anticipation of this roster and scholarship expansion.

</details>

---
