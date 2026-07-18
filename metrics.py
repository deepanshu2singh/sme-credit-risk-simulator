"""
metrics.py  —  Rolling credit-risk metrics engine
=================================================================
SME Open Banking Credit Risk Simulator  ·  Author: Deepanshu Singh

Reads the generated ledger and turns it into the two analytical feeds a credit
dashboard consumes:

  1. monthly_metrics.csv     — one row per SME per month:
                               inflows, outflows, net cash flow, and the
                               3-month ROLLING average + volatility.
  2. sme_credit_summary.csv  — one row per SME:
                               annualised operating cash flow, scheduled debt
                               service, DSCR, average / minimum balance,
                               overdraft usage, RUNWAY (months of survival at
                               current burn), plus the ground-truth label.

Why pandas and not SQL here?  The SQL views (03_views.sql) prove the same logic
lives in the warehouse. This module is the portable, no-database version that
the deployed Streamlit app imports — so the demo needs nothing but three CSVs.

Concepts
--------
* Operating cash flow : all inflows EXCEPT loan drawdowns, minus all outflows
                        EXCEPT loan repayments (debt service is the denominator,
                        not part of the numerator).
* DSCR                : annual operating cash flow / annual scheduled debt
                        service. > 1 means the business generates enough cash to
                        cover its loan payments. Banks typically want >= 1.25.
* Runway              : how many months the available liquidity (cash balance +
                        unused overdraft) lasts at the current monthly burn.
                        A firm already past its limit has runway 0.
* Volatility          : rolling standard deviation of monthly net cash flow —
                        lumpy, unpredictable cash flow is itself a risk signal.
"""

from __future__ import annotations
import pandas as pd

# --- category dictionaries: the single source of truth for what counts as what
REVENUE_CATEGORIES = {
    "SALES_CARD_SETTLEMENT", "SALES_CASH_DEPOSIT",
    "SALES_INVOICE_RECEIPT", "OTHER_INCOME",
}
FINANCING_INFLOW = {"LOAN_DRAWDOWN"}
FINANCING_OUTFLOW = {"LOAN_REPAYMENT"}
ROLL_WINDOW = 3            # months in the rolling window
RUNWAY_HORIZON = 12        # months, used later by the stress engine


def load_data(data_dir: str = "output"):
    """Load the three CSVs this module needs, with correct dtypes."""
    profiles = pd.read_csv(f"{data_dir}/sme_profiles.csv",
                           parse_dates=["incorporation_date", "account_open_date"])
    txns = pd.read_csv(f"{data_dir}/daily_transactions.csv",
                       parse_dates=["booking_date", "value_date"])
    balances = pd.read_csv(f"{data_dir}/daily_balances.csv",
                           parse_dates=["balance_date"])
    facilities = pd.read_csv(f"{data_dir}/credit_facilities.csv")
    return profiles, txns, balances, facilities


