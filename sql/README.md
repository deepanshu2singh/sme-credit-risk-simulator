# SQL layer — SME Open Banking Credit Risk Simulator

A normalised PostgreSQL model for the synthetic Open Banking dataset, plus an
analytical view layer that expresses the core credit-risk metrics in SQL.

## Files (run in order)

| Script | What it does |
|---|---|
| `01_schema.sql` | Creates the five tables with primary/foreign keys, CHECK constraints (financial invariants), indexes, and self-documenting `COMMENT`s. Safe to re-run — it drops and rebuilds. |
| `02_load.sql` | Bulk-loads the five CSVs with `\copy` (client-side, so it works on Supabase / RDS without server file access). Run it **from the `output/` directory** that holds the CSVs. |
| `03_views.sql` | Builds the analytical views: monthly cash flow, debt service, overdraft behaviour, and a per-SME credit overview with a DSCR proxy. |

## Quick start

```bash
# 1. schema
psql "$DATABASE_URL" -f sql/01_schema.sql

# 2. data (run from the folder containing the CSVs)
cd output && psql "$DATABASE_URL" -f ../sql/02_load.sql && cd ..

# 3. views
psql "$DATABASE_URL" -f sql/03_views.sql
```

## The model

- **`sme_profiles`** — dimension, one row per borrower.
- **`credit_facilities`** — the lending relationship (every SME has an overdraft; ~1 in 3 also carry a term loan / revolving / invoice-finance line).
- **`daily_transactions`** — the current-account ledger fact table (~435k rows). Signed amounts, enforced by a CHECK: a `credit` can never be negative, a `debit` never positive.
- **`daily_balances`** — end-of-day balance snapshot, so runway / volatility work doesn't re-derive balances from the ledger.
- **`macro_index`** — shared monthly macro factor that drives correlated revenue (what makes a *portfolio* stress test meaningful).

## The views

- **`v_monthly_cashflow`** — inflows / outflows / net per SME per month.
- **`v_sme_debt_service`** — annual scheduled debt service (the DSCR denominator).
- **`v_overdraft_behaviour`** — days overdrawn, days breached, min/avg balance.
- **`v_credit_overview`** — one row per SME: profile + annualised operating cash flow + debt service + **DSCR proxy** + observed overdraft behaviour. The primary feed for a credit dashboard.

### DSCR proxy definition

```
operating cash flow = (all inflows except LOAN_DRAWDOWN)
                    − (all outflows except LOAN_REPAYMENT)
DSCR = annualised operating cash flow / annual scheduled debt service
```

This is a **cash-flow** DSCR (it nets owner drawings, tax and capex out of the
numerator), so it reads lower and more conservatively than an EBITDA-style DSCR.
`DSCR` is `NULL` for overdraft-only borrowers with no scheduled repayments.

## Verified (PostgreSQL 16)

Loads clean with **0 orphan rows**, every SME has an overdraft, and the CHECK
constraints reject sign-violating transactions. The breach signal survives the
round-trip into SQL: **~18% of healthy firms vs ~83% of the deteriorating
cohort** ever breach their arranged limit.
