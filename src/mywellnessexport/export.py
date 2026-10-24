from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from hpikit.json import Json
from kloggy import get_logger

from .common import session_file_name

logger = get_logger(__name__)

DEFAULT_API_APP_ID = "EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9"
DEFAULT_API_HOST = "services.mywellness.com"
DEFAULT_WORKOUT_HOST = "workout.mywellness.com"
# The API spelling is intentional; "Monthly" returns 500.
DEFAULT_TREND_PERIOD = "Montly"
DEFAULT_TREND_COUNT = 12

# Related projects checked:
# - https://github.com/AeonDave/myWellnezz and https://github.com/mboogerd/mywellness use
#   Application/{appId}/Login plus class calendar/Core endpoints.
# - https://github.com/lcanis/gymexport and https://github.com/Matt-Mcl/workout-app scrape
#   the retired legacy portal.
# None exposes body scan history; that still needs
#   AvailableBiometrics -> BiometricTrendsNew -> MeasurementDetails.

_TRANSIENT_STATUS_CODES = {502}


def _scrub_for_export(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub_for_export(child) for key, child in value.items() if key.lower() not in {"password", "token"}
        }
    if isinstance(value, list):
        return [_scrub_for_export(child) for child in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_scrub_for_export(value), indent=2, ensure_ascii=False, sort_keys=True))


def _assert_unchanged(path: Path, value: Any) -> None:
    if not path.exists():
        return
    assert json.loads(path.read_text()) == _scrub_for_export(value), path


class MywellnessClient:
    def __init__(
        self,
        *,
        email: str,
        password: str,
    ) -> None:
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                )
            }
        )  # fmt: skip
        self.config = {
            "API_APP_ID": DEFAULT_API_APP_ID,
            "API_HOST": DEFAULT_API_HOST,
            "WORKOUT_HOST": DEFAULT_WORKOUT_HOST,
        }
        self.user: Json = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        attempts: int = 3,
        **kwargs: Any,
    ) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(attempts):
            response = self.session.request(method, url, **kwargs)
            if response.status_code not in _TRANSIENT_STATUS_CODES:
                return response
            if attempt != attempts - 1:
                time.sleep(1 << attempt)
        assert response is not None
        return response

    def login(self) -> None:
        response = self.request(
            "POST",
            f"https://{DEFAULT_API_HOST}/Application/{DEFAULT_API_APP_ID}/Login",
            params={"_c": "en-US"},
            json={
                "domain": "com.mywellness",
                "keepMeLoggedIn": False,
                "username": self.email,
                "password": self.password,
            },
            headers={
                "X-MWAPPS-CLIENT": "enduserweb",
                "X-MWAPPS-APPID": DEFAULT_API_APP_ID,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict), payload
        data = payload.get("data")
        assert isinstance(data, dict), payload
        user_context = data.get("userContext")
        assert isinstance(user_context, dict), payload
        user_id = user_context.get("id")
        token = payload.get("token")
        culture = user_context.get("defaultCulture")
        assert isinstance(user_id, str), user_context
        assert isinstance(token, str), payload.get("errors")
        assert isinstance(culture, str), user_context

        self.user = {
            "aspirationMap": None,
            "culture": culture,
            "id": user_id,
            "language": culture.partition("-")[0],
            "measurementSystem": user_context.get("measurementSystem"),
            "token": token,
            "windowTimeZoneId": user_context.get("timeZoneWindowsId"),
        }

    def api_post(self, endpoint: str, payload: Json | None = None) -> Json:
        request_payload: Json = {
            "_c": self.user.get("culture") or "en-US",
            "AppId": self.config["API_APP_ID"],
            "token": self.user["token"],
        }
        request_payload.update(payload or {})
        culture = str(request_payload["_c"])

        url = f"https://{self.config['WORKOUT_HOST']}/Biometrics/User/{self.user['id']}/{endpoint}"
        response = self.request(
            "POST",
            url,
            params={"_c": culture},
            json=request_payload,
            headers={
                "X-MWAPPS-CLIENT": "enduserweb",
                "X-MWAPPS-APPID": self.config["API_APP_ID"],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict), data
        assert isinstance(data.get("token"), str), data
        self.user["token"] = data["token"]
        return data

    def measurement_details(self, user_measurement_id: str) -> Json:
        data = self.api_post("MeasurementDetails", {"userMeasurementId": user_measurement_id})
        details = data.get("data")
        assert isinstance(details, dict), data
        return details

    def biometric_trends(
        self,
        *,
        biometric_descriptor_id: str,
        start_day: str,
        trends: str = DEFAULT_TREND_PERIOD,
        number_of_trends: int = DEFAULT_TREND_COUNT,
    ) -> Json:
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


def collect_measurement_ids(*objects: Any) -> list[str]:
    found: dict[str, None] = {}

    def visit(value: Any) -> None:
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


def collect_available_biometric_ids(available: Json) -> list[str]:
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


def collect_trend_measurement_ids(trends: dict[str, Json]) -> list[str]:
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


def export_account(
    *,
    client: MywellnessClient,
    output_directory: Path,
) -> None:
    client.login()

    descriptors = client.api_post("MeasurementsDescriptorList")
    latest = client.api_post("LastBiometricsMeasurements")
    available = client.api_post("AvailableBiometrics")
    trends = {
        biometric_descriptor_id: client.biometric_trends(
            biometric_descriptor_id=biometric_descriptor_id,
            start_day=date.today().isoformat(),
            number_of_trends=DEFAULT_TREND_COUNT,
        )
        for biometric_descriptor_id in collect_available_biometric_ids(available)
    }

    measurement_ids = collect_measurement_ids(latest) + collect_trend_measurement_ids(trends)
    measurement_ids = list(dict.fromkeys(measurement_ids))
    assert measurement_ids, "No userMeasurementId values found"

    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(output_directory / "profile.json", client.user)
    _write_json(output_directory / "service-config.json", client.config)
    _write_json(output_directory / "measurements-descriptor-list.json", descriptors)
    _write_json(output_directory / "available-biometrics.json", available)

    logger.info(f"Found {len(measurement_ids)} measurement sessions")
    for index, measurement_id in enumerate(measurement_ids, start=1):
        matching = sorted((output_directory / "sessions").glob(f"*_{measurement_id}.json"))
        if matching:
            details = json.loads(matching[0].read_text())
            assert isinstance(details, dict), details
            status = "existing"
        else:
            details = client.measurement_details(measurement_id)
            status = "new"

        session_path = output_directory / "sessions" / session_file_name(details)
        _assert_unchanged(session_path, details)
        if not session_path.exists():
            _write_json(session_path, details)

        measures = details.get("measures")
        assert isinstance(measures, list), details
        logger.info(
            f"[{index}/{len(measurement_ids)}] {details['measuredOn']} "
            f"{details.get('measureName', '')} ({status}, {len(measures)} measures)"
        )


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export raw Mywellness body measurement data.")
    parser.add_argument("--secrets", type=Path, required=True, help="JSON file containing email and password")
    parser.add_argument("--output", type=Path, required=True, help="Archive directory")
    return parser


def main() -> None:
    args = _make_parser().parse_args()
    secrets = json.loads(args.secrets.read_text())
    assert set(secrets) >= {"email", "password"}, sorted(secrets)

    client = MywellnessClient(
        email=secrets["email"],
        password=secrets["password"],
    )
    export_account(
        client=client,
        output_directory=args.output,
    )
    logger.info(f"Archive written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
