from __future__ import annotations

import argparse
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import Json, JsonObject


def _is_profile_measurement_session(session: JsonObject) -> bool:
    # These look like signup/profile biometrics, not device measurement sessions.
    return session.get("measuredWhere") == "WebSiteUserProfile" and session.get("facilityId") is None


def _session_measure_categories(session: Session) -> set[str]:
    categories: set[str] = set()
    for measure in session.measures():
        category = measure.raw.get("category")
        assert category is None or isinstance(category, str), measure.raw
        if category is not None:
            categories.add(category)
    return categories


def _is_body_composition_session(session: Session) -> bool:
    if _is_profile_measurement_session(session.raw):
        return False
    categories = _session_measure_categories(session)
    return bool(categories) and categories <= {"Anthropometric", "BodyComposition"}


def _is_performance_session(session: Session) -> bool:
    return "Performance" in _session_measure_categories(session)


def _measure_display_value(measure: JsonObject) -> Json:
    for field in ("formattedDisplayValue", "displayValue", "value", "storedValue"):
        value = measure.get(field)
        if value not in (None, ""):
            return value
    return None


def _metric_matches(name: str, filters: Sequence[str]) -> bool:
    if not filters:
        return True
    normalized = name.casefold()
    return any(filter_text.casefold() in normalized for filter_text in filters)


@dataclass(frozen=True)
class Snapshot:
    root: Path
    profile: JsonObject
    sessions_raw: list[JsonObject]

    @classmethod
    def load(cls, source: Path | str) -> Snapshot:
        root = Path(source)
        if root.is_file():
            root = root.parent
        root = root.resolve()

        profile = json.loads((root / "profile.json").read_text())
        assert isinstance(profile, dict), profile

        sessions: list[JsonObject] = []
        for path in sorted((root / "sessions").glob("*.json")):
            session = json.loads(path.read_text())
            assert isinstance(session, dict), session
            measurement_id = session["userMeasurementId"]
            assert isinstance(measurement_id, str), session
            sessions.append(session)
        return cls(root=root, profile=profile, sessions_raw=sessions)


@dataclass(frozen=True)
class Measure:
    session: Session
    raw: JsonObject

    @property
    def name(self) -> str:
        value = self.raw.get("name")
        assert isinstance(value, str), value
        return value

    @property
    def display_value(self) -> Json:
        return _measure_display_value(self.raw)

    def row(self) -> JsonObject:
        # Mywellness omits these nested objects on simple profile measures.
        descriptor = self.raw.get("descriptor", {})
        assert isinstance(descriptor, dict), descriptor
        ideal_range = self.raw.get("idealRange", {})
        assert isinstance(ideal_range, dict), ideal_range
        return {
            "sessionId": self.session.id,
            "measuredOn": self.session.measured_on_raw,
            "measureDescriptorId": self.raw.get("measureDescriptorId") or self.session.raw.get("measureDescriptorId"),
            "biometricDescriptorId": self.raw.get("biometricDescriptorId"),
            "name": self.raw.get("name"),
            "type": self.raw.get("type"),
            "category": self.raw.get("category"),
            "subCategory": self.raw.get("subCategory"),
            "value": self.raw.get("value"),
            "storedValue": self.raw.get("storedValue"),
            "displayValue": self.raw.get("displayValue"),
            "formattedDisplayValue": self.raw.get("formattedDisplayValue"),
            "unitOfMeasure": self.raw.get("unitOfMeasure"),
            "descriptorUnitOfMeasure": descriptor.get("unitOfMeasure"),
            "descriptorUnitOfMeasureString": descriptor.get("unitOfMeasureString"),
            "idealRangeMin": ideal_range.get("min"),
            "idealRangeMax": ideal_range.get("max"),
            "facilityId": self.raw.get("facilityId") or self.session.raw.get("facilityId"),
            "measuredBy": self.raw.get("measuredBy") or self.session.raw.get("measuredBy"),
            "measuredWhere": self.session.raw.get("measuredWhere"),
            "calculated": self.raw.get("calculated"),
            "isDefaultValue": self.raw.get("isDefaultValue"),
        }


@dataclass(frozen=True)
class Session:
    snapshot: Snapshot
    raw: JsonObject

    @property
    def id(self) -> str:
        value = self.raw["userMeasurementId"]
        assert isinstance(value, str), value
        return value

    @property
    def measured_on_raw(self) -> str:
        value = self.raw["measuredOn"]
        assert isinstance(value, str), value
        return value

    @property
    def measured_on(self) -> datetime:
        value = self.measured_on_raw.strip()
        assert value, "empty measuredOn value"
        return datetime.fromisoformat(value)

    @property
    def measure_name(self) -> str:
        value = self.raw.get("measureName", "")
        assert isinstance(value, str), value
        return value

    @property
    def measured_where(self) -> str | None:
        value = self.raw.get("measuredWhere")
        assert value is None or isinstance(value, str), value
        return value

    def measures(self) -> Iterator[Measure]:
        measures = self.raw.get("measures")
        assert isinstance(measures, list), self.raw
        for measure in measures:
            assert isinstance(measure, dict), measure
            yield Measure(session=self, raw=measure)


