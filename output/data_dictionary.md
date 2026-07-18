# Data Dictionary — SME Open Banking Credit Risk Simulator

Synthetic dataset of **500 UK SMEs** with **~435k current-account transactions**
over **1 Jan 2023 – 31 Dec 2024**. All figures are in **GBP**. The generator is
fully reproducible from a single seed (`SEED = 42`).

> **Design note.** This models *bank-account movement data* as it appears in an
> Open Banking feed, **not** till-level sales. A retailer shows a daily card
> acquirer settlement (T+1, net of fees); an invoice-based business shows a few
> larger receipts arriving after a receivables lag. Costs, VAT, payroll, tax and
> debt service are all posted as realistic dated transactions.

---

## 1. `sme_profiles.csv` — one row per business (dimension table)

| Column | Type | Description |
|---|---|---|
| `sme_id` | string (PK) | Unique business identifier, e.g. `SME0001`. |
| `business_name` | string | Synthetic trading name. |
| `sector` | string | One of 10 sectors (Retail, Hospitality, Construction, Professional Services, Manufacturing, Wholesale, Health & Care, IT & Software, Transport & Logistics, Agriculture). |
| `sic_code` | string | 2-digit UK SIC division for the sector. |
| `size_band` | string | `Micro` / `Small` / `Medium` (by turnover). |
| `legal_form` | string | Private Limited Company, Sole Trader, LLP, or Partnership. |
| `region` | string | UK region of registration. |
| `incorporation_date` | date | Date the business was incorporated. |
| `age_years` | float | Business age at end of the reporting window. |
| `annual_turnover_gbp` | float | Modelled annual revenue, **ex-VAT** (the calibration target). |
| `estimated_headcount` | int | FTE headcount implied by the labour cost stack. |
| `gross_margin` | float | Conventional gross margin (1 − materials %). |
| `revenue_trend_annual` | float | Annual revenue growth/decline factor (log-space drift). |
| `vat_registered` | bool | True if turnover ≥ £90k (UK VAT threshold). |
| `vat_stagger_group` | int | VAT quarter stagger group (1–3). |
| `account_open_date` | date | Date the current account was opened. |
| `currency` | string | Always `GBP`. |
| `deteriorating_flag` | bool | **Design label** — firm was injected with a downward revenue trajectory over the window (the "ground truth" for a default/early-warning model). |
| `ever_breached_overdraft` | bool | **Observed outcome** — the balance blew past the arranged overdraft limit at least once. |

> Keep `deteriorating_flag` (injected cause) and `ever_breached_overdraft`
> (observed effect) distinct: the first is the label you would predict, the
> second is one of the behaviours a model might learn from.

---

## 2. `credit_facilities.csv` — one row per facility (0–2 per SME)

| Column | Type | Description |
|---|---|---|
| `facility_id` | string (PK) | Unique facility identifier, e.g. `FAC00001`. |
| `sme_id` | string (FK → sme_profiles) | Owning business. |
| `facility_type` | string | `Overdraft` (every SME has one), `Term Loan`, `Revolving Credit Facility`, or `Invoice Finance`. |
| `limit_gbp` | float | Facility limit (overdraft) or original principal (loans). |
| `apr` | float | Annual interest rate (decimal, e.g. `0.09` = 9%). |
| `start_date` | date | Facility start date. |
| `term_months` | int / null | Repayment term for loans; null for overdrafts. |
| `monthly_repayment_gbp` | float | Scheduled monthly repayment (amortising for term loans; interest-only for revolving/invoice lines; 0 for overdrafts). |
| `drawn_at_open_gbp` | float | Amount drawn at facility start. |

---

## 3. `daily_transactions.csv` — the transaction fact table (~435k rows)

