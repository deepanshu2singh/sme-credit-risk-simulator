# SME Open Banking Credit Risk Simulator — The Complete Build Book

*Author: Deepanshu Singh · A from-scratch guide to building, explaining, and deploying the project.*

This is the whole thing: what you're building, why it matters, which tool does
what and where, the concepts you must be able to defend in an interview, and the
code for every phase — with the reasoning behind each decision. Work through it
top to bottom the first time; use the cheat sheet at the end afterwards.

---

## 0. What you're building (the one-page pitch)

A **credit-risk analytics pipeline** that underwrites small businesses from their
**bank-account data** instead of their filed accounts.

You generate a realistic synthetic dataset of **500 UK SMEs** with **~435,000
current-account transactions** over two years, model it in a **SQL database**,
compute the **credit metrics a lender actually uses** (debt-service coverage,
cash runway, cash-flow volatility, overdraft behaviour), and then run a
**portfolio stress test** — "if revenue fell 20% across the book, how many of
these businesses run out of cash within a year?" — surfaced through an
**interactive dashboard** with a live stress slider.

**Why it lands with hiring managers.** It hits three things they rarely see
together in a portfolio project:

1. **You understand real financial data.** The single most important design
   decision (below) proves you've seen what bank data actually looks like.
2. **You speak the language of credit.** DSCR, runway, covenants, operating
   leverage, stress testing — the vocabulary of a commercial-banking or
   decision-analytics team.
3. **You built the whole stack.** Data engineering (SQL model with enforced
   constraints), analytics (rolling metrics), risk modelling (stress engine),
   and a deployed product a non-technical person can click.

> **The differentiating insight — say this in the interview.**
> Open Banking gives you **bank-account movements, not till-level sales.** A shop
> doesn't show 200 card taps a day — it shows **one** card-acquirer settlement
> the next morning, net of fees. A consultancy shows a few invoice receipts
> arriving 30–60 days after the work. Everything in this project is modelled as
> **dated money movements on an account**, because that's the only data a lender
> using Open Banking ever actually sees.

---

## 1. The architecture (how the pieces fit)

```
                 data_generation.py            (Phase 1 · Python)
                        │  writes CSVs
                        ▼
   ┌─────────────────────────────────────────┐
   │  sme_profiles · credit_facilities        │  the raw dataset
   │  daily_transactions · daily_balances     │
   │  macro_index                             │
   └─────────────────────────────────────────┘
         │                              │
         │ load                         │ read (pandas)
         ▼                              ▼
   PostgreSQL / Supabase          metrics.py      (Phase 3)
   01_schema · 02_load · 03_views │  writes monthly_metrics,
   (Phase 2 · SQL)                │         sme_credit_summary
         │                        ▼
         │                   stress_test.py       (Phase 4)
         │                     writes firm_economics,
         │                            stress_curve
         └──────────────┬─────────────┘
                        ▼
                    app.py  (Phase 5 · Streamlit dashboard)
                        │  deploy
                        ▼
              Streamlit Community Cloud            (Phase 6)
                        │
                        ▼
                 GitHub repo + README              (Phase 7)
```

The SQL layer and the Python analytics layer compute **the same metrics two
ways**. That's deliberate: the SQL proves you can model and query in a
warehouse; the Python proves you can build a portable analytics product. When
they agree (they do — DSCR median 6.38, breach rates 18.5% / 82.7% in both),
you have a built-in correctness check you can point to.

---

## 2. The toolchain — what, where, why

| Tool | Role in the project | Where you run it | Why this one |
|---|---|---|---|
| **Python 3.11+** | Data generation + analytics engines | Local, in VS Code | Pandas/NumPy are the standard for this; the whole analytics layer is pure Python. |
| **VS Code** | Editor | Your machine | Free, great Python + SQL + Git integration. |
| **pandas / numpy** | Dataframes, rolling windows, vectorised maths | Inside Python | The core analytics toolkit. |
| **PostgreSQL** (via **Supabase**) | The relational model + SQL views | Supabase (free tier) or local | Postgres is the industry default; Supabase gives you a hosted Postgres with a browser SQL editor and no server admin. |
| **Streamlit** | Interactive dashboard | Local, then Streamlit Cloud | Turns a Python script into a clickable web app in ~100 lines. Free hosting. |
| **Plotly** | Charts inside Streamlit | Inside the app | Interactive, clean, good defaults. |
| **Git + GitHub** | Version control + portfolio home | Local + github.com | The repo *is* your portfolio artifact; recruiters read it. |
| **Power BI** (optional) | Alternative BI dashboard | Power BI Desktop (Windows) | If you want to show BI-tool fluency alongside the code. |

