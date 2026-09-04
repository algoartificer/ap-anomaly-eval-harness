# ap-anomaly-eval-harness

A labeled evaluation harness for AP transaction anomaly-detection rules.

The durable asset here is **the labeled eval set and the scoring harness**, not
the data generator. The generator is a fixture; the eval set is a regression
suite that re-runs on every rule change.

## What this is for

Given a rule set (`spec/rules.yaml`) and a synthetic transaction population
(`spec/vendors.yaml`), score the rules against 12 hand-specified boundary cases
with known expected outcomes (`spec/edge_cases.yaml`) plus 500 clean rows that
establish a false-positive rate.

The diff in pass/fail across cases when a threshold changes **is** the
change-control documentation for that control.

## Layout

```
spec/
  vendors.yaml      20 vendors: mean, sd, frequency, distribution
  rules.yaml        R1-R9, thresholds as data
  edge_cases.yaml   12 injected cases with expected + predicted class
  errata.md         defects found in spec v1.0, with disposition
generate/           population generator (not yet written)
data/v1/            frozen output + manifest (seed, spec hash, date)
rules/              rule engine (not yet written)
eval/               scoring, confusion matrix, review-burden
reports/            run outputs
docs/assumptions.md 12 assumptions, each with a decision and date
```

## Rules of the road

1. **`data/v1/` is frozen once generated.** Never regenerate in place. Bump the
   version on any spec change. Store the RNG seed and a hash of `spec/` in a
   manifest alongside the data. Without this you cannot tell whether a metric
   moved because the rule changed or because the data did.
2. **Thresholds live in `rules.yaml`, never in code.** A rule change must be a
   config diff that the eval re-runs against.
3. **Fill `predicted_class` before writing rule code.** Commit it, then run. The
   prediction-vs-actual diff is the artifact; without it, "the test passed" tells
   you nothing about whether you understood your own rules.

## Status

Spec v1.1. Five decisions in `docs/assumptions.md` are open; A03 and A06 block
generation.

## Generation order

Amounts marked `derived: true` depend on the *empirical* sample statistics, not
the parameters:

1. Generate 500 normal rows from `vendors.yaml` with a fixed seed
2. Freeze
3. Compute empirical per-vendor mean/sd
4. Compute derived amounts from those
5. Inject edge cases

Deriving EC01 from the parameter sd instead lands it near 2.06 sigma and the
boundary test measures nothing.

## Output schema

`txn_id`, `vendor_id`, `timestamp`, `amount`, `txn_type`, `invoice_no`,
`is_injected`, `case_id`, `expected_class`, `predicted_class`, `rule_under_test`,
`case_notes`