class DAL:
    """Offline access to one Mywellness export snapshot."""

    def __init__(self, source: Path | str) -> None:
        self.snapshot = Snapshot.load(source)

    def sessions(self) -> Iterator[Session]:
        sessions = [Session(snapshot=self.snapshot, raw=raw) for raw in self.snapshot.sessions_raw]
        yield from sorted(sessions, key=lambda item: (item.measured_on, item.id))

    def body_scan_sessions(self) -> Iterator[Session]:
        yield from self.body_composition_sessions()

    def body_composition_sessions(self) -> Iterator[Session]:
        for session in self.sessions():
            if _is_body_composition_session(session):
                yield session

    def performance_sessions(self) -> Iterator[Session]:
        for session in self.sessions():
            if _is_performance_session(session):
                yield session

    def profile_sessions(self) -> Iterator[Session]:
        for session in self.sessions():
            if _is_profile_measurement_session(session.raw):
                yield session

    def measures(self) -> Iterator[Measure]:
        for session in self.sessions():
            yield from session.measures()

    def measure_rows(self) -> Iterator[JsonObject]:
        for measure in self.measures():
            yield measure.row()

    def measurement_names(self) -> list[str]:
        names: dict[str, None] = {}
        for measure in self.measures():
            names.setdefault(measure.name, None)
        return sorted(names)


def _table_cell(value: Json) -> str:
    if value is None:
        return ""
    return str(value)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Json]]) -> str:
    from tabulate import tabulate

    rendered = [[_table_cell(value) for value in row] for row in rows]
    return str(tabulate(rendered, headers=headers, tablefmt="github"))


def measurement_table(
    dal: DAL,
    *,
    metric_filters: Sequence[str],
    include_profile: bool,
    performance: bool = False,
) -> str:
    if performance:
        sessions = list(dal.performance_sessions())
    elif include_profile:
        sessions = [*dal.body_composition_sessions(), *dal.profile_sessions()]
    else:
        sessions = list(dal.body_composition_sessions())
    all_names = sorted({measure.name for session in sessions for measure in session.measures()})
    _default_table_metrics = [
        "Weight",
        "BMI",
        "% fat mass",
        "Body fat mass (BFM)",
        "Fat free mass (FFM)",
        "Skeletal muscle mass (SMM)",
        "Total body water (TBW)",
        "Basal metabolic rate (BMR)",
        "Phase angle",
    ]
    metrics = [
        name
        for name in _default_table_metrics
        if name in all_names and _metric_matches(name, metric_filters)
    ]  # fmt: skip
    extra_metrics = [
        name
        for name in all_names
        if name not in metrics and _metric_matches(name, metric_filters)
    ]  # fmt: skip
    metrics.extend(extra_metrics)

    headers = ["measuredOn", "sessionId", *metrics]
    rows: list[list[Json]] = []
    for session in sessions:
        values = {measure.name: measure.display_value for measure in session.measures()}
        rows.append([session.measured_on_raw, session.id, *(values.get(metric) for metric in metrics)])
    return _table(headers, rows)


def demo(dal: DAL) -> None:
    sessions = list(dal.sessions())
    print(f"Source: {dal.snapshot.root}")
    print(f"Sessions: {len(sessions)}")
    print(f"Measures: {sum(1 for _ in dal.measures())}")
    if sessions:
        print(f"Range: {sessions[0].measured_on.isoformat()} to {sessions[-1].measured_on.isoformat()}")


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Mywellness exports offline through the DAL.")
    parser.add_argument("--source", type=Path, required=True, help="Mywellness export directory")
    parser.add_argument("--include-profile", action="store_true", help="include WebSiteUserProfile rows")
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help="case-insensitive metric name substring to include; can be passed multiple times",
    )
    parser.add_argument("--summary", action="store_true", help="print archive summary instead of the measurement table")
    return parser


def main() -> None:
    args = _make_parser().parse_args()
    dal = DAL(args.source)
    if args.summary:
        demo(dal)
    else:
        body_composition = measurement_table(
            dal,
            metric_filters=args.metric,
            include_profile=args.include_profile,
        )
        print("Body composition")
        print(body_composition)

        performance_sessions = list(dal.performance_sessions())
        if performance_sessions:
            performance = measurement_table(
                dal,
                metric_filters=args.metric,
                include_profile=False,
                performance=True,
            )
            print()
            print("Biostrength")
            print(performance)


if __name__ == "__main__":
    main()