**Install order (do this once):**
- Python: python.org (tick "Add to PATH" on Windows).
- VS Code: code.visualstudio.com, then install the *Python* and *SQLTools*
  extensions.
- Git: git-scm.com. Sign into GitHub in VS Code.
- Supabase: sign up at supabase.com, create a free project (this gives you a
  Postgres database with a SQL editor in the browser).

---

## 3. Concepts you must own

You can't defend the project without these. Learn to explain each in two
sentences.

**SME credit risk & why it's hard.** Small businesses often have thin or messy
filed accounts, so lenders struggle to judge whether they can repay. Their
**bank account**, though, shows the truth of the cash coming in and going out —
which is exactly what Open Banking exposes with the borrower's consent. That's
"alternative underwriting": scoring the business on cash-flow behaviour rather
than on a year-old balance sheet.

**DSCR — Debt-Service Coverage Ratio.**
`DSCR = operating cash flow ÷ scheduled debt service`.
It answers "does the business generate enough cash to cover its loan payments?"
DSCR of 1.0 means it exactly covers them; below 1.0 it's eating into reserves to
pay the bank. Lenders commonly want **≥ 1.25** as headroom, and a DSCR covenant
(a contractual floor) is one of the most common loan conditions in commercial
banking. *In this project the numerator is a strict cash-flow measure — all
operating inflows minus all operating outflows — so the ratio reads more
conservatively than an EBITDA-based DSCR would.*

**Runway.** `Runway = available liquidity ÷ monthly cash burn`, where available
liquidity is **cash balance + unused overdraft**. It's the number of months the
business survives at its current rate of losing money. A firm already past its
overdraft limit has runway 0. This is the metric that turns "is this firm
losing money?" into "how long until it hits the wall?"

**Cash-flow volatility.** The **rolling standard deviation** of monthly net cash
flow. Two firms can have the same average cash flow but very different risk: one
steady, one lurching between big surpluses and deficits. Lumpy cash flow is
itself a warning sign, so you measure it directly.

**Overdraft utilisation & breach.** How often and how hard the account leans on
its overdraft. **Overdrawn** = balance below zero (using the facility, which is
normal). **Breach** = balance below the *negative of the limit* (past the
arranged facility — a genuine distress signal). Keeping these separate matters.

**Stress testing & operating leverage.** You shock one input (revenue) and watch
the whole book react. The bite comes from **operating leverage**: when revenue
falls, **variable costs** (cost of goods) fall with it, but **fixed costs**
(rent, payroll, debt service) don't — so profit falls faster than revenue. A
firm with mostly fixed costs is fragile; that's why Hospitality and
Manufacturing fail first in the results.

**The macro factor.** A single shock is only meaningful at the *portfolio* level
if firms move *together*. The generator injects a shared monthly macro index and
gives each sector a sensitivity ("beta") to it, so a downturn hits many firms at
once — exactly the correlation that makes a portfolio stress test worth running.

