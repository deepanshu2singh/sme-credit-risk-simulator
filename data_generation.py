"""
data_generation.py
===================
Synthetic data generator for the SME Open Banking Credit Risk Simulator.

Design principle
----------------
Open Banking data is **bank-account movement data**, not till-level sales.
A retailer therefore shows a daily *card acquirer settlement* (T+1), not 200
individual card taps; a consultancy shows a handful of *invoice receipts*
arriving 30-60 days after the work. We model each SME as a latent economic
process (sector economics + firm characteristics + a shared macro factor) and
simulate the resulting current-account ledger day by day.

Outputs (normalised, ready to load into SQL)
--------------------------------------------
1. sme_profiles.csv       - one row per SME (dimension)
2. credit_facilities.csv  - one row per credit facility (0-2 per SME)
3. daily_transactions.csv - the transaction fact table (account movements)
4. daily_balances.csv     - end-of-day balance snapshot per SME (derived)
5. macro_index.csv        - monthly macro activity index driving revenue
6. data_dictionary.md     - column-level documentation

Everything is reproducible from a single seed.

Author: Deepanshu Singh
"""

from __future__ import annotations

import os
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 1. CONFIGURATION
# --------------------------------------------------------------------------- #
SEED = 42
N_SMES = 500

# Reporting window that ends up in the output files.
REPORT_START = date(2023, 1, 1)
REPORT_END = date(2024, 12, 31)

# We simulate a 90-day warm-up *before* the reporting window so that lagged
# flows (receivables, supplier payments, opening VAT) are already "in flight"
# on day one of the reported data - real accounts are never born empty.
WARMUP_DAYS = 90
SIM_START = REPORT_START - timedelta(days=WARMUP_DAYS)

OUT_DIR = "output"
CURRENCY = "GBP"

rng = np.random.default_rng(SEED)


# --------------------------------------------------------------------------- #
# 2. SECTOR ECONOMICS
# --------------------------------------------------------------------------- #
# Each sector carries its own economics: margin, revenue volatility, seasonality
# (12 monthly multipliers), day-of-week pattern (Mon..Sun), how revenue is
# collected (card-settled vs invoiced), receivables lag, and macro sensitivity.
# Monthly / weekday vectors are stored raw and normalised to mean 1.0 at load.

@dataclass
class Sector:
    name: str
    sic: str
    materials_pct: float         # bought-in materials / COGS as share of revenue
    labour_pct: float            # total employment cost as share of revenue
    overhead_pct: float          # rent + utilities + insurance + subs + mkt ...
    rev_cv: float                # coefficient of variation of daily revenue
    seasonality: list            # 12 monthly multipliers (Jan..Dec)
    weekday: list                # 7 day-of-week multipliers (Mon..Sun)
    collection: str              # "card" | "invoice"
    receivables_lag: int         # mean days from sale to cash (invoice sectors)
    macro_beta: float            # sensitivity of revenue to the macro index
    avg_wage: float              # market monthly wage per FTE (to imply headcount)
    cash_share: float = 0.0      # share of sales taken as physical cash

    @property
    def gross_margin(self):      # conventional gross margin for reporting
        return round(1 - self.materials_pct, 3)

    @property
    def target_net_margin(self):
        return round(1 - self.materials_pct - self.labour_pct
                     - self.overhead_pct, 3)


def _norm(v):
    a = np.array(v, dtype=float)
    return (a / a.mean()).tolist()


