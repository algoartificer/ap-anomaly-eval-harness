# Spec Errata — v1.0 to v1.1

Six defects found by review of spec v1.0 **before any code was executed**. All
were findable by cross-referencing sections of a single document.

This file is retained deliberately. A spec that was reviewed and amended is
stronger design evidence than a spec that appears to have always been correct.

| ID | Location | Defect | Severity | Disposition | Date |
|----|----------|--------|----------|-------------|------|
| D1 | vendors V015 vs. R4 | V015 generates weekend rows; day-aware R4 flags ~14 of the clean 500. Assumption "zero off-hours in the normal set" was false as written. | High | R4 defaults to `TIME_OF_DAY_ONLY`. **Awaiting ratification.** | 2026-09-04 |
| D2 | R1/R3 "prior n" vs. A03 | Contradictory history basis. Under a prior-only reading, all six n=12 vendors (72 rows) are REVIEW for the entire year. v1.0 described this as "nearly every January row" — understated by ~an order of magnitude. | High | `R1.history_basis: FULL_SAMPLE` set provisionally. **Ratification set in 'rules.yml and assumptions.md.** | 2026-09-04 |
| D3 | EC07 | No rule handled `sd == 0`, yet v1.0 asserted EC07 = FLAG "via a fallback rule." The mechanism was fabricated to justify the label. | High | **Closed.** R8 added. EC07's label is now true. | 2026-09-04 |
| D4 | R1–R7 | No evaluation order specified. Written as a flat list but only implementable as an ordered pipeline (R6 must precede R1/R5; R3/R7/R8 must precede R1). | Medium | **Closed.** R9 added. | 2026-09-04 |
| D5 | R2 vs. V002/V009 means | Structural R2 hits inside the clean 500: V009 ~8% of mass in [4750, 5000), V002 ~5% in [9500, 10000). v1.0 implied R2 would fire only on injected cases. | Medium | **Closed as documented, not fixed.** Recorded in `vendors.yaml: designed_traps`. Real vendors do sit near control limits; suppressing this would make the FP rate dishonest. | 2026-09-04 |
| D6 | EC02/03/04/08/12 | Stated z-scores assume parameter mean/sd, not sample mean/sd. Values drift a few percent. | Low | **Closed as accepted.** All five sit far from any boundary. Only EC01 requires derivation. | 2026-09-04 |
| D7 | — pipeline: block mis-numbered as a rule (Low, closed 2026-09-04). 

## Defect classes

Worth distinguishing, because they call for different review habits:

- **D3 was a fabrication** — confident reference to a spec element that did not
  exist. Detected by asking whether every noun in a justification corresponds to
  something actually defined. Cheap to check; specifically defeated by fluent prose.
- **D1, D2, D4, D5 were internal inconsistencies at section seams.** Each section
  was self-consistent. Every defect lived between Section 1 and R4, or between R1
  and Assumption 3. Review the seams, not the sections.