def monthly_cashflow(txns: pd.DataFrame) -> pd.DataFrame:
    """Per SME per month: inflows, outflows, net, and rolling avg + volatility."""
    t = txns.copy()
    t["year_month"] = t["value_date"].dt.to_period("M").astype(str)

    grp = t.groupby(["sme_id", "year_month"])
    monthly = grp.agg(
        inflows=("amount", lambda s: s[s > 0].sum()),
        outflows=("amount", lambda s: -s[s < 0].sum()),
        net_cashflow=("amount", "sum"),
        txn_count=("amount", "size"),
    ).reset_index().sort_values(["sme_id", "year_month"])

    # Rolling window is computed WITHIN each SME (groupby then rolling),
    # so one firm's history never leaks into another's.
    roll = monthly.groupby("sme_id")["net_cashflow"]
    monthly["roll3_avg_cf"] = (
        roll.rolling(ROLL_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    monthly["roll3_volatility"] = (
        roll.rolling(ROLL_WINDOW, min_periods=2).std().reset_index(level=0, drop=True)
    )
    return monthly


def per_sme_summary(profiles, txns, balances, facilities) -> pd.DataFrame:
    """One row per SME: operating CF, DSCR, balances, overdraft usage, runway."""
    # --- operating cash flow (annualised) ---------------------------------
    t = txns.copy()
    is_op_inflow = (t["amount"] > 0) & (~t["category"].isin(FINANCING_INFLOW))
    is_op_outflow = (t["amount"] < 0) & (~t["category"].isin(FINANCING_OUTFLOW))
    t["op_cf"] = 0.0
    t.loc[is_op_inflow, "op_cf"] = t.loc[is_op_inflow, "amount"]
    t.loc[is_op_outflow, "op_cf"] = t.loc[is_op_outflow, "amount"]

    span = t.groupby("sme_id")["value_date"].agg(["min", "max"])
    span["days"] = (span["max"] - span["min"]).dt.days + 1
    op = t.groupby("sme_id")["op_cf"].sum().rename("net_operating_cf")
    op = op.to_frame().join(span["days"])
    op["annual_operating_cf"] = op["net_operating_cf"] * 365.0 / op["days"]

    # --- scheduled annual debt service ------------------------------------
    debt = (facilities.groupby("sme_id")["monthly_repayment_gbp"].sum() * 12
            ).rename("annual_debt_service")

    # --- balance behaviour -------------------------------------------------
    bal = balances.groupby("sme_id").agg(
        avg_balance=("end_of_day_balance", "mean"),
        min_balance=("end_of_day_balance", "min"),
        days_observed=("end_of_day_balance", "size"),
        days_overdrawn=("is_overdrawn", "sum"),
    )
    # breach = balance below the negative of the arranged limit on that day
    b = balances.copy()
    b["breached"] = b["end_of_day_balance"] < -b["overdraft_limit"]
    bal["days_breached"] = b.groupby("sme_id")["breached"].sum()
    bal["pct_days_overdrawn"] = (bal["days_overdrawn"] / bal["days_observed"]).round(4)

    # --- available liquidity = latest balance + unused overdraft ----------
    last_bal = (balances.sort_values("balance_date")
                .groupby("sme_id").tail(1).set_index("sme_id"))
    liquidity = (last_bal["end_of_day_balance"] + last_bal["overdraft_limit"]
                 ).rename("available_liquidity")

    # --- assemble ----------------------------------------------------------
    s = profiles.set_index("sme_id")[[
        "business_name", "sector", "size_band", "region",
        "annual_turnover_gbp", "deteriorating_flag", "ever_breached_overdraft",
    ]].join([op[["annual_operating_cf"]], debt, bal, liquidity])

    s["annual_debt_service"] = s["annual_debt_service"].fillna(0.0)

    # DSCR: NaN when there is no scheduled debt service
    dscr = s["annual_operating_cf"] / s["annual_debt_service"].replace(0, pd.NA)
    s["dscr"] = pd.to_numeric(dscr, errors="coerce").round(2)

    # Monthly burn from the average monthly net operating cash flow
    monthly_net = s["annual_operating_cf"] / 12.0
    # Runway: months liquidity lasts at current burn.
    #   already past the limit (avail <= 0)  -> 0
    #   generating cash        (burn >= 0)   -> NA (not burning, no runway concept)
    #   burning cash                          -> avail / burn
    def _runway(row):
        avail, burn = row["available_liquidity"], row["_monthly_net"]
        if avail <= 0:
            return 0.0
        if burn >= 0:
            return pd.NA
        return round(avail / (-burn), 1)

    s["_monthly_net"] = monthly_net
    s["runway_months"] = s.apply(_runway, axis=1)
    s = s.drop(columns="_monthly_net")

    return s.reset_index()


def main(data_dir: str = "output", out_dir: str = "output"):
    profiles, txns, balances, facilities = load_data(data_dir)

    monthly = monthly_cashflow(txns)
    summary = per_sme_summary(profiles, txns, balances, facilities)

    monthly.to_csv(f"{out_dir}/monthly_metrics.csv", index=False)
    summary.to_csv(f"{out_dir}/sme_credit_summary.csv", index=False)

    # --- console sanity report --------------------------------------------
    print("=== metrics written ===")
    print(f"  monthly_metrics.csv     rows={len(monthly):,}")
    print(f"  sme_credit_summary.csv  rows={len(summary):,}")
    print()
    with_dscr = summary.dropna(subset=["dscr"])
    print("DSCR (firms with debt service):")
    print(f"  n={len(with_dscr)}  median={with_dscr['dscr'].median():.2f}"
          f"  share<1={ (with_dscr['dscr']<1).mean():.1%}")
    print()
    print("Overdraft breach rate by cohort:")
    print(summary.groupby("deteriorating_flag")["ever_breached_overdraft"]
          .mean().round(3).to_string())
    print()
    burn = summary.dropna(subset=["runway_months"])
    print(f"Firms burning cash: {len(burn)}  |  median runway (months): "
          f"{pd.to_numeric(burn['runway_months']).median():.1f}")


if __name__ == "__main__":
    main()
