from __future__ import annotations

from typing import Any

type Json = Any
type JsonObject = dict[str, Json]


def safe_file_name(name: str) -> str:
    assert name not in {"", ".", ".."}, name
    assert "/" not in name, name
    assert "\\" not in name, name
    return name


def session_file_name(session: JsonObject) -> str:
    measured_on = session["measuredOn"]
    assert isinstance(measured_on, str), measured_on
    measurement_id = session["userMeasurementId"]
    assert isinstance(measurement_id, str), measurement_id

    timestamp = measured_on
    timestamp = (
        timestamp.replace("-", "")
        .replace(":", "")
        .replace(" ", "T")
        .replace("+", "p")
        .replace("/", "")
    )  # fmt: skip
    return safe_file_name(f"{timestamp}_{measurement_id}.json")
