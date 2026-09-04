# Working agreement

Read spec/rules.yaml, spec/vendors.yaml, spec/edge_cases.yaml, and
docs/assumptions.md before writing any code.

## Hard constraints
- NO hardcoded thresholds. All values read from spec/rules.yaml at runtime.
- Rule evaluation follows the `pipeline:` order in rules.yaml exactly.
- Generation order: generate 500 → freeze → compute EMPIRICAL per-vendor
  mean/sd → derive amounts marked `derived: true` → inject. Never derive
  from the parameters in vendors.yaml.
- Do not modify anything in spec/ or docs/. If the spec is ambiguous or
  contradictory, STOP and ask. Do not resolve it yourself.
- Do not edit predicted_class. It is committed evidence.

## Definition of done
Code that runs, with output I can inspect. "It should work" is not done.