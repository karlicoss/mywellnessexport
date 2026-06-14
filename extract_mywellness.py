#!/usr/bin/env python3
import argparse
import csv
import html
import json
import re
import time
from pathlib import Path
from datetime import date
from urllib.parse import quote, urljoin

import requests


DEFAULT_BASE_URL = "https://v1.mywellness.com"
DEFAULT_CLUB = "cloud"
DEFAULT_PASSWORD_FILE = "password"
DEFAULT_OUTPUT_DIR = "mywellness_export"


class MywellnessClient:
    def __init__(self, username: str, password: str, base_url: str, club: str) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.club = club.strip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                )
            }
        )
        self.config: dict[str, str] = {}
        self.user: dict[str, object] = {}

    def login(self) -> None:
        return_url = f"{self.base_url}/{self.club}/BodyMeasurement/"
        login_start = f"{self.base_url}/{self.club}/User/Register?ReturnUrl={quote(return_url, safe='')}"
        response = self.session.get(login_start, timeout=30)
        response.raise_for_status()

        match = re.search(r'<form[^>]+action=["\']([^"\']+)', response.text, re.IGNORECASE)
        assert match, "Could not find login form action"
        login_url = urljoin(response.url, html.unescape(match.group(1)))
        response = self.session.post(
            login_url,
            data={
                "UserBinder.MifareId": "",
                "UserBinder.IsFromLogin": "False",
                "UserBinder.ForceJoinFacility": "",
                "UserBinder.Username": self.username,
                "UserBinder.Password": self.password,
                "UserBinder.KeepMeLogged": "false",
            },
            timeout=30,
        )
        response.raise_for_status()
        assert "UserBinder.Password" not in response.text, response.url

        index = self.session.get(return_url, timeout=30)
        index.raise_for_status()
        self._load_page_config(index.text)

    def _load_page_config(self, html: str) -> None:
        self.config = dict(
            re.findall(r'(API_HOST|WORKOUT_HOST|API_APP_ID):\s*"([^"]+)"', html)
        )
        assert self.config.keys() >= {"WORKOUT_HOST", "API_APP_ID"}, self.config

        match = re.search(r"EU\.currentUser = JSON\.parse\('(.+?)'\);", html)
        assert match, "Could not find EU.currentUser in authenticated page"
        self.user = json.loads(match.group(1).encode().decode("unicode_escape"))
        assert self.user.get("id"), "Could not find authenticated user id"
        assert self.user.get("token"), "Could not find authenticated API token"

    def api_post(self, endpoint: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        payload = dict(payload or {})
        culture = str(payload.setdefault("_c", self.user.get("culture") or "en-US"))
        payload.setdefault("AppId", self.config["API_APP_ID"])
        payload.setdefault("token", self.user["token"])

        url = (
            f"https://{self.config['WORKOUT_HOST']}"
            f"/Biometrics/User/{self.user['id']}/{endpoint}?_c={culture}"
        )
        headers = {
            "X-MWAPPS-CLIENT": "enduserweb",
            "X-MWAPPS-APPID": self.config["API_APP_ID"],
        }
        for attempt in range(3):
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code not in {502, 503, 504} or attempt == 2:
                break
            time.sleep(1)
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict), data
        if "token" in data:
            self.user["token"] = data["token"]
        return data

    def measurement_details(self, user_measurement_id: str) -> dict[str, object]:
        data = self.api_post(
            "MeasurementDetails",
            {"userMeasurementId": user_measurement_id},
        )
        details = data.get("data")
        assert isinstance(details, dict), data
        return details

    def biometric_trends(
        self,
        biometric_descriptor_id: str,
        start_day: str,
        trends: str = "Montly",
        number_of_trends: int = 12,
    ) -> dict[str, object]:
        data = self.api_post(
            "BiometricTrendsNew",
            {
                "biometricDescriptorId": biometric_descriptor_id,
                "startDay": start_day,
                "trends": trends,
                "numberOfTrends": number_of_trends,
            },
        )
        trend_data = data.get("data")
        assert isinstance(trend_data, dict), data
        return trend_data


def read_password(path: Path) -> str:
    password = path.read_text().strip()
    assert password, path
    return password


