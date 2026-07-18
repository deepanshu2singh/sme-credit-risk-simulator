-- =============================================================================
--  SME Open Banking Credit Risk Simulator
--  01_schema.sql  —  normalised relational model (PostgreSQL 14+)
--
--  Author : Deepanshu Singh
--  Purpose: Turn the synthetic Open Banking dataset into a properly modelled,
--           constrained, and indexed relational schema that a credit-risk
--           analytics layer (rolling DSCR, runway, cash-flow volatility,
--           portfolio stress testing) can be built on top of.
--
--  Design : One dimension table (sme_profiles) + one slowly-changing-ish
--           reference table (credit_facilities) + one high-volume fact table
--           (daily_transactions) + two convenience aggregates
--           (daily_balances, macro_index). Financial invariants are enforced
--           with CHECK constraints so the data cannot silently drift
--           (e.g. a "credit" line can never carry a negative amount).
--
--  Target : PostgreSQL. Runs as-is on Supabase / RDS / local. Load with
--           02_load.sql, then explore with the views at the bottom.
-- =============================================================================

BEGIN;

-- Rebuild cleanly on re-run (views drop via CASCADE, children before parents).
DROP TABLE IF EXISTS daily_transactions CASCADE;
DROP TABLE IF EXISTS daily_balances     CASCADE;
DROP TABLE IF EXISTS credit_facilities  CASCADE;
DROP TABLE IF EXISTS macro_index        CASCADE;
DROP TABLE IF EXISTS sme_profiles       CASCADE;


-- -----------------------------------------------------------------------------
-- 1. sme_profiles  —  dimension: one row per business
-- -----------------------------------------------------------------------------
CREATE TABLE sme_profiles (
    sme_id                   TEXT        PRIMARY KEY,
    business_name            TEXT        NOT NULL,
    sector                   TEXT        NOT NULL,
    sic_code                 TEXT        NOT NULL,
    size_band                TEXT        NOT NULL,
    legal_form               TEXT        NOT NULL,
    region                   TEXT        NOT NULL,
    incorporation_date       DATE        NOT NULL,
    age_years                NUMERIC(5,2) NOT NULL,
    annual_turnover_gbp      NUMERIC(14,2) NOT NULL,
    estimated_headcount      INTEGER     NOT NULL,
    gross_margin             NUMERIC(6,4) NOT NULL,
    revenue_trend_annual     NUMERIC(6,4) NOT NULL,
    vat_registered           BOOLEAN     NOT NULL,
    vat_stagger_group        SMALLINT    NOT NULL,
    account_open_date        DATE        NOT NULL,
    currency                 CHAR(3)     NOT NULL DEFAULT 'GBP',
    deteriorating_flag       BOOLEAN     NOT NULL,
    ever_breached_overdraft  BOOLEAN     NOT NULL,

    CONSTRAINT chk_profiles_size_band
        CHECK (size_band IN ('Micro','Small','Medium')),
    CONSTRAINT chk_profiles_turnover_positive
        CHECK (annual_turnover_gbp > 0),
    CONSTRAINT chk_profiles_margin_range
        CHECK (gross_margin BETWEEN 0 AND 1),
    CONSTRAINT chk_profiles_stagger
        CHECK (vat_stagger_group BETWEEN 1 AND 3),
    CONSTRAINT chk_profiles_headcount_nonneg
        CHECK (estimated_headcount >= 0)
);

COMMENT ON TABLE  sme_profiles IS
    'Dimension table: one row per simulated UK SME (the borrower).';
COMMENT ON COLUMN sme_profiles.annual_turnover_gbp IS
    'Modelled annual revenue, ex-VAT. This is the calibration target.';
COMMENT ON COLUMN sme_profiles.deteriorating_flag IS
    'DESIGN LABEL: firm was injected with a downward revenue trajectory. This is the ground-truth target a default / early-warning model would predict.';
COMMENT ON COLUMN sme_profiles.ever_breached_overdraft IS
    'OBSERVED OUTCOME: balance blew past the arranged overdraft limit at least once. A behaviour, not the label.';


-- -----------------------------------------------------------------------------
-- 2. credit_facilities  —  reference: 1..N facilities per business
-- -----------------------------------------------------------------------------
CREATE TABLE credit_facilities (
    facility_id            TEXT        PRIMARY KEY,
    sme_id                 TEXT        NOT NULL
                             REFERENCES sme_profiles(sme_id) ON DELETE CASCADE,
    facility_type          TEXT        NOT NULL,
    limit_gbp              NUMERIC(14,2) NOT NULL,
    apr                    NUMERIC(6,4) NOT NULL,
    start_date             DATE        NOT NULL,
    term_months            INTEGER,               -- NULL for overdrafts
    monthly_repayment_gbp  NUMERIC(14,2) NOT NULL,
    drawn_at_open_gbp      NUMERIC(14,2) NOT NULL,

    CONSTRAINT chk_fac_type
        CHECK (facility_type IN
               ('Overdraft','Term Loan','Revolving Credit Facility','Invoice Finance')),
    CONSTRAINT chk_fac_limit_positive     CHECK (limit_gbp > 0),
    CONSTRAINT chk_fac_apr_range          CHECK (apr >= 0 AND apr < 1),
    CONSTRAINT chk_fac_term_positive      CHECK (term_months IS NULL OR term_months > 0),
    CONSTRAINT chk_fac_repay_nonneg       CHECK (monthly_repayment_gbp >= 0),
    CONSTRAINT chk_fac_drawn_nonneg       CHECK (drawn_at_open_gbp >= 0)
);

