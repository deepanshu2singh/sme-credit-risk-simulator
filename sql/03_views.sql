-- =============================================================================
--  SME Open Banking Credit Risk Simulator
--  03_views.sql  —  analytical layer (credit-risk metrics expressed in SQL)
--
--  These views turn the raw ledger into the quantities a credit officer or a
--  scoring model actually consumes: monthly cash flow, debt service, overdraft
--  behaviour, and a per-SME credit overview with a DSCR proxy. They also seed
--  the downstream rolling-metrics and stress-testing work.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- v_monthly_cashflow
--   Per SME per calendar month: gross inflows, gross outflows, net flow.
--   Keyed on value_date (when money actually moves).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_monthly_cashflow AS
SELECT
    sme_id,
    to_char(value_date, 'YYYY-MM')                                 AS year_month,
    SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END)     AS inflows,
    SUM(CASE WHEN direction = 'debit'  THEN -amount ELSE 0 END)    AS outflows,
    SUM(amount)                                                    AS net_cashflow,
    COUNT(*)                                                       AS txn_count
FROM daily_transactions
GROUP BY sme_id, to_char(value_date, 'YYYY-MM');

COMMENT ON VIEW v_monthly_cashflow IS
    'Per SME per month: inflows, outflows (as a positive number), net cash flow, and transaction count.';


-- -----------------------------------------------------------------------------
-- v_sme_debt_service
--   Per SME: scheduled annual debt service and facility footprint.
--   Debt service = scheduled monthly repayments x 12 (the DSCR denominator).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sme_debt_service AS
SELECT
    p.sme_id,
    COUNT(f.facility_id)                                     AS num_facilities,
    BOOL_OR(f.facility_type = 'Term Loan')                  AS has_term_loan,
    COALESCE(SUM(f.monthly_repayment_gbp), 0)               AS monthly_debt_service,
    COALESCE(SUM(f.monthly_repayment_gbp), 0) * 12          AS annual_debt_service,
    COALESCE(SUM(f.limit_gbp), 0)                           AS total_facility_limit
FROM sme_profiles p
LEFT JOIN credit_facilities f ON f.sme_id = p.sme_id
GROUP BY p.sme_id;

COMMENT ON VIEW v_sme_debt_service IS
    'Per SME facility footprint and scheduled annual debt service (sum of monthly repayments x 12). Forms the DSCR denominator.';


-- -----------------------------------------------------------------------------
-- v_overdraft_behaviour
--   Per SME: how hard and how often the account leans on / breaches its limit.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_overdraft_behaviour AS
SELECT
    sme_id,
    COUNT(*)                                                        AS days_observed,
    MIN(end_of_day_balance)                                         AS min_balance,
    AVG(end_of_day_balance)                                         AS avg_balance,
    SUM(CASE WHEN is_overdrawn THEN 1 ELSE 0 END)                  AS days_overdrawn,
    ROUND( AVG(CASE WHEN is_overdrawn THEN 1 ELSE 0 END)::numeric, 4) AS pct_days_overdrawn,
    -- A breach = balance pushed past the arranged limit (below -limit).
    SUM(CASE WHEN end_of_day_balance < -overdraft_limit THEN 1 ELSE 0 END) AS days_breached,
    BOOL_OR(end_of_day_balance < -overdraft_limit)                 AS ever_breached
FROM daily_balances
GROUP BY sme_id;

COMMENT ON VIEW v_overdraft_behaviour IS
    'Per SME overdraft usage and breach behaviour derived from the end-of-day balance series.';


-- -----------------------------------------------------------------------------
-- v_credit_overview
--   The credit officer's one-row-per-SME summary. Combines the borrower
--   profile, an annualised operating cash flow, debt service, a DSCR proxy,
--   and observed overdraft behaviour.
--
--   DSCR proxy definition:
--     operating cash flow = all inflows EXCEPT loan drawdowns
--                           minus all outflows EXCEPT loan repayments
--                           (debt service sits in the denominator, not here)
--     annualised over the true number of days observed in the window.
--     DSCR = annual operating cash flow / annual debt service.
--     NULL DSCR = no scheduled debt service (overdraft-only borrower).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_credit_overview AS
WITH op AS (
    SELECT
        sme_id,
        SUM(CASE WHEN direction = 'credit' AND category <> 'LOAN_DRAWDOWN'
                 THEN amount ELSE 0 END)
          - SUM(CASE WHEN direction = 'debit' AND category <> 'LOAN_REPAYMENT'
                     THEN -amount ELSE 0 END)                       AS net_operating_cf,
        (MAX(value_date) - MIN(value_date) + 1)                     AS days_span
    FROM daily_transactions
    GROUP BY sme_id
)
SELECT
    p.sme_id,
    p.business_name,
    p.sector,
    p.size_band,
    p.region,
    p.annual_turnover_gbp,
    -- Annualised operating cash flow (guard against very short spans)
    ROUND( op.net_operating_cf * 365.0 / NULLIF(op.days_span, 0), 2) AS annual_operating_cf,
    ds.annual_debt_service,
    -- DSCR proxy: NULL when there is no scheduled debt service
    ROUND(
        (op.net_operating_cf * 365.0 / NULLIF(op.days_span, 0))
        / NULLIF(ds.annual_debt_service, 0)
    , 2)                                                            AS dscr,
    ob.min_balance,
    ob.pct_days_overdrawn,
    ob.days_breached,
    p.deteriorating_flag,
    p.ever_breached_overdraft
FROM sme_profiles p
JOIN op                       ON op.sme_id = p.sme_id
LEFT JOIN v_sme_debt_service  ds ON ds.sme_id = p.sme_id
LEFT JOIN v_overdraft_behaviour ob ON ob.sme_id = p.sme_id;

COMMENT ON VIEW v_credit_overview IS
    'One row per SME: borrower profile + annualised operating cash flow + debt service + DSCR proxy + observed overdraft behaviour. The primary feed for a credit dashboard.';