# Cost stacks (materials + labour + overhead) sum to <1, leaving a realistic
# net margin per sector. Labour-intensive sectors (services, IT, care) carry
# low materials/high labour; volume sectors (retail, wholesale) the reverse.
SECTORS = [
    Sector("Retail", "47", 0.68, 0.16, 0.08, 0.35,
           [0.85, 0.82, 0.90, 0.95, 1.00, 1.00, 0.98, 0.98, 1.00, 1.05, 1.25, 1.60],
           [0.90, 0.90, 0.95, 1.00, 1.15, 1.30, 1.10],
           "card", 0, 0.55, 1800, cash_share=0.22),
    Sector("Hospitality", "56", 0.30, 0.30, 0.22, 0.45,
           [0.80, 0.82, 0.90, 0.98, 1.05, 1.15, 1.25, 1.25, 1.05, 0.98, 0.95, 1.15],
           [0.70, 0.70, 0.85, 1.00, 1.30, 1.60, 1.20],
           "card", 0, 0.70, 1650, cash_share=0.30),
    Sector("Construction", "41", 0.55, 0.28, 0.08, 0.55,
           [0.80, 0.82, 0.95, 1.05, 1.10, 1.15, 1.15, 1.10, 1.10, 1.05, 0.95, 0.75],
           [1.10, 1.10, 1.10, 1.10, 1.05, 0.20, 0.10],
           "invoice", 45, 0.85, 2600),
    Sector("Professional Services", "69", 0.08, 0.60, 0.18, 0.30,
           [1.00, 1.00, 1.05, 1.00, 1.00, 1.00, 0.95, 0.85, 1.05, 1.05, 1.05, 0.90],
           [1.10, 1.10, 1.10, 1.10, 1.05, 0.20, 0.10],
           "invoice", 40, 0.45, 3400),
    Sector("Manufacturing", "25", 0.48, 0.27, 0.11, 0.35,
           [1.00, 1.00, 1.05, 1.00, 1.02, 1.00, 0.98, 0.85, 1.05, 1.05, 1.02, 0.90],
           [1.10, 1.10, 1.10, 1.10, 1.05, 0.20, 0.10],
           "invoice", 50, 0.75, 2500),
    Sector("Wholesale", "46", 0.75, 0.11, 0.06, 0.40,
           [0.90, 0.90, 1.00, 1.00, 1.00, 1.00, 0.98, 0.95, 1.05, 1.15, 1.20, 0.95],
           [1.10, 1.10, 1.10, 1.10, 1.05, 0.25, 0.10],
           "invoice", 35, 0.70, 2200),
    Sector("Health & Care", "86", 0.15, 0.60, 0.15, 0.20,
           [1.00, 1.00, 1.02, 1.00, 1.00, 1.00, 0.98, 0.98, 1.02, 1.02, 1.00, 0.98],
           [1.05, 1.05, 1.05, 1.05, 1.00, 0.60, 0.50],
           "invoice", 30, 0.25, 2100),
    Sector("IT & Software", "62", 0.12, 0.53, 0.18, 0.25,
           [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.98, 1.02, 1.02, 1.02, 0.98],
           [1.05, 1.05, 1.05, 1.05, 1.00, 0.55, 0.45],
           "invoice", 25, 0.50, 3800),
    Sector("Transport & Logistics", "49", 0.44, 0.32, 0.11, 0.40,
           [0.90, 0.90, 0.95, 1.00, 1.00, 1.00, 1.00, 0.98, 1.02, 1.10, 1.20, 1.15],
           [1.05, 1.05, 1.05, 1.05, 1.10, 0.90, 0.70],
           "invoice", 40, 0.80, 2300),
    Sector("Agriculture", "01", 0.48, 0.22, 0.15, 0.50,
           [0.70, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30, 1.40, 1.20, 0.90, 0.80],
           [1.00, 1.00, 1.00, 1.00, 1.00, 0.90, 0.80],
           "invoice", 55, 0.65, 1900),
]
SECTOR_WEIGHTS = [0.20, 0.14, 0.12, 0.14, 0.08, 0.08, 0.07, 0.08, 0.06, 0.03]

for s in SECTORS:
    s.seasonality = _norm(s.seasonality)
    s.weekday = _norm(s.weekday)

SECTOR_BY_NAME = {s.name: s for s in SECTORS}

REGIONS = ["London", "South East", "South West", "East of England",
           "West Midlands", "East Midlands", "Yorkshire", "North West",
           "North East", "Scotland", "Wales", "Northern Ireland"]
_rw = np.array([19, 15, 9, 10, 9, 7, 8, 11, 4, 8, 5, 3], dtype=float)
REGION_WEIGHTS = (_rw / _rw.sum()).tolist()

LEGAL_FORMS = ["Private Limited Company", "Sole Trader",
               "Limited Liability Partnership", "Partnership"]
LEGAL_WEIGHTS = [0.62, 0.24, 0.08, 0.06]

SIZE_BANDS = ["Micro", "Small", "Medium"]          # ONS size definitions
SIZE_WEIGHTS = [0.55, 0.35, 0.10]
# Annual turnover ranges (£) by size band - lognormal drawn within band.
SIZE_TURNOVER = {"Micro": (80_000, 550_000),
                 "Small": (550_000, 4_000_000),
                 "Medium": (4_000_000, 25_000_000)}


# --------------------------------------------------------------------------- #
# 3. NAME / COUNTERPARTY HELPERS
# --------------------------------------------------------------------------- #
_ADJ = ["Northgate", "Riverside", "Summit", "Oakfield", "Crown", "Vantage",
        "Meridian", "Kestrel", "Halcyon", "Bramble", "Pinnacle", "Ashcroft",
        "Beacon", "Thornbury", "Clearwater", "Marlow", "Redwood", "Sterling",
        "Kingsway", "Harbour", "Cobalt", "Ironbridge", "Fairlead", "Whitfield"]
_NOUN = {"Retail": ["Stores", "Trading", "Retail", "Outlets"],
         "Hospitality": ["Kitchen", "Hospitality", "Taverns", "Catering"],
         "Construction": ["Construction", "Builders", "Contractors", "Groundworks"],
         "Professional Services": ["Advisory", "Consulting", "Associates", "Partners"],
         "Manufacturing": ["Manufacturing", "Fabrications", "Industries", "Works"],
         "Wholesale": ["Wholesale", "Distribution", "Supplies", "Traders"],
         "Health & Care": ["Care", "Health", "Clinics", "Medical"],
         "IT & Software": ["Systems", "Software", "Digital", "Technologies"],
         "Transport & Logistics": ["Logistics", "Haulage", "Transport", "Freight"],
         "Agriculture": ["Farms", "Agri", "Produce", "Growers"]}
