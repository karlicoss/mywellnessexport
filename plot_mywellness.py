#!/usr/bin/env python3
# /// script
# dependencies = ["matplotlib>=3.8"]
# ///
import argparse
import html
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from mywellness_data import DEFAULT_DETAILS, iter_measure_rows, load_measurement_details


DEFAULT_OUTPUT_DIR = Path("mywellness_export/plots")


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    assert value, "empty measuredOn value"

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    assert False, value


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
        if not match:
            return None
        parsed = float(match.group(0).replace(",", "."))
    if not math.isfinite(parsed):
        return None
    return parsed


def row_value(row: dict[str, object]) -> float | None:
    for field in ("value", "storedValue", "displayValue", "formattedDisplayValue"):
        value = parse_number(row.get(field))
        if value is not None:
            return value
    return None


def read_rows(path: Path) -> list[dict[str, object]]:
    return list(iter_measure_rows(load_measurement_details(path)))


def metric_unit(rows: list[dict[str, object]]) -> str:
    for row in rows:
        for field in ("descriptorUnitOfMeasureString", "unitOfMeasure", "descriptorUnitOfMeasure"):
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def metric_matches(name: str, filters: list[str]) -> bool:
    if not filters:
        return True
    normalized = name.casefold()
    return any(filter_text.casefold() in normalized for filter_text in filters)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "measurement"


def collect_metrics(rows: list[dict[str, object]]) -> dict[str, list[tuple[datetime, float, dict[str, object]]]]:
    metrics: dict[str, list[tuple[datetime, float, dict[str, object]]]] = defaultdict(list)
    for row in rows:
        name = str(row.get("name") or "").strip()
        measured_on = str(row.get("measuredOn") or "").strip()
        value = row_value(row)
        if not name or not measured_on or value is None:
            continue
        metrics[name].append((parse_datetime(measured_on), value, row))

    for points in metrics.values():
        points.sort(key=lambda point: point[0])
    return dict(sorted(metrics.items()))


def ideal_range(points: list[tuple[datetime, float, dict[str, object]]]) -> tuple[float, float] | None:
    ranges = {
        (
            parse_number(row.get("idealRangeMin")),
            parse_number(row.get("idealRangeMax")),
        )
        for _, _, row in points
    }
    ranges = {(low, high) for low, high in ranges if low is not None and high is not None}
    if len(ranges) != 1:
        return None
    low, high = next(iter(ranges))
    assert low is not None and high is not None
    if low >= high:
        return None
    return low, high


def plot_metric(
    name: str,
    points: list[tuple[datetime, float, dict[str, object]]],
    output_dir: Path,
    image_format: str,
) -> Path:
    dates = [point[0] for point in points]
    values = [point[1] for point in points]
    unit = metric_unit([point[2] for point in points])

    fig, ax = plt.subplots(figsize=(9, 4.8), constrained_layout=True)
    ax.plot(dates, values, marker="o", linewidth=1.8, markersize=4.5, color="#2563eb")

    bounds = ideal_range(points)
    if bounds is not None:
        ax.axhspan(bounds[0], bounds[1], color="#16a34a", alpha=0.09, linewidth=0)

    ax.set_title(name)
    ax.set_xlabel("Date")
    ax.set_ylabel(f"{name} ({unit})" if unit else name)
    ax.grid(True, axis="y", color="#d4d4d8", linewidth=0.8, alpha=0.75)
    ax.grid(True, axis="x", color="#e4e4e7", linewidth=0.6, alpha=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))

    filename = f"{slugify(name)}.{image_format}"
    path = output_dir / filename
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def write_index(output_dir: Path, plots: list[tuple[str, Path, int]]) -> None:
    items = []
    for name, path, count in plots:
        rel = html.escape(path.name, quote=True)
        title = html.escape(name)
        items.append(
            f"<section><h2>{title}</h2><p>{count} points</p>"
            f'<img src="{rel}" alt="{title}"></section>'
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mywellness Body Measurements</title>
  <style>
    body {{ font: 16px/1.45 system-ui, sans-serif; margin: 32px; color: #18181b; }}
    main {{ max-width: 1040px; margin: 0 auto; }}
    section {{ margin: 0 0 32px; padding-bottom: 28px; border-bottom: 1px solid #e4e4e7; }}
    h1 {{ margin: 0 0 24px; font-size: 28px; }}
    h2 {{ margin: 0 0 4px; font-size: 18px; }}
    p {{ margin: 0 0 12px; color: #52525b; }}
    img {{ display: block; width: 100%; max-width: 960px; height: auto; }}
  </style>
</head>
<body>
<main>
  <h1>Mywellness Body Measurements</h1>
  {''.join(items)}
</main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(document)


def list_metrics(metrics: dict[str, list[tuple[datetime, float, dict[str, object]]]]) -> None:
    for name, points in sorted(metrics.items(), key=lambda item: (-len(item[1]), item[0])):
        unit = metric_unit([point[2] for point in points])
        suffix = f" [{unit}]" if unit else ""
        print(f"{len(points):>3}  {name}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot exported Mywellness body measurements.")
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help="Case-insensitive substring filter. Can be passed multiple times.",
    )
    parser.add_argument("--min-points", type=int, default=2)
    parser.add_argument("--format", choices=["png", "svg", "pdf"], default="png")
    parser.add_argument("--list-metrics", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_rows(args.details)
    metrics = collect_metrics(rows)

    if args.list_metrics:
        list_metrics(metrics)
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = [
        (name, points)
        for name, points in metrics.items()
        if metric_matches(name, args.metric) and len(points) >= args.min_points
    ]
    assert selected, "No matching numeric metrics with enough points"

    plots = [
        (name, plot_metric(name, points, args.output_dir, args.format), len(points))
        for name, points in selected
    ]
    write_index(args.output_dir, plots)

    print(f"Plotted {len(plots)} metrics")
    print(f"Output directory: {args.output_dir}")
    print(f"Index: {args.output_dir / 'index.html'}")


if __name__ == "__main__":
    main()
