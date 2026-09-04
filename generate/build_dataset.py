"""Build the v1 frozen transaction dataset.

Generation order (per README.md / spec/edge_cases.yaml header):
  1. Generate 500 normal rows from spec/vendors.yaml with a fixed seed
  2. Freeze
  3. Compute EMPIRICAL per-vendor mean/sd from the frozen sample
  4. Compute amounts marked derived: true from those empirical stats
  5. Inject the 12 edge cases

No thresholds are hardcoded here: R1's z-score threshold is read from
spec/rules.yaml at runtime (see derive_amount / assert_ec01_boundary).
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "spec"
OUT_DIR = REPO_ROOT / "data" / "v1"

CSV_COLUMNS = [
    "txn_id", "vendor_id", "timestamp", "amount", "txn_type", "invoice_no",
    "is_injected", "case_id", "expected_class", "predicted_class",
    "rule_under_test", "case_notes",
]


def load_specs():
    vendors = yaml.safe_load((SPEC_DIR / "vendors.yaml").read_text(encoding="utf-8"))
    rules = yaml.safe_load((SPEC_DIR / "rules.yaml").read_text(encoding="utf-8"))
    edge_cases = yaml.safe_load((SPEC_DIR / "edge_cases.yaml").read_text(encoding="utf-8"))
    return vendors, rules, edge_cases


def spec_dir_sha256():
    h = hashlib.sha256()
    for path in sorted(SPEC_DIR.iterdir()):
        if path.is_file():
            h.update(path.name.encode("utf-8"))
            h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Timestamp generation
# ---------------------------------------------------------------------------

def _parse_hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def valid_days(period_start, period_end, calendar):
    days = []
    d = period_start
    one_day = dt.timedelta(days=1)
    while d <= period_end:
        if calendar == "all_days" or d.weekday() < 5:  # Mon-Fri = 0-4
            days.append(d)
        d += one_day
    return days


def draw_timestamp(rng, day_pool, timestamp_cfg, hour_bounds):
    day = day_pool[rng.integers(0, len(day_pool))]
    lo_min = _parse_hhmm(hour_bounds[0])
    hi_min = _parse_hhmm(hour_bounds[1])

    modes = timestamp_cfg["modes"]
    weights = np.array([m["weight"] for m in modes], dtype=float)
    weights = weights / weights.sum()
    mode = modes[rng.choice(len(modes), p=weights)]
    center_min = _parse_hhmm(mode["center"])
    sd_min = mode["sd_minutes"]

    minute_of_day = None
    for _ in range(1000):
        candidate = rng.normal(center_min, sd_min)
        if lo_min <= candidate <= hi_min:
            minute_of_day = candidate
            break
    if minute_of_day is None:
        minute_of_day = min(max(rng.normal(center_min, sd_min), lo_min), hi_min)

    total_seconds = int(round(minute_of_day * 60))
    return dt.datetime.combine(day, dt.time()) + dt.timedelta(seconds=total_seconds)


# ---------------------------------------------------------------------------
# Amount generation
# ---------------------------------------------------------------------------

def draw_amount_vector(rng, vendor, n):
    """Draw n amounts in the vendor's distribution shape, then rescale so the
    *sample* mean/sd land exactly on the vendors.yaml target mean/sd.

    Plain i.i.d. draws don't reliably do this: sampling error of a sample sd
    scales as ~1/sqrt(2(n-1)), so for low-n or high-cv vendors (e.g. V012
    n=4, V017 cv=0.90) the realized mean/sd can easily miss a +/-10% band by
    chance, for any seed. Rescaling keeps the distribution's shape (skew for
    lognormal vendors) while guaranteeing the empirical stats the assertions
    check are the ones vendors.yaml actually specifies.
    """
    dist = vendor["distribution"]
    mean = vendor["mean"]
    sd = vendor["sd"]

    if dist == "constant":
        return np.full(n, float(mean))
    elif dist == "normal":
        raw = rng.normal(mean, sd, size=n)
    elif dist == "lognormal":
        # derive lognormal mu/sigma from the target arithmetic mean/sd
        sigma2 = np.log(1.0 + (sd / mean) ** 2)
        sigma = np.sqrt(sigma2)
        mu = np.log(mean) - sigma2 / 2.0
        raw = rng.lognormal(mu, sigma, size=n)
    else:
        raise ValueError(f"unknown distribution '{dist}' for vendor {vendor['id']}")

    if sd == 0:
        return np.full(n, float(mean))

    raw_mean = raw.mean()
    raw_sd = raw.std(ddof=1) if n > 1 else 0.0
    if raw_sd == 0:
        return np.full(n, float(mean))

    return (raw - raw_mean) * (sd / raw_sd) + mean


def apply_floor_round(amount, amount_floor, amount_decimals):
    return round(max(float(amount), amount_floor), amount_decimals)


# ---------------------------------------------------------------------------
# Normal population generation
# ---------------------------------------------------------------------------

def generate_normal_rows(rng, vendors_spec):
    meta = vendors_spec["meta"]
    period_start = dt.date.fromisoformat(meta["period_start"])
    period_end = dt.date.fromisoformat(meta["period_end"])
    amount_floor = meta["amount_floor"]
    amount_decimals = meta["amount_decimals"]
    ts_defaults = vendors_spec["timestamp_defaults"]

    rows = []
    txn_seq = 1000
    invoice_seq = 70000

    for vendor in vendors_spec["vendors"]:
        overrides = vendor.get("timestamp_overrides", {})
        calendar = overrides.get("calendar", ts_defaults["calendar"])
        hour_bounds = overrides.get("hour_bounds", ts_defaults["hour_bounds"])
        ts_cfg = {**ts_defaults, **{k: v for k, v in overrides.items() if k not in ("calendar", "hour_bounds")}}
        day_pool = valid_days(period_start, period_end, calendar)

        raw_amounts = draw_amount_vector(rng, vendor, vendor["n_12mo"])

        for raw_amount in raw_amounts:
            txn_seq += 1
            invoice_seq += 1
            amount = apply_floor_round(raw_amount, amount_floor, amount_decimals)
            timestamp = draw_timestamp(rng, day_pool, ts_cfg, hour_bounds)
            rows.append({
                "txn_id": f"T-{txn_seq}",
                "vendor_id": vendor["id"],
                "timestamp": timestamp.isoformat(),
                "amount": amount,
                "txn_type": "INVOICE",
                "invoice_no": f"INV-{invoice_seq}",
                "is_injected": False,
                "case_id": "",
                "expected_class": "",
                "predicted_class": "",
                "rule_under_test": "",
                "case_notes": "",
            })

    return rows


# ---------------------------------------------------------------------------
# Empirical stats + derived amounts
# ---------------------------------------------------------------------------

def compute_empirical_stats(normal_df):
    stats = {}
    for vendor_id, group in normal_df.groupby("vendor_id"):
        amounts = group["amount"].to_numpy(dtype=float)
        stats[vendor_id] = {
            "mean": float(np.mean(amounts)),
            "sd": float(np.std(amounts, ddof=1)) if len(amounts) > 1 else 0.0,
            "n": int(len(amounts)),
        }
    return stats


DERIVED_FORMULA_RE = re.compile(
    r"^empirical_mean\((?P<vendor>\w+)\)\s*\+\s*(?P<coef>[\d.]+)\s*\*\s*empirical_sd\((?P<vendor2>\w+)\)$"
)


def derive_amount(case, vendor_stats, rules_spec):
    formula = case["amount"]["formula"]
    m = DERIVED_FORMULA_RE.match(formula.strip())
    if not m or m.group("vendor") != m.group("vendor2") or m.group("vendor") != case["vendor_id"]:
        raise ValueError(
            f"{case['id']}: derived formula '{formula}' does not match the "
            f"supported pattern 'empirical_mean(V) + k * empirical_sd(V)'; "
            f"cannot derive without manual review."
        )

    rule = rules_spec[case["rule_under_test"]]
    threshold = rule["threshold"]
    if float(m.group("coef")) != float(threshold):
        raise ValueError(
            f"{case['id']}: formula coefficient {m.group('coef')} does not match "
            f"rules.yaml {case['rule_under_test']}.threshold={threshold}; "
            f"spec/edge_cases.yaml formula has drifted out of sync with spec/rules.yaml."
        )

    vendor_id = case["vendor_id"]
    stats = vendor_stats[vendor_id]
    amount = stats["mean"] + threshold * stats["sd"]
    # Round toward the boundary the rule tests, never away from it: a plain
    # round-to-nearest can shave the derived amount under threshold by half
    # a cent and silently invalidate a boundary test like EC01. Direction is
    # read from rule.comparison (rules.yaml), not assumed.
    comparison = rule["comparison"]
    if comparison == ">=":
        return math.ceil(amount * 100) / 100
    elif comparison == ">":
        return math.floor(amount * 100) / 100
    else:
        raise ValueError(f"{case['id']}: unsupported rule comparison '{comparison}' for rounding derived amount")


# ---------------------------------------------------------------------------
# Edge case injection
# ---------------------------------------------------------------------------

def build_injected_rows(edge_cases_spec, vendor_stats, rules_spec):
    rows = []
    for case in edge_cases_spec["cases"]:
        case_id = case["id"]
        rule_under_test = case.get("rule_under_test", "")
        expected_class = case.get("expected_class", "")
        predicted_class = case.get("predicted_class", "")
        case_notes = case.get("tests", case.get("name", ""))

        if "rows" in case:
            sub_rows = case["rows"]
        else:
            amount_field = case["amount"]
            if isinstance(amount_field, dict) and amount_field.get("derived"):
                amount = derive_amount(case, vendor_stats, rules_spec)
            else:
                amount = amount_field  # may be None (EC10) or a float
            # spec/edge_cases.yaml EC13 has no txn_id field (spec gap; not
            # fixed here since spec/ must not be edited) — fall back to a
            # case-id-derived id rather than crashing or inventing a number
            # in the T-44xx sequence that could collide.
            sub_rows = [{
                "txn_id": case.get("txn_id", f"T-{case_id}"),
                "amount": amount,
                "timestamp": case["timestamp"],
                "invoice_no": case.get("invoice_no", f"INV-{case_id}"),
            }]

        multi = len(sub_rows) > 1
        for i, sub in enumerate(sub_rows):
            default_invoice = f"INV-{case_id}-{i + 1}" if multi else f"INV-{case_id}"
            rows.append({
                "txn_id": sub["txn_id"],
                "vendor_id": case["vendor_id"],
                "timestamp": sub["timestamp"],
                "amount": sub.get("amount"),
                "txn_type": sub.get("txn_type", case.get("txn_type", "INVOICE")),
                "invoice_no": sub.get("invoice_no", default_invoice),
                "is_injected": True,
                "case_id": case_id,
                "expected_class": expected_class,
                "predicted_class": predicted_class,
                "rule_under_test": rule_under_test,
                "case_notes": case_notes,
            })

    return rows


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def run_assertions(normal_df, vendors_spec, vendor_stats, rules_spec, injected_df):
    failures = []

    if len(normal_df) != 500:
        failures.append(f"expected 500 normal rows, got {len(normal_df)}")

    for vendor in vendors_spec["vendors"]:
        vid = vendor["id"]
        stats = vendor_stats.get(vid)
        if stats is None:
            failures.append(f"{vid}: no rows generated")
            continue

        mean_p, sd_p = vendor["mean"], vendor["sd"]
        mean_e, sd_e = stats["mean"], stats["sd"]

        if mean_p != 0 and abs(mean_e - mean_p) / abs(mean_p) > 0.10:
            failures.append(
                f"{vid}: empirical mean {mean_e:.2f} outside +/-10% of parameter mean {mean_p:.2f}"
            )

        if sd_p == 0:
            if sd_e > 1e-6:
                failures.append(f"{vid}: expected sd 0 (constant distribution), got empirical sd {sd_e:.6f}")
        elif abs(sd_e - sd_p) / abs(sd_p) > 0.10:
            failures.append(
                f"{vid}: empirical sd {sd_e:.2f} outside +/-10% of parameter sd {sd_p:.2f}"
            )

    ec01 = injected_df[injected_df["case_id"] == "EC01"]
    if len(ec01) != 1:
        failures.append(f"EC01: expected exactly 1 row, found {len(ec01)}")
    else:
        row = ec01.iloc[0]
        vendor_id = row["vendor_id"]
        stats = vendor_stats[vendor_id]
        threshold = rules_spec["R1"]["threshold"]
        z = abs((row["amount"] - stats["mean"]) / stats["sd"])
        if z < threshold:
            failures.append(
                f"EC01: recomputed z={z:.4f} is below R1 threshold {threshold} "
                f"(amount={row['amount']}, vendor_mean={stats['mean']:.2f}, vendor_sd={stats['sd']:.2f})"
            )

    if failures:
        raise AssertionError("Generation-time assertions FAILED:\n  - " + "\n  - ".join(failures))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build the v1 frozen AP transaction dataset.")
    parser.add_argument("--seed", type=int, required=True, help="RNG seed for the normal 500 draw")
    args = parser.parse_args()

    vendors_spec, rules_spec, edge_cases_spec = load_specs()

    assert vendors_spec["meta"]["normal_row_count"] == sum(v["n_12mo"] for v in vendors_spec["vendors"]), (
        "vendors.yaml meta.normal_row_count does not match sum of vendors[].n_12mo"
    )

    rng = np.random.default_rng(args.seed)

    normal_rows = generate_normal_rows(rng, vendors_spec)
    normal_df = pd.DataFrame(normal_rows)
    normal_df = normal_df.sort_values("timestamp").reset_index(drop=True)  # freeze

    vendor_stats = compute_empirical_stats(normal_df)

    injected_rows = build_injected_rows(edge_cases_spec, vendor_stats, rules_spec)
    injected_df = pd.DataFrame(injected_rows)

    run_assertions(normal_df, vendors_spec, vendor_stats, rules_spec, injected_df)

    full_df = pd.concat([normal_df, injected_df], ignore_index=True)[CSV_COLUMNS]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "transactions.csv"
    full_df.to_csv(csv_path, index=False)

    manifest = {
        "seed": args.seed,
        "spec_sha256": spec_dir_sha256(),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "row_counts": {
            "normal": len(normal_df),
            "injected_cases": len(edge_cases_spec["cases"]),
            "injected_rows": len(injected_df),
            "total": len(full_df),
        },
    }
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Wrote {csv_path} ({len(full_df)} rows)")
    print(f"Wrote {manifest_path}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
