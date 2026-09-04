# Assumptions Register

Each assumption gets a **decision**, a **date**, and a **rationale** — not just a
description. Four are unresolved and block generation.

Status: `OPEN` (blocks work) · `ACCEPTED` (known limitation, documented) · `RESOLVED`

---

## A01 — Base rate is unrealistic · ACCEPTED · 2026-09-04
12 injected anomalies in 512 rows = 2.3%. Real AP fraud incidence is well under
0.1%. Any precision figure from this dataset is optimistic by roughly an order
of magnitude.

**Decision:** Use v1 as a unit-test suite, not a performance estimate. Do not
quote precision from it. A 50,000-row / 5-injection variant is required before
any defensible precision number. Deferred to v2.

## A02 — No fraud in the "normal" 500 · ACCEPTED · 2026-09-04
Clean by construction, so every flag on them is a false positive by definition.
Real historical data contains undetected fraud, so production FP rate will look
worse than it is.

**Decision:** Accepted for v1. v2 should add 3–5 *unlabeled* subtle anomalies to
test whether the script surfaces things that were not planted.

## A03 — History basis / leakage · **OPEN — BLOCKS GENERATION**
Computing vendor stats from all 12 months means the transaction being scored is
inside its own baseline. Production scores against *prior* history only.

| Option | Consequence |
|---|---|
| `FULL_SAMPLE` | Simpler, optimistic, leaks. ~13 rows REVIEW. |
| `TRAILING_WINDOW` | Realistic. All six n=12 vendors REVIEW all year → ~85 rows REVIEW. |

**Decision required.** Currently set to `FULL_SAMPLE` in `rules.yaml` provisionally.
This single choice moves the REVIEW count from 13 to ~85 and silently determines
most downstream metrics. See errata D2.

## A04 — No trend or seasonality · ACCEPTED · 2026-09-04
`V004.seasonality.enabled: false`. Real AP has month-end, quarter-end and
fiscal-year-end spikes that a static z-score will flag as legitimate anomalies.

**Decision:** Off for v1 to keep a clean baseline. Enable in v2 to measure
seasonal false positives.

## A05 — Transactions are independent · ACCEPTED · 2026-09-04
No PO-to-invoice relationships, no split invoices, no partial payments.
Split-invoice structuring (three $4,000 invoices instead of one $12,000) is a
top-tier real fraud pattern this dataset **cannot** test for.

**Decision:** Known blind spot. Tracked as EC13 in `edge_cases.yaml: backlog`.

## A06 — Off-hours activity in the normal set · **OPEN — BLOCKS GENERATION**
v1.0 claimed zero off-hours rows in the clean 500, which contradicted V015's
weekend calendar. See errata D1.

| Option | Consequence |
|---|---|
| V015 weekdays only + day-aware R4 | R4 scores 1-for-1. Rigged, no FP denominator. |
| Keep weekends + time-of-day-only R4 | R4 gets an honest FP rate. **Recommended.** |

**Decision required.** Currently `R4.variant: TIME_OF_DAY_ONLY` provisionally.
Separately, real AP posts batch/integration transactions at 02:00 routinely —
consider ~2% legitimate off-hours activity in v2.

## A07 — `min_prior_n: 12` is arbitrary · ACCEPTED · 2026-09-04
Roughly "one year of monthly activity," but only three months for V001 at
4/month. A count threshold conflates recency with sufficiency.

**Decision:** Keep 12 for v1 as a fixed baseline. v2 should test a time-based
window or derive the minimum from the confidence-interval width on sd.

## A08 — Rule combination · RESOLVED · 2026-09-04
v1.0 implied a flat OR. That is not implementable — R6 must precede R1/R5, and
R3/R7/R8 suppress R1.

**Decision:** Ordered pipeline, specified as R9. Note the OR/pipeline design
still cannot express "mildly unusual amount AND slightly odd timing," which is
what most real anomalies look like. A weighted risk score is the v2 alternative
and would turn EC02, EC04 and EC08 from binary outcomes into threshold calls.

**Sub-decision still open:** R9 currently makes REVIEW *non-terminal* — a
sparse-history vendor can still be FLAGged for off-hours or duplication.
Ratify or reverse.

## A09 — Vendor master is clean · ACCEPTED · 2026-09-04
One ID per vendor, no near-duplicates. Shell-vendor and duplicate-vendor-record
schemes are invisible to this dataset by construction.

**Decision:** Known blind spot. Tracked as EC14 in backlog.

## A10 — Single currency, no tax/FX/reversals · ACCEPTED · 2026-09-04
Fine for v1. Each is a real source of production false positives.

## A11 — How REVIEW scores · **OPEN — BLOCKS METRICS**
REVIEW is neither TP nor FP. A script that routes 100% of rows to REVIEW has
zero false positives and zero value.

**Decision required** before computing any metric. The metric set needs a
human-review-burden term (e.g. % of rows requiring human touch) to penalize
that degenerate strategy.

## A12 — R5 flag target · **OPEN**
Flag the second row only, or both rows of a duplicate pair? Both are defensible;
the choice changes the FP count and EC09's expected label.

**Decision required.** Currently `SECOND_AND_SUBSEQUENT` provisionally.

---

## Open decisions blocking work

| Ref | Decision | Blocks |
|---|---|---|
| A03 | History basis: full-sample vs. trailing window | Generation |
| A06 | R4 variant + V015 calendar | Generation |
| A11 | How REVIEW scores in metrics | Eval |
| A12 | R5 flag target | Eval |
| A08 | Is REVIEW terminal? | Eval |