_SUFFIX = {"Private Limited Company": "Ltd",
           "Sole Trader": "",
           "Limited Liability Partnership": "LLP",
           "Partnership": "& Co"}


def company_name(sector, legal):
    core = f"{rng.choice(_ADJ)} {rng.choice(_NOUN[sector])}"
    suf = _SUFFIX[legal]
    return f"{core} {suf}".strip()


def _pick(seq, size=None):
    return rng.choice(seq, size=size)


# --------------------------------------------------------------------------- #
# 4. MACRO INDEX
# --------------------------------------------------------------------------- #
def build_macro_index():
    """Monthly macro activity index (~1.0 baseline). A gentle cycle plus noise.

    Revenue is scaled by (1 + macro_beta * (index - 1)), so a single shared
    factor drives correlated ups and downs across firms - which is exactly what
    a portfolio-level stress test needs to be meaningful."""
    months = pd.period_range(SIM_START, REPORT_END, freq="M")
    n = len(months)
    t = np.arange(n)
    cycle = 0.03 * np.sin(2 * np.pi * t / 30.0)          # slow ~2.5yr cycle
    drift = np.linspace(-0.01, 0.02, n)                  # mild recovery trend
    noise = rng.normal(0, 0.008, n)
    idx = 1.0 + cycle + drift + noise
    return pd.DataFrame({
        "year_month": [str(m) for m in months],
        "macro_index": np.round(idx, 4),
    })


# --------------------------------------------------------------------------- #
# 5. SME PROFILES
# --------------------------------------------------------------------------- #
@dataclass
class SMEState:
    """Everything the transaction engine needs about one firm."""
    row: dict
    sector: Sector
    ann_rev: float
    daily_rev: float
    trend: float                 # annual revenue growth factor (log space)
    buffer_ratio: float          # opening cash as fraction of monthly rev
    overdraft_limit: float
    headcount: int
    monthly_rent: float
    vat_stagger: int
    counterparties: dict = field(default_factory=dict)
    deteriorating: bool = False   # injected downward trajectory (design label)
    ever_breached: bool = False   # observed: blew past arranged overdraft limit


def lognormal_between(lo, hi):
    """Draw a lognormal value roughly within [lo, hi] (10th-90th pctile)."""
    mu = (np.log(lo) + np.log(hi)) / 2.0
    sigma = (np.log(hi) - np.log(lo)) / (2 * 1.2816)     # 80% mass in range
    return float(np.exp(rng.normal(mu, sigma)))


def make_profiles():
    states = []
    for i in range(N_SMES):
        sid = f"SME{i+1:04d}"
        sector = SECTOR_BY_NAME[rng.choice([s.name for s in SECTORS], p=SECTOR_WEIGHTS)]
        size = rng.choice(SIZE_BANDS, p=SIZE_WEIGHTS)
        legal = rng.choice(LEGAL_FORMS, p=LEGAL_WEIGHTS)
        region = rng.choice(REGIONS, p=REGION_WEIGHTS)

        lo, hi = SIZE_TURNOVER[size]
        ann_rev = lognormal_between(lo, hi)
        daily_rev = ann_rev / 365.0

        # Firm age -> stability. Younger firms are more volatile and thinner.
        age_years = float(np.round(rng.gamma(2.2, 3.0) + 0.5, 1))
        incorporation = REPORT_END - timedelta(days=int(age_years * 365))

        # Growth trajectory: most flat-ish, a tail growing, a tail shrinking.
        trend = float(rng.normal(0.03, 0.14))            # annual, log-ish
        # Younger / smaller firms carry thinner cash buffers.
        base_buffer = {"Micro": 0.9, "Small": 1.3, "Medium": 1.8}[size]
        buffer_ratio = max(0.05, rng.normal(base_buffer, 0.35)
                           * min(1.0, 0.4 + age_years / 8))

        # Headcount implied by the labour cost stack (avg_wage loaded ~15% for
        # employer NIC / pension), so payroll and headcount stay consistent.
        labour_cost_annual = sector.labour_pct * ann_rev
        headcount = max(1, int(round(labour_cost_annual
                                     / (sector.avg_wage * 12 * 1.15))))
        # Rent takes ~45% of the sector overhead budget.
        monthly_rent = round(0.45 * sector.overhead_pct * ann_rev / 12
                             * rng.uniform(0.85, 1.15), 2)

        # Overdraft sized to the working-capital gap: bigger for invoice
        # sectors with long receivables, smaller for card-settled retailers.
        wc_need = 0.05 + 0.10 * (sector.receivables_lag / 55.0)
        overdraft_limit = round(max(3_000, ann_rev * wc_need
                                    * rng.uniform(0.8, 1.2)), -2)
        vat_stagger = int(rng.integers(1, 4))            # 1,2,3 quarter staggers

        st = SMEState(
            row=dict(
                sme_id=sid,
                business_name=company_name(sector.name, legal),
                sector=sector.name,
                sic_code=sector.sic,
                size_band=size,
                legal_form=legal,
                region=region,
                incorporation_date=incorporation.isoformat(),
                age_years=age_years,
                annual_turnover_gbp=round(ann_rev, 2),
                estimated_headcount=headcount,
                gross_margin=round(sector.gross_margin, 3),
                revenue_trend_annual=round(trend, 3),
                vat_registered=ann_rev >= 90_000,        # UK VAT threshold
                vat_stagger_group=vat_stagger,
                account_open_date=(incorporation
                                   + timedelta(days=int(rng.integers(0, 120)))).isoformat(),
                currency=CURRENCY,
            ),
            sector=sector, ann_rev=ann_rev, daily_rev=daily_rev, trend=trend,
            buffer_ratio=buffer_ratio, overdraft_limit=overdraft_limit,
            headcount=headcount, monthly_rent=monthly_rent, vat_stagger=vat_stagger,
        )

        # Recurring counterparties - reused so payment streams look consistent.
        st.counterparties = {
            "landlord": f"{rng.choice(_ADJ)} Estates Ltd",
            "utility": rng.choice(["British Gas", "EDF Energy", "OVO Energy",
                                   "E.ON Next", "Scottish Power"]),
            "insurer": rng.choice(["Aviva", "AXA", "Hiscox", "Zurich", "Allianz"]),
            "acquirer": rng.choice(["Worldpay", "Barclaycard", "Stripe",
                                    "Square", "Dojo", "SumUp"]),
            "software": rng.choice(["Xero", "QuickBooks", "Sage", "Microsoft 365",
                                    "Google Workspace"]),
            "accountant": f"{rng.choice(_ADJ)} Accountancy",
            "suppliers": [f"{rng.choice(_ADJ)} {rng.choice(_NOUN[sector.name])}"
                          for _ in range(int(rng.integers(2, 5)))],
        }
        states.append(st)
    return states


