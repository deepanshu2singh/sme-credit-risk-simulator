"""
stress_test.py  —  Macro stress-testing engine
=================================================================
SME Open Banking Credit Risk Simulator  ·  Author: Deepanshu Singh

The "wow" module. It answers the question a credit committee actually asks:

    "If revenue across the book fell by X%, how many of these businesses run
     out of cash within the next 12 months?"

Method
------
For each SME we decompose the last 24 months of the ledger into three monthly
averages:

    rev_m   average monthly REVENUE inflow
    cvar_m  average monthly VARIABLE cost   (cost of goods — scales with sales)
    cfix_m  average monthly FIXED cost      (payroll, rent, tax, debt service…)

Under a revenue shock `s` (e.g. 0.20 for a 20% fall), revenue and the variable
part of the cost base both contract, while fixed costs stay put — this is what
makes a downturn bite (operating leverage):

    stressed_net_m = rev_m·(1−s) − [ cvar_m·(1−s)·e + cfix_m ]

where `e` (cost_elasticity, default 1.0) is the share of variable cost that
truly flexes with revenue. Lower `e` = stickier costs = harsher stress.

A firm's cushion is its AVAILABLE LIQUIDITY = latest cash balance + unused
overdraft. It FAILS the stress if it is burning cash and that cushion lasts
fewer than `horizon` months:

    months_to_survive = available_liquidity / (−stressed_net_m)
    fails  ⇔  stressed_net_m < 0  AND  months_to_survive < horizon

Because the underlying dataset shares a macro factor across firms, a single
shock realistically hits many businesses at once — so the portfolio-level
failure rate is a meaningful number, not just independent coin flips.
"""

from __future__ import annotations
import pandas as pd
from metrics import load_data, REVENUE_CATEGORIES

FINANCING = {"LOAN_DRAWDOWN"}          # excluded from the cost base entirely
VARIABLE_CATEGORIES = {"COST_OF_GOODS"}
MONTHS_IN_WINDOW = 24


def firm_economics(txns: pd.DataFrame, balances: pd.DataFrame) -> pd.DataFrame:
    """Per-firm monthly revenue, variable cost, fixed cost, and liquidity."""
    t = txns.copy()
    rev = (t[t["category"].isin(REVENUE_CATEGORIES)]
           .groupby("sme_id")["amount"].sum() / MONTHS_IN_WINDOW).rename("rev_m")

    cvar = (-t[t["category"].isin(VARIABLE_CATEGORIES)]
            .groupby("sme_id")["amount"].sum() / MONTHS_IN_WINDOW).rename("cvar_m")

    # fixed = every other outflow that isn't COGS or a loan drawdown
    fixed_mask = ((t["amount"] < 0)
                  & (~t["category"].isin(VARIABLE_CATEGORIES))
                  & (~t["category"].isin(FINANCING)))
    cfix = (-t[fixed_mask].groupby("sme_id")["amount"].sum()
            / MONTHS_IN_WINDOW).rename("cfix_m")

    last_bal = (balances.sort_values("balance_date")
                .groupby("sme_id").tail(1).set_index("sme_id"))
    avail = (last_bal["end_of_day_balance"] + last_bal["overdraft_limit"]
             ).rename("available_liquidity")

    econ = pd.concat([rev, cvar, cfix, avail], axis=1).fillna(0.0)
    return econ


def run_stress(econ: pd.DataFrame, shock: float,
               horizon: int = 12, cost_elasticity: float = 1.0) -> pd.DataFrame:
    """Apply a revenue shock and return per-firm stressed outcomes."""
    df = econ.copy()
    df["stressed_net_m"] = (
        df["rev_m"] * (1 - shock)
        - (df["cvar_m"] * (1 - shock) * cost_elasticity + df["cfix_m"])
    )
    burning = df["stressed_net_m"] < 0
    df["months_to_survive"] = pd.NA
    df.loc[burning, "months_to_survive"] = (
        df.loc[burning, "available_liquidity"] / (-df.loc[burning, "stressed_net_m"])
    ).clip(lower=0)
    # already past the limit => 0 months
    df.loc[df["available_liquidity"] <= 0, "months_to_survive"] = 0.0
    df["fails"] = burning & (
        pd.to_numeric(df["months_to_survive"], errors="coerce") < horizon
    )
    df["shock"] = shock
    return df.reset_index()


def stress_curve(econ: pd.DataFrame, shocks=None,
                 horizon: int = 12, cost_elasticity: float = 1.0) -> pd.DataFrame:
    """Portfolio failure rate across a sweep of shock sizes."""
    if shocks is None:
        shocks = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50]
    rows = []
    for s in shocks:
        r = run_stress(econ, s, horizon, cost_elasticity)
        rows.append({
            "shock": s,
            "n": len(r),
            "fail_count": int(r["fails"].sum()),
            "fail_rate": round(r["fails"].mean(), 3),
        })
    return pd.DataFrame(rows)


def main(data_dir: str = "output", out_dir: str = "output"):
    profiles, txns, balances, facilities = load_data(data_dir)
    econ = firm_economics(txns, balances)

    # Export the compact 500-row economics table so the dashboard can recompute
    # any stress scenario from a slider instantly, without touching the ledger.
    econ.reset_index().merge(
        profiles[["sme_id", "sector", "size_band", "deteriorating_flag"]],
        on="sme_id", how="left").to_csv(f"{out_dir}/firm_economics.csv", index=False)

    curve = stress_curve(econ)
    curve.to_csv(f"{out_dir}/stress_curve.csv", index=False)

    # a headline 20% scenario, joined back to profile + label for the dashboard
    scenario = run_stress(econ, shock=0.20)
    scenario = scenario.merge(
        profiles[["sme_id", "sector", "size_band", "deteriorating_flag"]],
        on="sme_id", how="left")
    scenario.to_csv(f"{out_dir}/stress_scenario_20pct.csv", index=False)

    print("=== stress test written ===")
    print(f"  stress_curve.csv           rows={len(curve)}")
    print(f"  stress_scenario_20pct.csv  rows={len(scenario)}")
    print()
    print("Portfolio survival curve (fail = out of cash within 12 months):")
    print(curve.to_string(index=False))
    print()
    print("20% shock — failure rate by cohort:")
    print(scenario.groupby("deteriorating_flag")["fails"].mean().round(3).to_string())
    print()
    print("20% shock — failure rate by sector (top 5):")
    print(scenario.groupby("sector")["fails"].mean().round(3)
          .sort_values(ascending=False).head(5).to_string())


if __name__ == "__main__":
    main()