def collect_measurement_ids(*objects: object) -> list[str]:
    found: dict[str, None] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            measurement_id = value.get("userMeasurementId")
            if isinstance(measurement_id, str) and measurement_id:
                found.setdefault(measurement_id, None)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for obj in objects:
        visit(obj)
    return list(found)


def collect_available_biometric_ids(available: dict[str, object]) -> list[str]:
    data = available.get("data")
    assert isinstance(data, dict), available
    biometrics = data.get("biometrics")
    assert isinstance(biometrics, list), data

    ids: list[str] = []
    for biometric in biometrics:
        assert isinstance(biometric, dict), biometric
        biometric_descriptor_id = biometric.get("biometricDescriptorId")
        assert isinstance(biometric_descriptor_id, str), biometric
        ids.append(biometric_descriptor_id)
    return ids


def collect_trend_measurement_ids(trends: dict[str, dict[str, object]]) -> list[str]:
    found: dict[str, None] = {}
    for trend_data in trends.values():
        points = trend_data.get("points")
        if points is None:
            continue
        assert isinstance(points, list), trend_data
        for point in points:
            assert isinstance(point, dict), point
            measurement_id = point.get("userMeasurementId")
            if isinstance(measurement_id, str) and measurement_id:
                found.setdefault(measurement_id, None)
    return list(found)


def flatten_measure(measurement: dict[str, object], measure: dict[str, object]) -> dict[str, object]:
    descriptor = measure.get("descriptor")
    ideal_range = measure.get("idealRange")
    if not isinstance(descriptor, dict):
        descriptor = {}
    if not isinstance(ideal_range, dict):
        ideal_range = {}

    return {
        "userMeasurementId": measurement.get("userMeasurementId"),
        "measuredOn": measurement.get("measuredOn") or measure.get("measuredOn"),
        "measureDescriptorId": measure.get("measureDescriptorId")
        or measurement.get("measureDescriptorId"),
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
        "facilityId": measure.get("facilityId") or measurement.get("facilityId"),
        "measuredBy": measure.get("measuredBy") or measurement.get("measuredBy"),
        "measuredWhere": measurement.get("measuredWhere"),
        "calculated": measure.get("calculated"),
        "isDefaultValue": measure.get("isDefaultValue"),
    }


def flatten_trend_value(
    biometric_descriptor_id: str,
    value: dict[str, object],
    metadata: dict[str, object],
) -> dict[str, object]:
    descriptor = metadata.get("descriptor")
    ideal_range = metadata.get("idealRange")
    if not isinstance(descriptor, dict):
        descriptor = {}
    if not isinstance(ideal_range, dict):
        ideal_range = {}

    measured_on = value.get("measuredOn")
    return {
        "userMeasurementId": f"trend:{measured_on}",
        "measuredOn": measured_on,
        "measureDescriptorId": metadata.get("measureDescriptorId"),
        "biometricDescriptorId": biometric_descriptor_id,
        "name": metadata.get("name"),
        "type": metadata.get("type"),
        "category": metadata.get("category"),
        "subCategory": metadata.get("subCategory"),
        "value": value.get("value"),
        "storedValue": value.get("value"),
        "displayValue": value.get("displayValue"),
        "formattedDisplayValue": value.get("displayValue"),
        "unitOfMeasure": metadata.get("unitOfMeasure"),
        "descriptorUnitOfMeasure": descriptor.get("unitOfMeasure"),
        "descriptorUnitOfMeasureString": descriptor.get("unitOfMeasureString"),
        "idealRangeMin": ideal_range.get("min"),
        "idealRangeMax": ideal_range.get("max"),
        "facilityId": None,
        "measuredBy": value.get("measuredBy"),
        "measuredWhere": None,
        "calculated": metadata.get("calculated"),
        "isDefaultValue": None,
    }