# --------------------------------------------------------------------------- #
# 6. CREDIT FACILITIES
# --------------------------------------------------------------------------- #
def make_facilities(states):
    """Assign credit facilities. Older / larger firms are likelier to borrow.
    Every SME gets an overdraft (its limit); some also hold a term loan,
    revolving credit, or invoice-finance line - the debt service that DSCR
    will later be measured against."""
    facilities = []
    fac_seq = 0
    for st in states:
        sid = st.row["sme_id"]
        # 1) Overdraft (arranged limit; may or may not be drawn day to day).
        fac_seq += 1
        facilities.append(dict(
            facility_id=f"FAC{fac_seq:05d}", sme_id=sid, facility_type="Overdraft",
            limit_gbp=st.overdraft_limit, apr=round(rng.uniform(0.11, 0.19), 4),
            start_date=st.row["account_open_date"], term_months=None,
            monthly_repayment_gbp=0.0, drawn_at_open_gbp=0.0,
        ))

        # 2) Optional term / revolving / invoice-finance facility.
        p_loan = {"Micro": 0.30, "Small": 0.55, "Medium": 0.75}[st.row["size_band"]]
        p_loan *= min(1.2, 0.5 + st.row["age_years"] / 8)
        if rng.random() < min(0.9, p_loan):
            ftype = rng.choice(["Term Loan", "Revolving Credit Facility",
                                "Invoice Finance"], p=[0.55, 0.25, 0.20])
            principal = round(max(10_000, st.ann_rev * rng.uniform(0.03, 0.12)), -2)
            apr = round(rng.uniform(0.06, 0.14), 4)
            term = int(rng.choice([48, 60, 72, 84], p=[0.25, 0.35, 0.25, 0.15]))
            if ftype == "Term Loan":
                r = apr / 12
                pay = principal * r / (1 - (1 + r) ** -term) if r else principal / term
            else:
                pay = principal * (apr / 12)              # interest-only-ish
            fac_seq += 1
            facilities.append(dict(
                facility_id=f"FAC{fac_seq:05d}", sme_id=sid, facility_type=ftype,
                limit_gbp=principal, apr=apr,
                start_date=(REPORT_END - timedelta(
                    days=int(rng.integers(180, int(st.row["age_years"] * 365) + 181)))
                ).isoformat(),
                term_months=term, monthly_repayment_gbp=round(pay, 2),
                drawn_at_open_gbp=principal,
            ))
    return facilities


# --------------------------------------------------------------------------- #
# 7. TRANSACTION ENGINE
# --------------------------------------------------------------------------- #
TXN_COLS = ["transaction_id", "sme_id", "booking_date", "value_date", "amount",
            "direction", "category", "subcategory", "counterparty_name",
            "counterparty_type", "payment_method", "channel", "is_recurring",
            "currency"]

CATEGORIES = {  # for the data dictionary
    "SALES_CARD_SETTLEMENT", "SALES_CASH_DEPOSIT", "SALES_INVOICE_RECEIPT",
    "OTHER_INCOME", "LOAN_DRAWDOWN", "VAT_REFUND", "COST_OF_GOODS",
    "PAYROLL_WAGES", "RENT", "UTILITIES", "INSURANCE", "SOFTWARE_SUBSCRIPTION",
    "PROFESSIONAL_FEES", "MARKETING", "VAT_PAYMENT", "PAYE_NIC",
    "CORPORATION_TAX", "LOAN_REPAYMENT", "BANK_FEES", "OWNER_DRAWINGS",
    "CAPEX", "UNCATEGORISED",
}