**Label vs behaviour — keep them apart.** `deteriorating_flag` is the **design
label**: firms the generator deliberately put on a downward path (the "ground
truth" a model would try to predict). `ever_breached_overdraft` is an
**observed behaviour** a model might *learn from*. Never conflate the thing you
predict with the evidence you predict it from — that's target leakage, and being
able to name that distinction signals real modelling maturity.

---

## 4. Phase 0 — Set up the project from scratch

**1. Make the folder and open it in VS Code.**
```bash
mkdir sme-credit-risk-simulator
cd sme-credit-risk-simulator
code .
```

**2. Create a virtual environment** (an isolated Python for this project, so its
packages never clash with anything else on your machine).
```bash
# Windows (your environment):
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS / Linux:
python3 -m venv venv
source venv/bin/activate
```
You'll see `(venv)` on your prompt. Re-activate it every time you open a new
terminal.

**3. Install the packages.** Create `requirements.txt` (provided) and run:
```bash
pip install -r requirements.txt
```

**4. Lay out the folders:**
```
sme-credit-risk-simulator/
├── data_generation.py          # Phase 1
├── metrics.py                  # Phase 3
├── stress_test.py              # Phase 4
├── app.py                      # Phase 5
├── requirements.txt
├── .gitignore
├── README.md                   # Phase 7 (portfolio front door)
├── sql/                        # Phase 2
│   ├── 01_schema.sql
│   ├── 02_load.sql
│   ├── 03_views.sql
│   └── README.md
└── output/                     # generated data (mostly git-ignored)
```

**5. Initialise Git** (do this early; commit after each phase):
```bash
git init
git add .
git commit -m "Phase 0: project scaffold"
```

---

## 5. Phase 1 — Synthetic data generation

**File: `data_generation.py`. Run: `python data_generation.py`.**

This is the phase hiring managers scrutinise, because generating *realistic*
financial data proves you know what real financial data contains. Don't skip it
and don't hand-wave it.

### 5.1 The design philosophy

Everything follows from the Open-Banking insight. Concretely:

- A **card business** (retail, hospitality) shows a **daily card-acquirer
  settlement** the next business day, net of a ~1–1.8% fee, plus occasional
  cash deposits — **not** individual sales.
- An **invoice business** (consultancy, construction) accrues work daily but
  **bills in lumps** and **collects after a receivables lag** (30–60 days).
- Every real cost is posted as a **dated transaction**: weekly cost-of-goods,
  monthly payroll (split into net wages + PAYE/NIC to HMRC), rent, utilities,
  insurance, software, professional fees, marketing, quarterly **VAT**, annual
  **corporation tax**, monthly **loan repayments**, owner drawings, the odd
  capex, and rare **failed direct debits that reverse**.

### 5.2 The architecture (how the code is organised)

- A `Sector` dataclass carries an **internally-consistent cost stack**:
  `materials % + labour % + overhead % < 1`, leaving a target net margin. This
  is the fix for the classic bug where you charge a firm both a high cost-of-
  goods *and* an independent payroll and it "spends" 120% of turnover.
- Revenue is **conserved**: invoice businesses accrue daily revenue into a
  bucket and bill the whole bucket, so you never silently drop a slice of income.
- **VAT is modelled properly**: VAT-registered firms bank VAT-inclusive receipts
  (×1.20) and pay VAT-inclusive bills, then remit the *net* quarterly — so VAT
  nets to roughly zero instead of looking like a permanent cash drain.
- A **90-day warm-up** runs before the reporting window so lagged flows are
  already "in flight" on day one (accounts aren't born empty). The warm-up is
  discarded from the output but its effect is baked into opening balances.
- A **shared macro index** drives correlated revenue; a **deteriorating cohort**
  (~11%) is put on a downward path late in the window to create a genuine
  default signal.

### 5.3 What it outputs (into `output/`)

| File | Rows | What it is |
|---|---|---|
| `sme_profiles.csv` | 500 | one row per business (the borrower) |
| `credit_facilities.csv` | 738 | overdraft for everyone; ~1 in 3 also have a loan / revolving / invoice line |
| `daily_transactions.csv` | 435,041 | the current-account ledger (the fact table) |
| `daily_balances.csv` | 365,500 | end-of-day balance per SME per day |
| `macro_index.csv` | 27 | the shared monthly macro factor |

It's fully reproducible from `SEED = 42` — run it twice, get byte-identical
files. See `output/data_dictionary.md` for every column.

> **Reproducibility gotcha you can talk about.** `term_months` is null for
> overdrafts, which makes pandas promote the column to float (`48.0`). That
> won't load into an integer SQL column. The fix is one line —
> `fac_df["term_months"] = fac_df["term_months"].astype("Int64")` (pandas'
> *nullable* integer) — so the CSV emits a clean `48` and an empty cell for
> null. Small, but it's exactly the kind of data-type discipline the SQL layer
> depends on.

---

## 6. Phase 2 — Model it in SQL

**Files: `sql/01_schema.sql`, `02_load.sql`, `03_views.sql`. Run in Supabase's
SQL editor (or `psql`).**

### 6.1 Normalisation (the concept)

Instead of one giant flat table, you split the data by **what each row is
about**:

- **`sme_profiles`** — a **dimension**: one row per business, the slowly-changing
  facts (sector, size, region).
- **`credit_facilities`** — the lending relationship: 1–2 rows per business.
- **`daily_transactions`** — the **fact table**: the high-volume event stream.
- **`daily_balances`**, **`macro_index`** — convenience aggregates / reference.

Each fact row points back to its business with a **foreign key**. This removes
duplication and lets the database *enforce* that every transaction belongs to a
real SME.

### 6.2 Constraints that enforce financial truth

The schema doesn't just declare types — it enforces **invariants** so bad data
physically cannot enter:

```sql
-- a credit can never be negative; a debit can never be positive
CONSTRAINT chk_txn_amount_sign
    CHECK ( (direction = 'credit' AND amount > 0)
         OR (direction = 'debit'  AND amount < 0) )
```

Try to insert a "credit" of −£50 and Postgres rejects it. That single constraint
is worth mentioning in an interview: it shows you think about **data integrity
at the schema level**, not just in application code.

Other guards: `size_band IN ('Micro','Small','Medium')`, `apr` between 0 and 1,
`value_date >= booking_date` (money can't clear before it's booked), positive
turnover and limits. Plus **indexes** on the columns you filter and join on
(`sme_id`, `value_date`, `category`) so analytical queries stay fast.

### 6.3 Loading

`02_load.sql` uses `\copy` (client-side) so it works over any connection —
including Supabase — without server file access. Load the **parent first**
(`sme_profiles`) so foreign keys resolve. Empty CSV cells map to SQL `NULL`,
which correctly turns an empty `term_months` into `NULL`.

### 6.4 The analytical views (metrics, in SQL)

`03_views.sql` builds the layer a dashboard consumes:

- `v_monthly_cashflow` — inflows / outflows / net per SME per month.
- `v_sme_debt_service` — annual scheduled debt service (the DSCR denominator).
- `v_overdraft_behaviour` — days overdrawn, days breached, min/avg balance.
- `v_credit_overview` — **one row per SME**: profile + annualised operating cash
  flow + debt service + **DSCR** + overdraft behaviour. This is the money view.

The DSCR definition is documented right in the view:
```
operating cash flow = (all inflows  except LOAN_DRAWDOWN)
                    − (all outflows except LOAN_REPAYMENT)
DSCR = annualised operating cash flow ÷ annual scheduled debt service
```
`DSCR` is `NULL` for overdraft-only borrowers (no scheduled repayments — you
can't divide by zero, and it's honest to say "not applicable").

**Verified against live Postgres:** loads with **0 orphan rows**, every SME has
an overdraft, the sign constraint rejects bad rows, and the headline signal
survives into SQL — **~18% of healthy firms vs ~83% of the deteriorating cohort
ever breach their limit.**

---

## 7. Phase 3 — The rolling-metrics engine

**File: `metrics.py`. Run: `python metrics.py`.**

This is the portable (no-database) version of the SQL views — the same logic in
pandas, so the deployed app needs nothing but CSVs. It writes two feeds:

- `monthly_metrics.csv` — one row per SME per month, **with rolling metrics**.
- `sme_credit_summary.csv` — one row per SME: DSCR, balances, overdraft usage,
  **runway**, and the label.

### 7.1 The rolling window (the important concept)

A rolling metric recomputes over a moving window of recent months. The subtlety
is that it must be computed **within each firm** so one firm's history never
leaks into another's:

```python
roll = monthly.groupby("sme_id")["net_cashflow"]
monthly["roll3_avg_cf"]     = roll.rolling(3, min_periods=1).mean()...
monthly["roll3_volatility"] = roll.rolling(3, min_periods=2).std()...
```

`groupby(...).rolling(...)` is the idiom: group first, *then* roll. The 3-month
average smooths out lumpy months; the 3-month standard deviation **is** the
volatility signal.

### 7.2 Runway, with the edge case handled

```python
if avail <= 0:      return 0.0     # already past the limit → out of runway
if burn  >= 0:      return NA      # generating cash → runway not meaningful
else:               return avail / (-burn)
```

The `avail <= 0` branch is the edge case I hit during testing: a firm already
past its overdraft limit has negative available liquidity, and dividing by a
negative burn would produce a nonsensical *negative* runway. Catching that is
the difference between a metric that looks right and one that *is* right.

**Verified:** the Python numbers match the SQL views exactly — DSCR n=238,
median 6.38, share below 1 = 38.2%; breach rates 18.5% / 82.7%. That agreement
across two independent implementations is your correctness proof.

---

## 8. Phase 4 — The stress-testing engine

**File: `stress_test.py`. Run: `python stress_test.py`.**

The headline module. It decomposes each firm into three monthly averages —
**revenue**, **variable cost** (cost of goods), **fixed cost** (everything else)
— and applies a revenue shock where revenue *and* variable costs contract but
fixed costs stay put:

```python
stressed_net_m = rev_m*(1-s) − ( cvar_m*(1-s)*e + cfix_m )
```

`s` is the shock (0.20 = a 20% revenue fall); `e` is a cost-elasticity knob
(how much of the variable cost actually flexes — lower `e` = stickier costs =
harsher stress). A firm **fails** if it's burning cash and its liquidity cushion
lasts fewer than the horizon (default 12 months).

### 8.1 The result (real, verified numbers)

**Portfolio survival curve** — share of the 500 firms that run out of cash
within 12 months, as the shock deepens:

| Revenue shock | 0% | 10% | 20% | 30% | 40% | 50% |
|---|---|---|---|---|---|---|
| **Fail within 12m** | 26.6% | 29.4% | **34.0%** | 38.2% | 42.0% | 47.2% |

At 0% shock, 26.6% already fail — that's the fragile tail the generator baked
in. The curve rises smoothly because the shared macro factor moves firms
together.

**By cohort at a 20% shock:** healthy **28.1%** vs deteriorating **84.6%** — the
stress test cleanly separates the two populations.

**Most fragile sectors at a 20% shock:** Hospitality 43.6%, Manufacturing 41.2%,
Construction 38.2%. These are the thin-margin, higher-fixed-cost sectors —
**operating leverage in action**, and a great thing to narrate.

### 8.2 The architecture choice that makes the dashboard fast

`stress_test.py` also writes `firm_economics.csv` — a compact **500-row** table
of each firm's revenue/variable/fixed/liquidity. The dashboard loads *that* (not
the 70 MB ledger), so dragging the stress slider re-underwrites the whole book
**instantly**. Pre-aggregating the heavy work once, then serving a tiny table to
the UI, is a pattern worth being able to explain.

---

## 9. Phase 5 — The dashboard

**File: `app.py`. Run: `streamlit run app.py`.**

Streamlit turns a Python script into a web app: every widget (`st.slider`,
`st.selectbox`) is a variable, and the whole script re-runs top-to-bottom
whenever the user moves one. So a slider *is* a stress parameter, and the app
re-underwrites live.

### 9.1 What it shows

- **KPI row**: businesses in view, fail rate under the current shock (with a
  delta vs baseline), median DSCR, breach rate.
- **Survival curve** with a marker at the current shock.
- **Failure by sector** and **by risk cohort** (healthy vs deteriorating).
- **DSCR distribution** with the DSCR = 1 line drawn in.
- **Single-firm drill-down**: pick a business, see its stressed monthly cash
  flow, months-to-survive, and its actual monthly-cash-flow history with the
  3-month rolling average overlaid.

### 9.2 The key pattern — reuse, don't duplicate

The app imports the **exact** stress function from the engine:
```python
from stress_test import run_stress
result = run_stress(econ_view[[...]], shock=shock, horizon=horizon, ...)
```
The dashboard and the batch engine share one implementation, so a fix in one
place fixes both. `@st.cache_data` on the loader means the CSVs are read once per
session, not on every slider nudge.

### 9.3 Power BI alternative (if you want BI-tool fluency on the CV)

You can build the same dashboard in **Power BI Desktop** from the CSVs:

1. **Get Data → Text/CSV** for `sme_credit_summary.csv`, `monthly_metrics.csv`,
   and `firm_economics.csv`.
2. In **Model view**, relate them on `sme_id`.
3. Add a **What-if parameter** (`Modeling → New parameter`) called *Shock*, 0 to
   0.6, step 0.05 — this becomes your stress slider.
4. Write the stress logic as **DAX measures**, e.g.:
   ```DAX
   Stressed Net Monthly =
   VAR s = SELECTEDVALUE('Shock'[Shock Value])
   RETURN
       SUMX( firm_economics,
             firm_economics[rev_m]*(1-s)
           - ( firm_economics[cvar_m]*(1-s) + firm_economics[cfix_m] ) )

   Fail Rate =
   VAR s = SELECTEDVALUE('Shock'[Shock Value])
   RETURN
       DIVIDE(
         COUNTROWS( FILTER( firm_economics,
             VAR net = firm_economics[rev_m]*(1-s)
                     - (firm_economics[cvar_m]*(1-s)+firm_economics[cfix_m])
             RETURN net < 0
                 && DIVIDE(firm_economics[available_liquidity], -net) < 12 )),
         COUNTROWS(firm_economics) )
   ```
5. Visuals: a line chart of Fail Rate, a bar of fail rate by sector, a card for
   the KPI, and a slicer on the Shock parameter.

Streamlit is the better *deployable* demo (a public URL a recruiter clicks);
Power BI is the better *"I know the enterprise BI tool"* signal. Do Streamlit
first; add Power BI if you have time.

---

## 10. Phase 6 — Deploy it (a URL a recruiter can click)

The "click buttons" deliverable. Streamlit Community Cloud hosts it free from
your GitHub repo.

**1. Trim the data you commit.** The 70 MB ledger shouldn't go to GitHub. The
`.gitignore` (provided) excludes `daily_transactions.csv` and
`daily_balances.csv` but **keeps** the small feeds the app needs
(`firm_economics.csv`, `sme_credit_summary.csv`, `monthly_metrics.csv`,
`stress_curve.csv`). Anyone can regenerate the big files with
`python data_generation.py`.

**2. Push to GitHub:**
```bash
git add .
git commit -m "Deployable dashboard"
git branch -M main
git remote add origin https://github.com/<you>/sme-credit-risk-simulator.git
git push -u origin main
```

**3. Deploy:** go to share.streamlit.io → *New app* → pick the repo, branch
`main`, main file `app.py` → *Deploy*. It installs `requirements.txt` and gives
you a public URL. Put that URL at the top of your README and CV.

**Supabase option.** If you'd rather the app read live from Postgres (to show
the DB in the loop), keep your Supabase credentials in Streamlit's *Secrets*
(never in the code), swap the `pd.read_csv` calls for a SQLAlchemy query against
the views, and uncomment the two DB lines in `requirements.txt`.