| Column | Type | Description |
|---|---|---|
| `transaction_id` | string (PK) | Unique transaction id, e.g. `SME0001-T000123`. |
| `sme_id` | string (FK → sme_profiles) | Owning business. |
| `booking_date` | date | Date the transaction was booked to the account. |
| `value_date` | date | Date funds cleared. Diverges from `booking_date` for BACS credits (+1 business day) and cheque/cash deposits (+2), as in real feeds. |
| `amount` | float | **Signed**: credits are positive, debits negative. |
| `direction` | string | `credit` or `debit`. |
| `category` | string | Standardised category (see list below). ~3% are `UNCATEGORISED` to mimic enrichment gaps. |
| `subcategory` | string / null | Human-readable detail; null where uncategorised. |
| `counterparty_name` | string | Name of the other party (recurring per SME where applicable). |
| `counterparty_type` | string | CUSTOMER, SUPPLIER, EMPLOYEE, HMRC, LENDER, LANDLORD, UTILITY, INSURER, BANK, OWNER. |
| `payment_method` | string | BACS, FASTER_PAYMENT, DIRECT_DEBIT, STANDING_ORDER, CARD, CASH, CHAPS, INTERNAL_TRANSFER. |
| `channel` | string | OPEN_BANKING, ONLINE_BANKING, BRANCH. |
| `is_recurring` | bool | Flags regular/scheduled payments (rent, payroll, subscriptions, etc.). |
| `currency` | string | Always `GBP`. |
| `balance_after` | float | Running account balance immediately after this transaction (in value-date order). |

### Transaction categories

**Inflows:** `SALES_CARD_SETTLEMENT`, `SALES_CASH_DEPOSIT`, `SALES_INVOICE_RECEIPT`,
`OTHER_INCOME`, `LOAN_DRAWDOWN`, `VAT_REFUND`.

**Outflows:** `COST_OF_GOODS`, `PAYROLL_WAGES`, `PAYE_NIC`, `RENT`, `UTILITIES`,
`INSURANCE`, `SOFTWARE_SUBSCRIPTION`, `PROFESSIONAL_FEES`, `MARKETING`,
`VAT_PAYMENT`, `CORPORATION_TAX`, `LOAN_REPAYMENT`, `BANK_FEES`,
`OWNER_DRAWINGS`, `CAPEX`.

**Other:** `UNCATEGORISED` (enrichment gap).

---

## 4. `daily_balances.csv` — end-of-day balance snapshot (365,500 rows)

One row per SME per calendar day. Convenient for time-series / runway analysis
without re-deriving balances from the ledger.

| Column | Type | Description |
|---|---|---|
| `sme_id` | string (FK → sme_profiles) | Owning business. |
| `balance_date` | date | Calendar date. |
| `end_of_day_balance` | float | Closing balance that day (negative = using overdraft). |
| `overdraft_limit` | float | Arranged overdraft limit. |
| `is_overdrawn` | bool | True when `end_of_day_balance < 0`. |

---

## 5. `macro_index.csv` — monthly macro activity index (27 rows)

A shared macro factor (~1.0 baseline) that drives *correlated* revenue movements
across firms via each sector's `macro_beta`. This is what makes a **portfolio**
stress test meaningful — a single shock moves many firms at once.

| Column | Type | Description |
|---|---|---|
| `year_month` | string | Month, `YYYY-MM`. |
| `macro_index` | float | Activity index (~1.0 = neutral). |

---

## Suggested joins

```
daily_transactions.sme_id  →  sme_profiles.sme_id
credit_facilities.sme_id   →  sme_profiles.sme_id
daily_balances.sme_id      →  sme_profiles.sme_id
daily_transactions.value_date (YYYY-MM)  →  macro_index.year_month
```

## Validated properties (at SEED = 42)

- Revenue reconciles to reported turnover within each sector (±5%).
- Median free-cash-flow margin ≈ +9%; a realistic fragile tail runs cash-negative.
- Overdraft-limit breaches concentrate in the deteriorating cohort
  (~81% vs ~19% of healthy firms) — genuine early-warning signal.
- Sector net margins are differentiated and realistic (≈ 3–14%).
- Seasonality preserved (e.g. retail December card takings ≈ 1.9× February).
- ~3% uncategorised transactions and returned-direct-debit reversals included
  as realistic data-quality noise.