COMMENT ON TABLE  credit_facilities IS
    'Reference table: the lending relationship. Every SME has exactly one Overdraft; ~1 in 3 also carries a Term Loan, Revolving Credit Facility, or Invoice Finance line.';
COMMENT ON COLUMN credit_facilities.limit_gbp IS
    'Facility limit for revolving lines / overdrafts, or original principal for term loans.';
COMMENT ON COLUMN credit_facilities.term_months IS
    'Repayment term in months for amortising / scheduled facilities; NULL for overdrafts.';


-- -----------------------------------------------------------------------------
-- 3. daily_transactions  —  fact: the current-account ledger (~435k rows)
-- -----------------------------------------------------------------------------
CREATE TABLE daily_transactions (
    transaction_id     TEXT        PRIMARY KEY,
    sme_id             TEXT        NOT NULL
                         REFERENCES sme_profiles(sme_id) ON DELETE CASCADE,
    booking_date       DATE        NOT NULL,
    value_date         DATE        NOT NULL,
    amount             NUMERIC(14,2) NOT NULL,
    direction          TEXT        NOT NULL,
    category           TEXT        NOT NULL,
    subcategory        TEXT,                        -- NULL where uncategorised
    counterparty_name  TEXT,
    counterparty_type  TEXT,
    payment_method     TEXT,
    channel            TEXT,
    is_recurring       BOOLEAN     NOT NULL,
    currency           CHAR(3)     NOT NULL DEFAULT 'GBP',
    balance_after      NUMERIC(14,2) NOT NULL,

    CONSTRAINT chk_txn_direction
        CHECK (direction IN ('credit','debit')),
    -- Financial invariant: sign of amount must agree with its direction.
    CONSTRAINT chk_txn_amount_sign
        CHECK ( (direction = 'credit' AND amount > 0)
             OR (direction = 'debit'  AND amount < 0) ),
    CONSTRAINT chk_txn_value_after_booking
        CHECK (value_date >= booking_date)
);

COMMENT ON TABLE  daily_transactions IS
    'Fact table: every credit-account movement as it would appear in an Open Banking feed. Signed amounts (credit > 0, debit < 0). This is bank movement data, NOT till-level sales.';
COMMENT ON COLUMN daily_transactions.value_date IS
    'Date funds actually clear. Lags booking_date for BACS / card settlement (T+1) and similar; equal for instant payments.';
COMMENT ON COLUMN daily_transactions.balance_after IS
    'Running account balance immediately after this transaction, in value-date order.';


-- -----------------------------------------------------------------------------
-- 4. daily_balances  —  aggregate: end-of-day balance snapshot per SME
-- -----------------------------------------------------------------------------
CREATE TABLE daily_balances (
    sme_id              TEXT        NOT NULL
                          REFERENCES sme_profiles(sme_id) ON DELETE CASCADE,
    balance_date        DATE        NOT NULL,
    end_of_day_balance  NUMERIC(14,2) NOT NULL,
    overdraft_limit     NUMERIC(14,2) NOT NULL,
    is_overdrawn        BOOLEAN     NOT NULL,

    CONSTRAINT pk_daily_balances PRIMARY KEY (sme_id, balance_date)
);

COMMENT ON TABLE daily_balances IS
    'Convenience aggregate: one closing balance per SME per calendar day, so runway / volatility work does not need to re-derive balances from the ledger.';


-- -----------------------------------------------------------------------------
-- 5. macro_index  —  reference: shared monthly macro activity factor
-- -----------------------------------------------------------------------------
CREATE TABLE macro_index (
    year_month   CHAR(7)      PRIMARY KEY,          -- 'YYYY-MM'
    macro_index  NUMERIC(8,4) NOT NULL
);

COMMENT ON TABLE macro_index IS
    'Shared macro activity factor (~1.0 = neutral) that drives correlated revenue across firms via each sector''s macro beta. What makes a PORTFOLIO stress test meaningful: one shock moves many firms at once.';


-- -----------------------------------------------------------------------------
-- Indexes tuned for the analytical access patterns
-- -----------------------------------------------------------------------------
-- Fact-table joins and time-slicing
CREATE INDEX idx_txn_sme            ON daily_transactions (sme_id);
CREATE INDEX idx_txn_value_date     ON daily_transactions (value_date);
CREATE INDEX idx_txn_sme_valuedate  ON daily_transactions (sme_id, value_date);
CREATE INDEX idx_txn_category       ON daily_transactions (category);
-- Facility lookups by borrower
CREATE INDEX idx_fac_sme            ON credit_facilities (sme_id);
CREATE INDEX idx_fac_type           ON credit_facilities (facility_type);
-- Balance time-series scans
CREATE INDEX idx_bal_date           ON daily_balances (balance_date);
-- Common segmentation filters on the dimension
CREATE INDEX idx_profiles_sector    ON sme_profiles (sector);
CREATE INDEX idx_profiles_det_flag  ON sme_profiles (deteriorating_flag);

COMMIT;