---

## 11. Phase 7 — Package it as a portfolio piece

The repo is the artifact recruiters actually open. Make the front door strong.

**README.md structure** (the one thing every visitor reads):

1. **One-line hook** + the **live demo link** + a screenshot/GIF of the stress
   slider moving.
2. **The problem** (2–3 sentences): SMEs are hard to underwrite from filed
   accounts; Open Banking exposes the cash-flow truth.
3. **What it does**: generate → model → measure → stress → visualise.
4. **The headline result**: the survival-curve table and the 18% vs 83% breach
   separation.
5. **How to run it** (the commands, in order).
6. **Design decisions** — this is where you show judgment: the
   movements-not-sales insight, the internally-consistent cost stack, the
   label-vs-behaviour split, the schema-level constraints.
7. **Tech stack** and a link to the data dictionary.

**Repo hygiene:** clean commit history (one per phase), no secrets, no
`__pycache__`, a sensible `.gitignore`, and code comments that explain *why* not
just *what*.

---

## 12. Interview talking points (defend every layer)

- **"Walk me through the data."** Lead with movements-not-sales. Then: conserved
  revenue, VAT modelled properly, a shared macro factor for correlation, and a
  deteriorating cohort so there's a real signal to find.
- **"Why SQL *and* Python?"** SQL proves warehouse modelling with enforced
  integrity; Python proves a portable analytics product. They compute the same
  metrics and agree — built-in validation.
