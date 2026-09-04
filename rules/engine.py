"""Score AP transactions with the ordered rule pipeline in spec/rules.yaml."""

from __future__ import annotations

import argparse
import csv
import operator
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = REPO_ROOT / "spec" / "rules.yaml"
DEFAULT_INPUT_PATH = REPO_ROOT / "data" / "v1" / "transactions.csv"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "v1" / "scored.csv"

ACTUAL_CLASS_COLUMN = "actual_class"
FIRED_RULES_COLUMN = "fired_rules"

COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}

NUMBER_PATTERN = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
R7_CONDITION_PATTERN = re.compile(
    rf"^amount\s*(?P<op><=|<|==|!=|>=|>)\s*(?P<value>{NUMBER_PATTERN})$",
    re.IGNORECASE,
)
R8_CONDITION_PATTERN = re.compile(
    rf"^vendor_sd\s*(?P<sd_op><=|<|==|!=|>=|>)\s*"
    rf"(?P<sd_value>{NUMBER_PATTERN})\s+AND\s+prior_n\s*"
    rf"(?P<n_op><=|<|==|!=|>=|>)\s*(?P<n_value>{NUMBER_PATTERN})$",
    re.IGNORECASE,
)
R8_LOGIC_PATTERN = re.compile(
    r"^(?P<true_class>\w+)\s+if\s+amount\s*"
    r"(?P<op><=|<|==|!=|>=|>)\s*vendor_mean\s*,?\s*else\s+"
    r"(?P<false_class>\w+)$",
    re.IGNORECASE,
)


class SpecError(ValueError):
    """Raised when rules.yaml asks for behavior this engine cannot honor."""


@dataclass(frozen=True)
class VendorStats:
    mean: Decimal | None
    sd: Decimal | None
    n: int


@dataclass(frozen=True)
class ParsedRow:
    index: int
    raw: dict[str, str]
    amount: Decimal | None
    timestamp: datetime | None
    invalid_fields: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.invalid_fields


@dataclass(frozen=True)
class RowResult:
    actual_class: str
    fired_rules: tuple[str, ...]


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SpecError(f"{field_name} must be numeric, got {value!r}") from exc
    if not result.is_finite():
        raise SpecError(f"{field_name} must be finite, got {value!r}")
    return result


def _parse_clock(value: Any, field_name: str) -> time:
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise SpecError(f"{field_name} must be an ISO time, got {value!r}") from exc


def _is_injected(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"is_injected must be a boolean value, got {value!r}")


def _comparison(symbol: str) -> Callable[[Any, Any], bool]:
    try:
        return COMPARATORS[symbol]
    except KeyError as exc:
        raise SpecError(f"unsupported comparison operator {symbol!r}") from exc


