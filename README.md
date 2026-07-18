# SME Open Banking Credit Risk Simulator

**Underwriting small businesses from their bank-account data — not their filed accounts.**

> 🔗 **Live demo:** _add your Streamlit Cloud URL here_
> 📊 500 simulated UK SMEs · ~435,000 current-account transactions · portfolio stress testing

---

## The problem

Small businesses are hard to lend to: their filed accounts are thin, late, and
backward-looking. But their **bank account** tells the truth about cash coming in
and going out — and Open Banking exposes exactly that, with the borrower's
consent. This project scores businesses on **cash-flow behaviour** and then
answers the question a credit committee actually asks:

> *"If revenue fell 20% across the whole book, how many of these businesses run
> out of cash within a year?"*

## What it does

```
generate  →  model (SQL)  →  measure  →  stress test  →  visualise  →  deploy
```

1. **Generate** a realistic synthetic dataset — modelled as **bank-account
   movements, not till-level sales** (a retailer shows one daily card-acquirer
   settlement, net of fees; an invoice business shows lumpy receipts after a
   collection lag). Fully reproducible from a single seed.
2. **Model** it in **PostgreSQL** with foreign keys, indexes, and CHECK
   constraints that enforce financial invariants (a credit can never be
   negative).
3. **Measure** the metrics lenders use — **DSCR**, **cash runway**, **cash-flow
   volatility**, overdraft utilisation — with rolling windows.
4. **Stress test** the portfolio against a macro revenue shock, using operating
   leverage (variable costs flex, fixed costs don't).
5. **Visualise** it in an interactive **Streamlit** dashboard with a live stress
   slider.

## Headline results

Share of the 500 firms that run out of cash within 12 months as a revenue shock
deepens:

| Revenue shock | 0% | 10% | 20% | 30% | 40% | 50% |
|---|---|---|---|---|---|---|
| **Fail within 12m** | 26.6% | 29.4% | 34.0% | 38.2% | 42.0% | 47.2% |

- **Risk separation:** at a 20% shock, healthy firms fail at **28%** vs the
  deteriorating cohort at **85%**.
- **Most fragile sectors:** Hospitality (44%) and Manufacturing (41%) — the
  thin-margin, high-fixed-cost sectors, exactly as operating leverage predicts.
- **Cross-validated:** the SQL views and the Python engine compute the same
  metrics and agree (DSCR median 6.4; overdraft breach 18% healthy vs 83%
  deteriorating).

## Run it

```bash
python -m venv venv && source venv/bin/activate     # (Windows: .\venv\Scripts\Activate.ps1)
pip install -r requirements.txt

python data_generation.py     # build the dataset  → output/*.csv
python metrics.py             # rolling metrics    → sme_credit_summary, monthly_metrics
python stress_test.py         # stress engine      → firm_economics, stress_curve
streamlit run app.py          # dashboard          → http://localhost:8501
```

SQL layer (run in the Supabase SQL editor or `psql`, in order):
`sql/01_schema.sql` → `sql/02_load.sql` (from the `output/` folder) → `sql/03_views.sql`.

## Design decisions (the interesting bit)

- **Movements, not sales.** The whole model reflects what a lender using Open
  Banking actually sees: dated money movements on an account.
- **Internally-consistent cost stack.** Each sector's materials + labour +
  overhead sum to leave a realistic net margin, so firms don't "spend" more than
  they earn.
- **VAT modelled properly** — inclusive receipts and bills, netted off with a
  quarterly remittance — instead of as a phantom cash drain.
- **Label vs behaviour kept separate** — the injected `deteriorating_flag` (what
  you'd predict) is distinct from `ever_breached_overdraft` (evidence you'd
  predict it from), avoiding target leakage.
- **Integrity enforced in the schema**, not just the app code.

## Tech stack

Python (pandas, NumPy) · PostgreSQL / Supabase · Streamlit · Plotly · Git.

See [`output/data_dictionary.md`](output/data_dictionary.md) for every column and
[`BUILD_GUIDE.md`](BUILD_GUIDE.md) for the full from-scratch build.

---

*Synthetic data — reproducible from seed 42. Not real businesses.*
