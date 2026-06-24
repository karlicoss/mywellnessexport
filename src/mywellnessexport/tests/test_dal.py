from __future__ import annotations

import json
from pathlib import Path

from mywellnessexport.common import JsonObject, session_file_name
from mywellnessexport.dal import DAL, measurement_table
from mywellnessexport.export import _scrub_for_export


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True))


def test_dal_filters_profile_measurements(tmp_path: Path) -> None:
    body_scan: JsonObject = {
        "userMeasurementId": "scan-1",
        "measuredOn": "2026-06-09 18:21:13 +01:00",
        "measureName": "Technogym Health",
        "measuredWhere": "HealthStation",
        "facilityId": "facility-1",
        "measures": [
            {
                "name": "Weight",
                "formattedDisplayValue": "70.0",
                "category": "BodyComposition",
                "descriptor": {"unitOfMeasureString": "kg"},
            }
        ],
    }
    profile: JsonObject = {
        "userMeasurementId": "profile-1",
        "measuredOn": "2023-01-20 13:14:57 +00:00",
        "measureName": "BMI",
        "measuredWhere": "WebSiteUserProfile",
        "facilityId": None,
        "measures": [
            {
                "name": "BMI",
                "formattedDisplayValue": "22.2",
            }
        ],
    }
    performance: JsonObject = {
        "userMeasurementId": "strength-1",
        "measuredOn": "2023-02-03 12:52:48 +00:00",
        "measureName": "1RM Artis Circuit",
        "measuredWhere": "Test",
        "facilityId": "facility-1",
        "measures": [
            {
                "name": "1RM Chest Press Biostrength",
                "formattedDisplayValue": "85",
                "category": "Performance",
            }
        ],
    }

    sessions: list[JsonObject] = [body_scan, profile, performance]
    _write_json(tmp_path / "profile.json", {"id": "user-1"})
    for session in sessions:
        _write_json(tmp_path / "sessions" / session_file_name(session), session)

    dal = DAL(tmp_path)

    assert [session.id for session in dal.sessions()] == ["profile-1", "strength-1", "scan-1"]
    assert [session.id for session in dal.body_scan_sessions()] == ["scan-1"]
    assert [session.id for session in dal.performance_sessions()] == ["strength-1"]
    assert [row["name"] for row in dal.measure_rows()] == ["BMI", "1RM Chest Press Biostrength", "Weight"]

    table = measurement_table(dal, metric_filters=[], include_profile=False)
    assert "scan-1" in table
    assert "profile-1" not in table
    assert "strength-1" not in table
    assert "Weight" in table
    assert "BMI" not in table

    performance_table = measurement_table(dal, metric_filters=[], include_profile=False, performance=True)
    assert "strength-1" in performance_table
    assert "1RM Chest Press Biostrength" in performance_table
    assert "scan-1" not in performance_table


def test_scrub_for_export_removes_tokens_and_passwords() -> None:
    raw = {
        "token": "secret-token",
        "data": {
            "password": "secret-password",
            "nested": [{"Token": "secret-token-2", "value": 1}],
        },
    }
    assert _scrub_for_export(raw) == {"data": {"nested": [{"value": 1}]}}
