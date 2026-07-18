"""
app.py  —  Credit Officer dashboard (Streamlit)
=================================================================
SME Open Banking Credit Risk Simulator  ·  Author: Deepanshu Singh

An interactive dashboard a credit officer would actually use:

  * headline portfolio KPIs (default-under-stress rate, median DSCR, breach rate)
  * a LIVE stress slider — drag the revenue shock and the whole book
    re-underwrites in real time
  * failure rate by sector and by risk cohort
  * a DSCR distribution
  * a single-firm drill-down

It reads three small pre-computed CSVs (built by metrics.py and stress_test.py)
so it deploys to Streamlit Community Cloud with no database and loads instantly.

Run locally:      streamlit run app.py
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from stress_test import run_stress   # reuse the exact engine, no duplicated logic

DATA_DIR = "output"

st.set_page_config(page_title="SME Credit Risk Simulator",
                   page_icon="📊", layout="wide")


# --------------------------------------------------------------------------- #
# Data loading (cached so the CSVs are read once per session)
# --------------------------------------------------------------------------- #
@st.cache_data
def load():
    econ = pd.read_csv(f"{DATA_DIR}/firm_economics.csv").set_index("sme_id")
    summary = pd.read_csv(f"{DATA_DIR}/sme_credit_summary.csv")
    monthly = pd.read_csv(f"{DATA_DIR}/monthly_metrics.csv")
    return econ, summary, monthly


econ, summary, monthly = load()

st.title("SME Open Banking — Credit Risk Simulator")
st.caption("Alternative underwriting from current-account data · "
           "500 simulated UK SMEs · portfolio stress testing")

# --------------------------------------------------------------------------- #
# Sidebar controls — the stress scenario
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Stress scenario")
    shock = st.slider("Revenue shock", 0.0, 0.60, 0.20, 0.05,
                      help="Fall in revenue applied across the whole book.")
    horizon = st.slider("Survival horizon (months)", 3, 24, 12, 1,
                        help="A firm 'fails' if its cash runs out within this window.")
    elasticity = st.slider("Variable-cost elasticity", 0.0, 1.0, 1.0, 0.1,
                           help="Share of variable cost that flexes with revenue. "
                                "Lower = stickier costs = harsher stress.")
    st.divider()
    sectors = ["All"] + sorted(econ["sector"].dropna().unique().tolist())
    sector_filter = st.selectbox("Sector filter", sectors)


# --------------------------------------------------------------------------- #
# Run the stress engine live on the current slider settings
# --------------------------------------------------------------------------- #
econ_view = econ if sector_filter == "All" else econ[econ["sector"] == sector_filter]
result = run_stress(econ_view[["rev_m", "cvar_m", "cfix_m", "available_liquidity"]],
                    shock=shock, horizon=horizon, cost_elasticity=elasticity)
result = result.merge(econ_view[["sector", "deteriorating_flag"]].reset_index(),
                      on="sme_id", how="left")

n = len(result)
fail_rate = result["fails"].mean() if n else 0
fail_count = int(result["fails"].sum())

# baseline (no shock) for a delta
base = run_stress(econ_view[["rev_m", "cvar_m", "cfix_m", "available_liquidity"]],
                  shock=0.0, horizon=horizon, cost_elasticity=elasticity)
base_rate = base["fails"].mean() if n else 0

sm = summary if sector_filter == "All" else summary[summary["sector"] == sector_filter]
median_dscr = pd.to_numeric(sm["dscr"], errors="coerce").median()
breach_rate = sm["ever_breached_overdraft"].mean()

# --------------------------------------------------------------------------- #
# KPI row
# --------------------------------------------------------------------------- #
c1, c2, c3, c4 = st.columns(4)
c1.metric("Businesses in view", f"{n:,}")
c2.metric(f"Fail under {shock:.0%} shock", f"{fail_rate:.1%}",
          delta=f"{(fail_rate - base_rate):+.1%} vs baseline", delta_color="inverse")
c3.metric("Median DSCR", "n/a" if pd.isna(median_dscr) else f"{median_dscr:.1f}")
c4.metric("Ever breached overdraft", f"{breach_rate:.1%}")

st.divider()

# --------------------------------------------------------------------------- #
# Row 1 — survival curve + failure by sector
# --------------------------------------------------------------------------- #
left, right = st.columns(2)

with left:
    st.subheader("Portfolio survival curve")
    shocks = [i / 100 for i in range(0, 61, 5)]
    curve = pd.DataFrame({
        "shock": shocks,
        "fail_rate": [
            run_stress(econ_view[["rev_m", "cvar_m", "cfix_m", "available_liquidity"]],
                       s, horizon, elasticity)["fails"].mean()
            for s in shocks
        ],
    })
    fig = px.line(curve, x="shock", y="fail_rate", markers=True)
    fig.add_vline(x=shock, line_dash="dash", line_color="crimson")
    fig.update_layout(yaxis_tickformat=".0%", xaxis_tickformat=".0%",
                      xaxis_title="Revenue shock", yaxis_title="Fail within horizon",
                      height=360, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader(f"Failure rate by sector · {shock:.0%} shock")
    by_sector = (result.groupby("sector")["fails"].mean()
                 .sort_values(ascending=False).reset_index())
    fig2 = px.bar(by_sector, x="fails", y="sector", orientation="h")
    fig2.update_layout(xaxis_tickformat=".0%", xaxis_title="Failure rate",
                       yaxis_title="", height=360,
                       margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig2, use_container_width=True)

# --------------------------------------------------------------------------- #
# Row 2 — DSCR distribution + cohort separation
# --------------------------------------------------------------------------- #
left2, right2 = st.columns(2)

with left2:
    st.subheader("DSCR distribution (firms with debt service)")
    d = pd.to_numeric(sm["dscr"], errors="coerce").dropna()
    d = d[d.between(-10, 60)]   # clip the long tail for a readable axis
    fig3 = px.histogram(d, nbins=40)
    fig3.add_vline(x=1.0, line_dash="dash", line_color="crimson",
                   annotation_text="DSCR = 1")
    fig3.update_layout(showlegend=False, xaxis_title="DSCR", yaxis_title="Firms",
                       height=340, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig3, use_container_width=True)

with right2:
    st.subheader(f"Failure by risk cohort · {shock:.0%} shock")
    cohort = (result.groupby("deteriorating_flag")["fails"].mean().reset_index())
    cohort["cohort"] = cohort["deteriorating_flag"].map(
        {False: "Healthy", True: "Deteriorating"})
    fig4 = px.bar(cohort, x="cohort", y="fails", color="cohort",
                  color_discrete_map={"Healthy": "#2e7d32", "Deteriorating": "#b03f3f"})
    fig4.update_layout(showlegend=False, yaxis_tickformat=".0%",
                       xaxis_title="", yaxis_title="Failure rate", height=340,
                       margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig4, use_container_width=True)

# --------------------------------------------------------------------------- #
# Row 3 — single-firm drill-down
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("Firm drill-down")
firm = st.selectbox("Select a business", result["sme_id"].tolist())

frow = result[result["sme_id"] == firm].iloc[0]
srow = summary[summary["sme_id"] == firm].iloc[0]
d1, d2, d3, d4 = st.columns(4)
d1.metric("Sector", frow["sector"])
d2.metric("Stressed monthly cash flow", f"£{frow['stressed_net_m']:,.0f}")
mts = frow["months_to_survive"]
d3.metric("Months to survive", "—" if pd.isna(mts) else f"{float(mts):.1f}")
d4.metric("Status under shock", "FAILS" if frow["fails"] else "Survives")

series = monthly[monthly["sme_id"] == firm]
fig5 = px.bar(series, x="year_month", y="net_cashflow",
              title="Monthly net cash flow (actual history)")
fig5.add_scatter(x=series["year_month"], y=series["roll3_avg_cf"],
                 mode="lines", name="3-month rolling avg")
fig5.update_layout(height=340, xaxis_title="", yaxis_title="Net cash flow (£)",
                   margin=dict(l=10, r=10, t=40, b=10))
st.plotly_chart(fig5, use_container_width=True)

st.caption("Synthetic data — reproducible from seed 42. Not real businesses.")