def collect_trend_only_rows(
    latest: dict[str, object],
    available: dict[str, object],
    existing_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    available_data = available.get("data")
    latest_data = latest.get("data")
    assert isinstance(available_data, dict), available
    assert isinstance(latest_data, dict), latest

    biometrics = available_data.get("biometrics")
    trend_values = latest_data.get("trendValues")
    assert isinstance(biometrics, list), available_data
    assert isinstance(trend_values, list), latest_data

    metadata_by_id = {
        item["biometricDescriptorId"]: item
        for item in biometrics
        if isinstance(item, dict) and item.get("biometricDescriptorId")
    }
    existing_keys = {
        (row.get("measuredOn"), row.get("biometricDescriptorId"))
        for row in existing_rows
    }
    rows: list[dict[str, object]] = []

    for trend in trend_values:
        assert isinstance(trend, dict), trend
        biometric_descriptor_id = trend.get("biometricDescriptorId")
        assert isinstance(biometric_descriptor_id, str), trend
        metadata = metadata_by_id.get(biometric_descriptor_id)
        assert metadata is not None, trend

        values = trend.get("values")
        assert isinstance(values, list), trend
        for value in values:
            assert isinstance(value, dict), value
            measured_on = value.get("measuredOn")
            key = (measured_on, biometric_descriptor_id)
            if key in existing_keys:
                continue
            row = flatten_trend_value(biometric_descriptor_id, value, metadata)
            rows.append(row)
            existing_keys.add(key)

    return rows


def scrub_for_export(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: scrub_for_export(child)
            for key, child in value.items()
            if key.lower() not in {"password", "token"}
        }
    if isinstance(value, list):
        return [scrub_for_export(child) for child in value]
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(scrub_for_export(value), indent=2, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_body_measurements(client: MywellnessClient, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    descriptors = client.api_post("MeasurementsDescriptorList")
    latest = client.api_post("LastBiometricsMeasurements")
    available = client.api_post("AvailableBiometrics")
    trends = {
        biometric_descriptor_id: client.biometric_trends(
            biometric_descriptor_id,
            start_day=date.today().isoformat(),
        )
        for biometric_descriptor_id in collect_available_biometric_ids(available)
    }

    write_json(output_dir / "measurements_descriptor_list.json", descriptors)
    write_json(output_dir / "last_biometrics_measurements.json", latest)
    write_json(output_dir / "available_biometrics.json", available)
    write_json(output_dir / "biometric_trends.json", trends)

    measurement_ids = collect_measurement_ids(latest) + collect_trend_measurement_ids(trends)
    measurement_ids = list(dict.fromkeys(measurement_ids))
    assert measurement_ids, "No userMeasurementId values found in LastBiometricsMeasurements"

    details = [client.measurement_details(measurement_id) for measurement_id in measurement_ids]
    write_json(output_dir / "measurement_details.json", details)

    detail_rows: list[dict[str, object]] = []
    for measurement in details:
        measures = measurement.get("measures")
        assert isinstance(measures, list), measurement
        for measure in measures:
            assert isinstance(measure, dict), measure
            detail_rows.append(flatten_measure(measurement, measure))

    trend_only_rows = collect_trend_only_rows(latest, available, detail_rows)
    rows = detail_rows + trend_only_rows

    rows.sort(
        key=lambda row: (
            str(row.get("measuredOn") or ""),
            str(row.get("name") or ""),
            str(row.get("biometricDescriptorId") or ""),
        )
    )
    write_csv(output_dir / "body_measurements.csv", rows)

    session_ids = {row["userMeasurementId"] for row in rows}
    summary = {
        "detailed_measurement_session_count": len(details),
        "trend_only_measurement_session_count": len(
            {row["userMeasurementId"] for row in trend_only_rows}
        ),
        "measurement_session_count": len(session_ids),
        "detailed_measure_count": len(detail_rows),
        "trend_only_measure_count": len(trend_only_rows),
        "flattened_measure_count": len(rows),
        "files": [
            "body_measurements.csv",
            "measurement_details.json",
            "last_biometrics_measurements.json",
            "measurements_descriptor_list.json",
            "available_biometrics.json",
            "biometric_trends.json",
        ],
    }
    write_json(output_dir / "summary.json", summary)

    print(f"Exported {summary['measurement_session_count']} measurement sessions")
    print(f"Exported {summary['flattened_measure_count']} flattened measures")
    print(f"Output directory: {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Mywellness body measurements.")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password-file", type=Path, default=Path(DEFAULT_PASSWORD_FILE))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--club", default=DEFAULT_CLUB)
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = MywellnessClient(
        username=args.username,
        password=read_password(args.password_file),
        base_url=args.base_url,
        club=args.club,
    )
    client.login()
    export_body_measurements(client, args.output_dir)


if __name__ == "__main__":
    main()