def _read_rule_spec(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise SpecError(f"{path} must contain a YAML mapping")
    return loaded


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        rows = [dict(row) for row in reader]
        return list(reader.fieldnames), rows


def _parse_timestamp(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_amount(value: str) -> Decimal | None:
    if not value.strip():
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _parse_rows(
    rows: Sequence[dict[str, str]], required_fields: Sequence[str]
) -> list[ParsedRow]:
    parsed_rows: list[ParsedRow] = []
    for index, row in enumerate(rows):
        amount = _parse_amount(row.get("amount", ""))
        timestamp = _parse_timestamp(row.get("timestamp", ""))
        invalid: list[str] = []

        for field in required_fields:
            value = row.get(field, "")
            if not value.strip():
                invalid.append(field)
            elif field == "amount" and amount is None:
                invalid.append(field)
            elif field == "timestamp" and timestamp is None:
                invalid.append(field)

        parsed_rows.append(
            ParsedRow(
                index=index,
                raw=row,
                amount=amount,
                timestamp=timestamp,
                invalid_fields=tuple(invalid),
            )
        )
    return parsed_rows


def _sample_stats(amounts: Sequence[Decimal]) -> VendorStats:
    n = len(amounts)
    if not amounts:
        return VendorStats(mean=None, sd=None, n=n)

    mean = sum(amounts, Decimal()) / Decimal(n)
    if n == 1:
        return VendorStats(mean=mean, sd=None, n=n)

    squared_deviations = ((amount - mean) ** 2 for amount in amounts)
    variance = sum(squared_deviations, Decimal()) / Decimal(n - 1)
    return VendorStats(mean=mean, sd=variance.sqrt(), n=n)


def _build_vendor_stats(rows: Sequence[ParsedRow]) -> dict[str, VendorStats]:
    by_vendor: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        if not row.is_valid or row.amount is None:
            continue
        if _is_injected(row.raw.get("is_injected", "")):
            continue
        by_vendor[row.raw["vendor_id"].strip()].append(row.amount)
    return {vendor_id: _sample_stats(amounts) for vendor_id, amounts in by_vendor.items()}


def _ordered_timestamp(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _match_value(row: ParsedRow, field: str) -> Any:
    if field == "amount":
        return row.amount
    if field == "timestamp":
        return _ordered_timestamp(row.timestamp) if row.timestamp is not None else None
    return row.raw.get(field, "").strip()


def _find_duplicate_targets(
    rows: Sequence[ParsedRow], match_fields: Sequence[str], window_hours: Decimal
) -> set[int]:
    window_seconds = window_hours * Decimal(60 * 60)
    prior_by_key: dict[tuple[Any, ...], deque[datetime]] = defaultdict(deque)
    duplicate_indexes: set[int] = set()
    valid_rows = sorted(
        (row for row in rows if row.is_valid and row.timestamp is not None),
        key=lambda row: (_ordered_timestamp(row.timestamp), row.index),
    )

    for row in valid_rows:
        timestamp = _ordered_timestamp(row.timestamp)
        key = tuple(_match_value(row, field) for field in match_fields)
        prior_timestamps = prior_by_key[key]

        while prior_timestamps:
            elapsed = Decimal(str((timestamp - prior_timestamps[0]).total_seconds()))
            if elapsed <= window_seconds:
                break
            prior_timestamps.popleft()

        if prior_timestamps:
            duplicate_indexes.add(row.index)
        prior_timestamps.append(timestamp)

    return duplicate_indexes


def _precedence_ranks(spec: Mapping[str, Any]) -> dict[str, int]:
    classes = [str(value) for value in spec["meta"]["output_classes"]]
    statement = str(spec["pipeline"]["precedence_when_multiple_fire"])
    edges: dict[str, set[str]] = {value: set() for value in classes}

    for sentence in (part.strip() for part in statement.split(".") if part.strip()):
        all_match = re.fullmatch(r"(\w+)\s+outranks\s+all", sentence, re.IGNORECASE)
        if all_match:
            higher = all_match.group(1)
            if higher not in edges:
                raise SpecError(f"unknown output class {higher!r} in precedence")
            edges[higher].update(value for value in classes if value != higher)
            continue

        chain = [part.strip() for part in re.split(r"\s+outranks\s+", sentence)]
        if len(chain) < 2 or any(value not in edges for value in chain):
            raise SpecError(f"cannot parse pipeline precedence {sentence!r}")
        for higher, lower in zip(chain, chain[1:]):
            edges[higher].add(lower)

    ranks: dict[str, int] = {}
    visiting: set[str] = set()

    def rank(value: str) -> int:
        if value in ranks:
            return ranks[value]
        if value in visiting:
            raise SpecError("pipeline precedence contains a cycle")
        visiting.add(value)
        value_rank = max((rank(lower) + 1 for lower in edges[value]), default=0)
        visiting.remove(value)
        ranks[value] = value_rank
        return value_rank

    for output_class in classes:
        rank(output_class)

    if len(set(ranks.values())) != len(classes):
        raise SpecError("pipeline precedence does not fully order every output class")
    return ranks


class RuleEngine:
    """Evaluate configured R1-R8 handlers in the YAML pipeline order."""

    def __init__(self, spec: dict[str, Any], rows: Sequence[ParsedRow]) -> None:
        self.spec = spec
        self.rows = rows
        self.output_classes = tuple(str(value) for value in spec["meta"]["output_classes"])
        self.precedence = _precedence_ranks(spec)
        self.default_class = str(spec["R7"]["on_fire"])
        self.vendor_stats = _build_vendor_stats(rows)
        self._suppress_r1 = False
        self._suppress_r2 = False

        pipeline = spec["pipeline"]["order"]
        self.pipeline = tuple((str(item["rule"]), bool(item["terminal"])) for item in pipeline)
        handlers = {
            "R1": self._r1,
            "R2": self._r2,
            "R3": self._r3,
            "R4": self._r4,
            "R5": self._r5,
            "R6": self._r6,
            "R7": self._r7,
            "R8": self._r8,
        }
        self.handlers = handlers

        configured_rules = [rule_id for rule_id, _ in self.pipeline]
        if len(configured_rules) != len(handlers) or set(configured_rules) != set(handlers):
            raise SpecError("pipeline.order must contain each of R1-R8 exactly once")
        if any(output_class not in self.precedence for output_class in self.output_classes):
            raise SpecError("every output class must have configured precedence")

        r1 = spec["R1"]
        if r1["history_basis"] != "FULL_SAMPLE":
            raise SpecError(f"unsupported R1.history_basis {r1['history_basis']!r}")
        if r1["statistic"] != "abs((amount - vendor_mean) / vendor_sd)":
            raise SpecError(f"unsupported R1.statistic {r1['statistic']!r}")

        r2 = spec["R2"]
        supported_interval = "half_open_lower_inclusive_upper_exclusive"
        if r2["interval"] != supported_interval:
            raise SpecError(f"unsupported R2.interval {r2['interval']!r}")

        r5 = spec["R5"]
        if r5["flag_target"] != "SECOND_AND_SUBSEQUENT":
            raise SpecError(f"unsupported R5.flag_target {r5['flag_target']!r}")
        self.duplicate_indexes = _find_duplicate_targets(
            rows,
            tuple(str(field) for field in r5["match_fields"]),
            _decimal(r5["window_hours"], "R5.window_hours"),
        )

        self.r7_condition = R7_CONDITION_PATTERN.fullmatch(str(spec["R7"]["condition"]))
        if self.r7_condition is None:
            raise SpecError(f"unsupported R7.condition {spec['R7']['condition']!r}")
        self.r8_condition = R8_CONDITION_PATTERN.fullmatch(str(spec["R8"]["condition"]))
        if self.r8_condition is None:
            raise SpecError(f"unsupported R8.condition {spec['R8']['condition']!r}")
        self.r8_logic = R8_LOGIC_PATTERN.fullmatch(str(spec["R8"]["logic"]))
        if self.r8_logic is None:
            raise SpecError(f"unsupported R8.logic {spec['R8']['logic']!r}")

    def score_all(self) -> list[RowResult]:
        return [self.score(row) for row in self.rows]

    def score(self, row: ParsedRow) -> RowResult:
        outcomes: list[str] = []
        fired_rules: list[str] = []
        self._suppress_r1 = False
        self._suppress_r2 = False

        for rule_id, terminal in self.pipeline:
            outcome = self.handlers[rule_id](row)
            if outcome is None:
                continue
            if outcome not in self.output_classes:
                raise SpecError(f"{rule_id} produced undeclared output class {outcome!r}")
            outcomes.append(outcome)
            fired_rules.append(rule_id)
            if terminal:
                break

        actual_class = max(outcomes, key=self.precedence.__getitem__) if outcomes else self.default_class
        return RowResult(actual_class=actual_class, fired_rules=tuple(fired_rules))

    def _stats_for(self, row: ParsedRow) -> VendorStats:
        return self.vendor_stats.get(row.raw.get("vendor_id", "").strip(), VendorStats(None, None, 0))

    def _r6(self, row: ParsedRow) -> str | None:
        return str(self.spec["R6"]["on_fire"]) if not row.is_valid else None

    def _r7(self, row: ParsedRow) -> str | None:
        if row.amount is None:
            return None
        symbol = self.r7_condition.group("op")
        threshold = _decimal(self.r7_condition.group("value"), "R7.condition")
        if _comparison(symbol)(row.amount, threshold):
            self._suppress_r1 = True
            self._suppress_r2 = True
            return str(self.spec["R7"]["on_fire"])
        return None

    def _r3(self, row: ParsedRow) -> str | None:
        stats = self._stats_for(row)
        min_prior_n = _decimal(self.spec["R3"]["min_prior_n"], "R3.min_prior_n")
        if Decimal(stats.n) < min_prior_n:
            self._suppress_r1 = True
            return str(self.spec["R3"]["on_fire"])
        return None

    def _r8(self, row: ParsedRow) -> str | None:
        stats = self._stats_for(row)
        if row.amount is None or stats.mean is None or stats.sd is None:
            return None

        sd_test = _comparison(self.r8_condition.group("sd_op"))(
            stats.sd,
            _decimal(self.r8_condition.group("sd_value"), "R8.condition vendor_sd"),
        )
        n_test = _comparison(self.r8_condition.group("n_op"))(
            Decimal(stats.n),
            _decimal(self.r8_condition.group("n_value"), "R8.condition prior_n"),
        )
        if not (sd_test and n_test):
            return None

        self._suppress_r1 = True
        comparison = _comparison(self.r8_logic.group("op"))
        return (
            self.r8_logic.group("true_class")
            if comparison(row.amount, stats.mean)
            else self.r8_logic.group("false_class")
        )

    def _r1(self, row: ParsedRow) -> str | None:
        if self._suppress_r1 or row.amount is None:
            return None
        stats = self._stats_for(row)
        if stats.mean is None or stats.sd is None:
            raise SpecError("R1 reached a row without usable vendor history; check R3 ordering")
        if not stats.sd:
            raise SpecError("R1 reached zero vendor variance; check R8 ordering and condition")

        z_score = abs((row.amount - stats.mean) / stats.sd)
        threshold = _decimal(self.spec["R1"]["threshold"], "R1.threshold")
        comparison = _comparison(str(self.spec["R1"]["comparison"]))
        return str(self.spec["R1"]["on_fire"]) if comparison(z_score, threshold) else None

    def _r2(self, row: ParsedRow) -> str | None:
        if self._suppress_r2 or row.amount is None:
            return None
        fraction = _decimal(self.spec["R2"]["window_fraction"], "R2.window_fraction")
        for configured_limit in self.spec["R2"]["limits"]:
            limit = _decimal(configured_limit, "R2.limits")
            if fraction * limit <= row.amount < limit:
                return str(self.spec["R2"]["on_fire"])
        return None

    def _r4(self, row: ParsedRow) -> str | None:
        if row.timestamp is None:
            return None
        allowed_window = self.spec["R4"]["time_of_day_only"]["allowed_window"]
        if len(allowed_window) != 2:
            raise SpecError("R4.time_of_day_only.allowed_window must contain start and end")
        start = _parse_clock(allowed_window[0], "R4 allowed-window start")
        end = _parse_clock(allowed_window[1], "R4 allowed-window end")
        local_time = row.timestamp.time().replace(tzinfo=None)
        return str(self.spec["R4"]["on_fire"]) if local_time < start or local_time > end else None

    def _r5(self, row: ParsedRow) -> str | None:
        return str(self.spec["R5"]["on_fire"]) if row.index in self.duplicate_indexes else None


def score_file(rules_path: Path, input_path: Path, output_path: Path) -> Counter[str]:
    spec = _read_rule_spec(rules_path)
    required_fields = tuple(str(field) for field in spec["R6"]["required_fields"])
    fieldnames, raw_rows = _read_csv(input_path)

    missing_columns = [field for field in required_fields if field not in fieldnames]
    if missing_columns:
        raise ValueError(f"{input_path} is missing required columns: {', '.join(missing_columns)}")

    parsed_rows = _parse_rows(raw_rows, required_fields)
    results = RuleEngine(spec, parsed_rows).score_all()

    output_fields = list(fieldnames)
    for result_field in (ACTUAL_CLASS_COLUMN, FIRED_RULES_COLUMN):
        if result_field not in output_fields:
            output_fields.append(result_field)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        for source, result in zip(raw_rows, results):
            output_row = dict(source)
            output_row[ACTUAL_CLASS_COLUMN] = result.actual_class
            output_row[FIRED_RULES_COLUMN] = ";".join(result.fired_rules)
            writer.writerow(output_row)

    return Counter(result.actual_class for result in results)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply spec/rules.yaml to AP transactions in configured pipeline order."
    )
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    spec = _read_rule_spec(args.rules)
    counts = score_file(args.rules, args.input, args.output)
    print(f"Wrote {args.output} ({sum(counts.values())} rows)")
    for output_class in spec["meta"]["output_classes"]:
        print(f"{output_class}: {counts[str(output_class)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