- **"Explain DSCR / runway."** Use the two-sentence definitions from §3, then
  point at the numbers your engine produced.
- **"How does the stress test work?"** Operating leverage: variable costs flex,
  fixed costs don't, so profit falls faster than revenue — which is why
  Hospitality and Manufacturing fail first.
- **"What would you do with more time?"** Replace the synthetic label with a
  trained early-warning model (logistic regression / gradient boosting) using
  the rolling metrics as features, and validate it properly — *and* be explicit
  about avoiding target leakage between `deteriorating_flag` and the behavioural
  features. That single sentence signals you know where this goes next.

---

## Appendix — command cheat sheet

```bash
# one-time setup
python -m venv venv && .\venv\Scripts\Activate.ps1      # (Windows)
pip install -r requirements.txt

# build the pipeline, in order
python data_generation.py     # Phase 1 → output/*.csv
python metrics.py             # Phase 3 → monthly_metrics, sme_credit_summary
python stress_test.py         # Phase 4 → firm_economics, stress_curve
streamlit run app.py          # Phase 5 → dashboard at localhost:8501

# SQL layer (in Supabase SQL editor or psql), in order
#   01_schema.sql → 02_load.sql (run from the output/ folder) → 03_views.sql

# ship it
git add . && git commit -m "..." && git push
#   then deploy on share.streamlit.io
```

**Run order matters:** `data_generation` → `metrics` → `stress_test` → `app`.
Each step reads what the previous one wrote.