def _next_business_day(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _last_business_day(y, m):
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def generate_ledger(st, facilities, macro_lookup):
    """Return a list of transaction dicts (booking order) for one SME."""
    sec = st.sector
    cp = st.counterparties
    sid = st.row["sme_id"]
    txns = []
    seq = 0

    def add(bd, vd, amount, direction, cat, sub, name, ctype, method, chan, rec):
        nonlocal seq
        seq += 1
        txns.append(dict(
            transaction_id=f"{sid}-T{seq:06d}", sme_id=sid,
            booking_date=bd.isoformat(), value_date=vd.isoformat(),
            amount=round(float(amount), 2),
            direction=direction, category=cat, subcategory=sub,
            counterparty_name=name, counterparty_type=ctype,
            payment_method=method, channel=chan,
            is_recurring=rec, currency=CURRENCY,
        ))

    my_facs = [f for f in facilities if f["sme_id"] == sid]
    term_facs = [f for f in my_facs if f["facility_type"] != "Overdraft"]

    # VAT-registered firms bank VAT-inclusive receipts and pay VAT-inclusive
    # supplier bills; the quarterly remittance then nets it back out to ~zero.
    vat = 1.20 if st.row["vat_registered"] else 1.00
    mat = sec.materials_pct
    labour_month = sec.labour_pct * st.ann_rev / 12.0
    oh_month = sec.overhead_pct * st.ann_rev / 12.0

    # --- Revenue: daily gross sales, then collected per sector rules -------- #
    cash_bucket = 0.0            # accrued cash sales awaiting weekly deposit
    inv_bucket = 0.0            # accrued invoice sales awaiting billing
    for d in _daterange(SIM_START, REPORT_END):
        ym = f"{d.year}-{d.month:02d}"
        macro = macro_lookup.get(ym, 1.0)
        yrs_in = (d - SIM_START).days / 365.0
        trend_mult = np.exp(st.trend * yrs_in)
        # Deteriorating firms: a subset drift downward late in the window.
        if st.deteriorating:
            trend_mult *= max(0.4, 1 - 0.5 * max(0, (d - REPORT_START).days) / 730)

        macro_mult = 1 + sec.macro_beta * (macro - 1)
        season = sec.seasonality[d.month - 1]
        dow = sec.weekday[d.weekday()]
        mean_day = st.daily_rev * season * dow * trend_mult * macro_mult
        if mean_day <= 0:
            continue
        # Lognormal daily noise around the mean.
        sigma = np.sqrt(np.log(1 + sec.rev_cv ** 2))
        gross = float(np.exp(rng.normal(np.log(mean_day) - sigma ** 2 / 2, sigma)))

        if sec.collection == "card":
            card = gross * vat * (1 - sec.cash_share)
            cash = gross * vat * sec.cash_share
            if card > 0:
                sd = _next_business_day(d + timedelta(days=1))   # T+1 settle
                fee = card * rng.uniform(0.010, 0.018)           # acquirer fee
                add(sd, sd, card - fee, "credit", "SALES_CARD_SETTLEMENT",
                    "Card acquirer settlement", cp["acquirer"], "CUSTOMER",
                    "BACS", "OPEN_BANKING", True)
            cash_bucket += cash
            if d.weekday() == 0 and cash_bucket > 0:             # weekly cash deposit
                add(d, d, cash_bucket, "credit", "SALES_CASH_DEPOSIT",
                    "Cash & cheque deposit", "Branch deposit", "CUSTOMER",
                    "CASH", "BRANCH", False)
                cash_bucket = 0.0
        else:
            # Invoice sectors accrue daily work into a bucket and bill it in
            # lumps (~ every 2-3 days on average). Revenue is CONSERVED: the
            # whole accrued bucket is invoiced, then collected after the
            # receivables lag. Fewer, larger receipts than card sectors.
            inv_bucket += gross * vat
            if inv_bucket > 0 and (rng.random() < 0.4 or d.weekday() == 4):
                lag = int(max(5, rng.normal(sec.receivables_lag, 12)))
                rd = _next_business_day(d + timedelta(days=lag))
                if rd <= REPORT_END + timedelta(days=1):
                    add(rd, rd, inv_bucket, "credit", "SALES_INVOICE_RECEIPT",
                        "Customer invoice payment",
                        f"Client {rng.integers(1000, 9999)}", "CUSTOMER",
                        rng.choice(["FASTER_PAYMENT", "BACS"], p=[0.7, 0.3]),
                        "OPEN_BANKING", False)
                    inv_bucket = 0.0

    # --- Cost of goods / suppliers: weekly batch on trailing revenue ------- #
    for d in _daterange(SIM_START, REPORT_END):
        if d.weekday() == 2:                                      # Wednesday runs
            weekly_rev = st.daily_rev * 7 * sec.seasonality[d.month - 1]
            amt = weekly_rev * mat * vat * rng.uniform(0.85, 1.15)
            if amt > 0:
                supplier = rng.choice(cp["suppliers"])
                add(d, d, -amt, "debit", "COST_OF_GOODS", "Supplier payment",
                    supplier, "SUPPLIER", "BACS", "ONLINE_BANKING", True)

    # --- Payroll + PAYE/NIC: monthly (total = labour cost stack) ---------- #
    # Split the monthly employment cost ~70% net wages / 30% PAYE + NIC.
    for d in _daterange(SIM_START, REPORT_END):
        if d == _last_business_day(d.year, d.month):
            month_cost = labour_month * rng.uniform(0.96, 1.04)
            net = month_cost * 0.70
            add(d, d, -net, "debit", "PAYROLL_WAGES", "Staff wages (net)",
                "Payroll", "EMPLOYEE", "BACS", "ONLINE_BANKING", True)
            paye = month_cost * 0.30                              # PAYE + NIC
            pd_ = _next_business_day(date(d.year, d.month, 22))
            add(pd_, pd_, -paye, "debit", "PAYE_NIC", "PAYE & NIC",
                "HMRC", "HMRC", "DIRECT_DEBIT", "ONLINE_BANKING", True)

    # --- Fixed monthly overheads ----------------------------------------- #
    for d in _daterange(SIM_START, REPORT_END):
        if d.day == 1:
            bd = _next_business_day(d)
            add(bd, bd, -st.monthly_rent, "debit", "RENT", "Premises rent",
                cp["landlord"], "LANDLORD", "STANDING_ORDER", "ONLINE_BANKING", True)
        if d.day == 12:
            util = oh_month * 0.12 * rng.uniform(0.8, 1.2)
            add(d, d, -util, "debit", "UTILITIES", "Energy & water",
                cp["utility"], "UTILITY", "DIRECT_DEBIT", "ONLINE_BANKING", True)
        if d.day == 15:
            ins = oh_month * 0.08 * rng.uniform(0.9, 1.1)
            add(d, d, -ins, "debit", "INSURANCE", "Business insurance",
                cp["insurer"], "INSURER", "DIRECT_DEBIT", "ONLINE_BANKING", True)
            sub = oh_month * 0.06 * rng.uniform(0.8, 1.2)
            add(d, d, -sub, "debit", "SOFTWARE_SUBSCRIPTION", "SaaS subscription",
                cp["software"], "SUPPLIER", "CARD", "ONLINE_BANKING", True)
        if d.day == 20:
            fee = oh_month * 0.09 * rng.uniform(0.7, 1.3)
            add(d, d, -fee, "debit", "PROFESSIONAL_FEES", "Accountancy fees",
                cp["accountant"], "SUPPLIER", "BACS", "ONLINE_BANKING", True)
            mkt = oh_month * 0.20 * rng.uniform(0.6, 1.4)
            add(d, d, -mkt, "debit", "MARKETING", "Advertising & marketing",
                rng.choice(["Google Ads", "Meta", "Local Media"]),
                "SUPPLIER", "CARD", "ONLINE_BANKING", False)

    # --- VAT: quarterly (output VAT less input VAT), paid ~1 month after --- #
    if st.row["vat_registered"]:
        stagger = st.vat_stagger
        for d in _daterange(SIM_START, REPORT_END):
            # Quarter ends depend on stagger group (1: Mar/Jun/Sep/Dec, etc.)
            end_months = [(stagger - 1 + q * 3) % 12 + 1 for q in range(4)]
            if d.day == calendar.monthrange(d.year, d.month)[1] and d.month in end_months:
                q_rev = st.daily_rev * 91 * sec.seasonality[d.month - 1]
                output_vat = q_rev * 0.20
                input_vat = q_rev * mat * 0.20
                net_vat = output_vat - input_vat
                pay_d = _next_business_day(date(
                    d.year + (d.month // 12), d.month % 12 + 1, 7)
                    + timedelta(days=30))
                if pay_d <= REPORT_END:
                    if net_vat >= 0:
                        add(pay_d, pay_d, -net_vat, "debit", "VAT_PAYMENT",
                            "VAT return payment", "HMRC", "HMRC",
                            "DIRECT_DEBIT", "ONLINE_BANKING", True)
                    else:
                        add(pay_d, pay_d, -net_vat, "credit", "VAT_REFUND",
                            "VAT refund", "HMRC", "HMRC", "BACS",
                            "OPEN_BANKING", False)

    # --- Corporation tax: annual, ~9 months after year end if profitable --- #
    profit = st.ann_rev * sec.target_net_margin
    if st.row["legal_form"] == "Private Limited Company" and profit > 0:
        for yr in (REPORT_START.year, REPORT_END.year):
            ct_d = _next_business_day(date(yr, 9, 30))
            if REPORT_START <= ct_d <= REPORT_END:
                ct = profit * rng.uniform(0.19, 0.25)
                add(ct_d, ct_d, -ct, "debit", "CORPORATION_TAX",
                    "Corporation tax", "HMRC", "HMRC", "BACS",
                    "ONLINE_BANKING", False)

    # --- Loan repayments: monthly on each term facility ------------------- #
    for f in term_facs:
        start = date.fromisoformat(f["start_date"])
        for d in _daterange(max(SIM_START, start), REPORT_END):
            if d.day == 5:
                bd = _next_business_day(d)
                add(bd, bd, -f["monthly_repayment_gbp"], "debit",
                    "LOAN_REPAYMENT", f["facility_type"], "Lender - "
                    + f["facility_id"], "LENDER", "DIRECT_DEBIT",
                    "ONLINE_BANKING", True)

    # --- Owner drawings / dividends -------------------------------------- #
    for d in _daterange(SIM_START, REPORT_END):
        if d.day == 28 and st.row["legal_form"] in ("Sole Trader", "Partnership"):
            draw = st.daily_rev * 30 * max(0.03, sec.target_net_margin) \
                * rng.uniform(0.4, 0.9)
            add(d, d, -draw, "debit", "OWNER_DRAWINGS", "Owner drawings",
                "Owner", "OWNER", "FASTER_PAYMENT", "ONLINE_BANKING", True)

    # --- Occasional one-offs: capex + returned direct debit -------------- #
    for d in _daterange(REPORT_START, REPORT_END):
        if rng.random() < 0.0008:                                # rare capex
            cap = st.ann_rev * rng.uniform(0.005, 0.02)
            add(d, d, -cap, "debit", "CAPEX", "Equipment purchase",
                rng.choice(cp["suppliers"]), "SUPPLIER", "CHAPS",
                "ONLINE_BANKING", False)
        if rng.random() < 0.003:                                 # failed DD + reversal
            amt = rng.uniform(80, 400)
            add(d, d, -amt, "debit", "UTILITIES", "Direct debit (returned)",
                cp["utility"], "UTILITY", "DIRECT_DEBIT", "ONLINE_BANKING", False)
            rv = _next_business_day(d + timedelta(days=1))
            add(rv, rv, amt, "credit", "OTHER_INCOME", "Returned DD reversal",
                cp["utility"], "UTILITY", "BACS", "OPEN_BANKING", False)

    return txns


# --------------------------------------------------------------------------- #
# 8. BALANCE + OVERDRAFT PASS, MESSINESS
# --------------------------------------------------------------------------- #
def finalise_sme(st, txns):
    """Sort into value-date order, compute running balance, add overdraft
    interest when overdrawn, and inject realistic data-quality noise. Returns
    (transactions_in_context, daily_balance_rows)."""
    df = pd.DataFrame(txns, columns=TXN_COLS)
    if df.empty:
        return [], []

    # Drop any transactions that rounded to £0.00 (real ledgers never show them).
    df = df[df["amount"].round(2) != 0.0].reset_index(drop=True)
    if df.empty:
        return [], []

    # Booking vs value date: BACS credits clear value-dated +1 business day and
    # cheque/cash deposits +2, so the two dates legitimately diverge (a detail
    # real Open Banking feeds expose). Debits and Faster Payments settle same day.
    bd = pd.to_datetime(df["booking_date"])
    lag = np.zeros(len(df), dtype=int)
    lag[(df["payment_method"] == "BACS") & (df["direction"] == "credit")] = 1
    lag[df["category"] == "SALES_CASH_DEPOSIT"] = 2
    vd = bd + pd.to_timedelta(lag, unit="D")
    # roll value dates off weekends onto the next Monday
    wd = vd.dt.weekday
    vd = vd + pd.to_timedelta(np.where(wd == 5, 2, np.where(wd == 6, 1, 0)), unit="D")
    df["value_date"] = vd.dt.strftime("%Y-%m-%d")

    # Opening balance: a buffer of monthly revenue.
    opening = st.daily_rev * 30 * st.buffer_ratio
    df["_vd"] = pd.to_datetime(df["value_date"])
    df = df.sort_values(["_vd", "transaction_id"]).reset_index(drop=True)

    # First pass balance (before overdraft interest).
    bal = opening + df["amount"].cumsum()

    # Monthly overdraft interest when the month-end balance is negative.
    df["balance_after"] = bal.round(2)
    extra = []
    seq = len(df)
    tmp = df.copy()
    tmp["month"] = tmp["_vd"].dt.to_period("M")
    for period, grp in tmp.groupby("month"):
        min_bal = grp["balance_after"].min()
        if min_bal < 0:
            od_limit = st.overdraft_limit
            avg_neg = -min(0, grp["balance_after"].mean())
            interest = avg_neg * (0.16 / 12)
            fee_date = _next_business_day(period.to_timestamp("M").date())
            if round(interest, 2) < 0.01:                 # skip negligible fees
                if min_bal < -od_limit:
                    st.ever_breached = True
                continue
            seq += 1
            extra.append(dict(
                transaction_id=f"{st.row['sme_id']}-T{seq:06d}",
                sme_id=st.row["sme_id"],
                booking_date=fee_date.isoformat(), value_date=fee_date.isoformat(),
                amount=-round(float(interest), 2), direction="debit",
                category="BANK_FEES", subcategory="Overdraft interest",
                counterparty_name="Bank", counterparty_type="BANK",
                payment_method="INTERNAL_TRANSFER", channel="ONLINE_BANKING",
                is_recurring=True, currency=CURRENCY,
            ))
            # Flag a covenant breach if it blows past the arranged limit.
            if min_bal < -od_limit:
                st.ever_breached = True

    if extra:
        df = pd.concat([df, pd.DataFrame(extra, columns=TXN_COLS)],
                       ignore_index=True)
        df["_vd"] = pd.to_datetime(df["value_date"])
        df = df.sort_values(["_vd", "transaction_id"]).reset_index(drop=True)
        df["balance_after"] = (opening + df["amount"].cumsum()).round(2)

    # --- Data-quality noise (this is what real enriched feeds look like) --- #
    # ~3% of transactions arrive without a clean category from enrichment.
    mask = rng.random(len(df)) < 0.03
    df.loc[mask, "category"] = "UNCATEGORISED"
    df.loc[mask, "subcategory"] = None

    # Keep only the reported window (drop the warm-up), but balances already
    # reflect the warm-up history so opening balances are realistic.
    df = df[(df["_vd"] >= pd.Timestamp(REPORT_START))
            & (df["_vd"] <= pd.Timestamp(REPORT_END))].copy()

    # Daily end-of-day balance snapshot.
    daily = (df.groupby(df["_vd"].dt.date)["balance_after"].last()
               .reindex(pd.Index(list(_daterange(REPORT_START, REPORT_END)),
                                  name="date"))
               .ffill())
    daily = daily.fillna(round(opening, 2))
    bal_rows = [dict(sme_id=st.row["sme_id"], balance_date=d.isoformat(),
                     end_of_day_balance=round(float(v), 2),
                     overdraft_limit=st.overdraft_limit,
                     is_overdrawn=bool(v < 0))
                for d, v in daily.items()]

    df = df.drop(columns=["_vd"])
    return df.to_dict("records"), bal_rows


# --------------------------------------------------------------------------- #
# 9. MAIN
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Seed={SEED} | SMEs={N_SMES} | window {REPORT_START}..{REPORT_END}")

    macro_df = build_macro_index()
    macro_lookup = {r.year_month: r.macro_index
                    for r in macro_df.itertuples(index=False)}

    print("Building SME profiles ...")
    states = make_profiles()

    print("Assigning credit facilities ...")
    facilities = make_facilities(states)

    # Pre-flag a realistic minority of firms to deteriorate over the window
    # (creates genuine default signal for the stress test to find).
    for st in states:
        base = {"Micro": 0.14, "Small": 0.08, "Medium": 0.04}[st.row["size_band"]]
        if rng.random() < base * (1.4 if st.row["age_years"] < 3 else 1.0):
            st.deteriorating = True

    print("Generating ledgers (this is the heavy bit) ...")
    all_txns, all_bal = [], []
    for i, st in enumerate(states, 1):
        raw = generate_ledger(st, facilities, macro_lookup)
        txns, bals = finalise_sme(st, raw)
        all_txns.extend(txns)
        all_bal.extend(bals)
        if i % 100 == 0:
            print(f"  {i}/{N_SMES} SMEs, {len(all_txns):,} txns so far")

    prof_df = pd.DataFrame([s.row for s in states])
    prof_df["deteriorating_flag"] = [s.deteriorating for s in states]
    prof_df["ever_breached_overdraft"] = [s.ever_breached for s in states]
    fac_df = pd.DataFrame(facilities)
    # term_months is NULL for overdrafts/revolving lines, which promotes the
    # column to float (e.g. 48.0). Use pandas' nullable integer so the CSV
    # emits clean integers (48) and empty for NULL -- loads directly into an
    # INTEGER SQL column.
    fac_df["term_months"] = fac_df["term_months"].astype("Int64")
    txn_df = pd.DataFrame(all_txns, columns=TXN_COLS + ["balance_after"])
    bal_df = pd.DataFrame(all_bal)

    prof_df.to_csv(f"{OUT_DIR}/sme_profiles.csv", index=False)
    fac_df.to_csv(f"{OUT_DIR}/credit_facilities.csv", index=False)
    txn_df.to_csv(f"{OUT_DIR}/daily_transactions.csv", index=False)
    bal_df.to_csv(f"{OUT_DIR}/daily_balances.csv", index=False)
    macro_df.to_csv(f"{OUT_DIR}/macro_index.csv", index=False)

    print("\n=== WRITTEN ===")
    for f, d in [("sme_profiles", prof_df), ("credit_facilities", fac_df),
                 ("daily_transactions", txn_df), ("daily_balances", bal_df),
                 ("macro_index", macro_df)]:
        print(f"  {f}.csv  rows={len(d):,}  cols={len(d.columns)}")
    return prof_df, fac_df, txn_df, bal_df, macro_df


if __name__ == "__main__":
    main()
