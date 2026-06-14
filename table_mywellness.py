#!/usr/bin/env python3
# /// script
# dependencies = ["tabulate>=0.9"]
# ///
import argparse
from collections import defaultdict
from pathlib import Path

from mywellness_data import (
    DEFAULT_DETAILS,
    DEFAULT_TABLE_METRICS,
    iter_measure_rows,
    load_measurement_details,
    measure_display_value,
    measurement_names,
    metric_matches,
)


def pivot_rows(
    details: list[dict[str, object]],
    metrics: list[str],
    *,
    by_session: bool,
) -> list[dict[str, object]]:
    row_headers: dict[tuple[str, str], dict[str, object]] = {}
    values_by_row: dict[tuple[str, str], dict[str, object]] = defaultdict(dict)
    session_ids_by_row: dict[tuple[str, str], dict[str, None]] = defaultdict(dict)

    for row in iter_measure_rows(details):
        session_id = row.get("sessionId")
        measured_on = row.get("measuredOn")
        name = row.get("name")
        if not isinstance(session_id, str) or not isinstance(measured_on, str):
            continue
        if not isinstance(name, str) or name not in metrics:
            continue

        key = (measured_on, session_id if by_session else "")
        row_headers.setdefault(
            key,
            {
                "measuredOn": measured_on,
                "sessionIds": "",
            },
        )
        session_ids_by_row[key].setdefault(session_id, None)
        values_by_row[key][name] = measure_display_value(row)

    table_rows: list[dict[str, object]] = []
    for key in sorted(row_headers):
        table_row = dict(row_headers[key])
        table_row["sessionIds"] = ", ".join(
            session_id[:8] for session_id in session_ids_by_row[key]
        )
        for metric in metrics:
            table_row[metric] = values_by_row[key].get(metric, "")
        table_rows.append(table_row)
    return table_rows


def select_metrics(
    details: list[dict[str, object]],
    filters: list[str],
    include_all: bool,
) -> list[str]:
    available = measurement_names(details)
    if include_all:
        return available
    if filters:
        return [name for name in available if metric_matches(name, filters)]
    return [name for name in DEFAULT_TABLE_METRICS if name in available]


def print_metrics(details: list[dict[str, object]]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in iter_measure_rows(details):
        name = row.get("name")
        if isinstance(name, str) and name:
            counts[name] += 1
    for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"{count:>3}  {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print Mywellness measurements as a table.")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help="Case-insensitive substring filter. Can be passed multiple times.",
    )
    parser.add_argument("--all", action="store_true", help="Include every metric as a column.")
    parser.add_argument("--list-metrics", action="store_true")
    parser.add_argument("--format", default="github", help="tabulate format, e.g. github, simple, rounded_grid.")
    parser.add_argument("--max-session-id", type=int, default=8)
    parser.add_argument("--by-session", action="store_true", help="Use one row per v1 userMeasurementId.")
    return parser.parse_args()


def main() -> None:
    from tabulate import tabulate

    args = parse_args()
    details = load_measurement_details(args.details)

    if args.list_metrics:
        print_metrics(details)
        return

    metrics = select_metrics(details, filters=args.metric, include_all=args.all)
    assert metrics, "No matching metrics found"

    rows = pivot_rows(details, metrics, by_session=args.by_session)
    assert rows, "No measurement sessions matched the selected metrics"

    headers = ["measuredOn", "sessionIds", *metrics]
    table = []
    for row in rows:
        session_ids = str(row["sessionIds"])
        table.append(
            [
                row["measuredOn"],
                ", ".join(
                    session_id[: args.max_session_id]
                    for session_id in session_ids.split(", ")
                    if session_id
                ),
                *[row.get(metric, "") for metric in metrics],
            ]
        )
    print(tabulate(table, headers=headers, tablefmt=args.format, missingval=""))


if __name__ == "__main__":
    main()
