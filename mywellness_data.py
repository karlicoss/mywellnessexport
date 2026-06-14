from collections.abc import Iterator
import json
from pathlib import Path


DEFAULT_DETAILS = Path("mywellness_export/measurement_details.json")

DEFAULT_TABLE_METRICS = [
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


ROW_FIELDS = [
    "sessionId",
    "measuredOn",
    "measureDescriptorId",
    "biometricDescriptorId",
    "name",
    "type",
    "category",
    "subCategory",
    "value",
    "storedValue",
    "displayValue",
    "formattedDisplayValue",
    "unitOfMeasure",
    "descriptorUnitOfMeasure",
    "descriptorUnitOfMeasureString",
    "idealRangeMin",
    "idealRangeMax",
    "facilityId",
    "measuredBy",
    "measuredWhere",
    "calculated",
    "isDefaultValue",
]


def dict_or_empty(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    return {}


def load_measurement_details(path: Path = DEFAULT_DETAILS) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    assert isinstance(data, list), data
    for item in data:
        assert isinstance(item, dict), item
    return data


def is_profile_measurement_session(session: dict[str, object]) -> bool:
    # These look like signup/profile biometrics, not device measurement sessions.
    return session.get("measuredWhere") == "WebSiteUserProfile" and session.get("facilityId") is None


def measure_display_value(measure: dict[str, object]) -> object:
    for field in ("formattedDisplayValue", "displayValue", "value", "storedValue"):
        value = measure.get(field)
        if value not in (None, ""):
            return value
    return None


def measure_row(
    session: dict[str, object],
    measure: dict[str, object],
) -> dict[str, object]:
    descriptor = dict_or_empty(measure.get("descriptor"))
    ideal_range = dict_or_empty(measure.get("idealRange"))
    return {
        "sessionId": session.get("userMeasurementId"),
        "measuredOn": session.get("measuredOn") or measure.get("measuredOn"),
        "measureDescriptorId": measure.get("measureDescriptorId")
        or session.get("measureDescriptorId"),
        "biometricDescriptorId": measure.get("biometricDescriptorId"),
        "name": measure.get("name"),
        "type": measure.get("type"),
        "category": measure.get("category"),
        "subCategory": measure.get("subCategory"),
        "value": measure.get("value"),
        "storedValue": measure.get("storedValue"),
        "displayValue": measure.get("displayValue"),
        "formattedDisplayValue": measure.get("formattedDisplayValue"),
        "unitOfMeasure": measure.get("unitOfMeasure"),
        "descriptorUnitOfMeasure": descriptor.get("unitOfMeasure"),
        "descriptorUnitOfMeasureString": descriptor.get("unitOfMeasureString"),
        "idealRangeMin": ideal_range.get("min"),
        "idealRangeMax": ideal_range.get("max"),
        "facilityId": measure.get("facilityId") or session.get("facilityId"),
        "measuredBy": measure.get("measuredBy") or session.get("measuredBy"),
        "measuredWhere": session.get("measuredWhere"),
        "calculated": measure.get("calculated"),
        "isDefaultValue": measure.get("isDefaultValue"),
    }


def iter_measure_rows(
    details: list[dict[str, object]],
) -> Iterator[dict[str, object]]:
    for session in details:
        if is_profile_measurement_session(session):
            continue
        measures = session.get("measures")
        assert isinstance(measures, list), session
        for measure in measures:
            assert isinstance(measure, dict), measure
            yield measure_row(session, measure)


def measurement_names(details: list[dict[str, object]]) -> list[str]:
    names: dict[str, None] = {}
    for row in iter_measure_rows(details):
        name = row.get("name")
        if isinstance(name, str) and name:
            names.setdefault(name, None)
    return sorted(names)


def metric_matches(name: str, filters: list[str]) -> bool:
    if not filters:
        return True
    normalized = name.casefold()
    return any(filter_text.casefold() in normalized for filter_text in filters)
