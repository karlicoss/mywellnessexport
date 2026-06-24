from __future__ import annotations

import argparse
import html
import html.parser
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from .common import Json, JsonObject, session_file_name

DEFAULT_BASE_URL = "https://v1.mywellness.com"
DEFAULT_PORTAL = "cloud"
# The API spelling is intentional; "Monthly" returns 500.
DEFAULT_TREND_PERIOD = "Montly"
DEFAULT_TREND_COUNT = 12

# Related projects checked:
# - https://github.com/lcanis/gymexport uses the same legacy portal login
#   and token/AppId/culture service calls, but only for workout endpoints.
# - https://github.com/AeonDave/myWellnezz and https://github.com/mboogerd/mywellness
#   use Application/{appId}/Login plus class calendar/Core endpoints.
# - https://github.com/Matt-Mcl/workout-app scrapes legacy Training HTML.
# None exposes body scan history; that still needs
#   AvailableBiometrics -> BiometricTrendsNew -> MeasurementDetails.

_TRANSIENT_STATUS_CODES = {502}


class LoginFormParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.data: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "form":
            self.in_form = True
        name = attr.get("name")
        if self.in_form and tag == "input" and name is not None:
            self.data[name] = attr.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False


def _scrub_for_export(value: Json) -> Json:
    if isinstance(value, dict):
        return {
            key: _scrub_for_export(child)
            for key, child in value.items()
            if key.lower() not in {"password", "token"}
        }
    if isinstance(value, list):
        return [_scrub_for_export(child) for child in value]
    return value


def _write_json(path: Path, value: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_scrub_for_export(value), indent=2, ensure_ascii=False, sort_keys=True))


def _assert_unchanged(path: Path, value: Json) -> None:
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
        )
        self.config: dict[str, str] = {}
        self.user: JsonObject = {}

    def portal_url(self, path: str = "") -> str:
        return urljoin(f"{DEFAULT_BASE_URL}/{DEFAULT_PORTAL}/", path.lstrip("/"))

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
        return_url = self.portal_url("BodyMeasurement/")
        response = self.request(
            "GET",
            self.portal_url("User/Register"),
            params={"ReturnUrl": return_url},
            timeout=30,
        )
        response.raise_for_status()

        match = re.search(r'<form[^>]+action=["\']([^"\']+)', response.text, re.IGNORECASE)
        assert match, "Could not find login form action"
        login_url = urljoin(response.url, html.unescape(match.group(1)))

        parser = LoginFormParser()
        parser.feed(response.text)
        login_data = parser.data
        login_data.update(
            {
                "UserBinder.Username": self.email,
                "UserBinder.Password": self.password,
                "UserBinder.KeepMeLogged": "false",
            }
        )
        response = self.request("POST", login_url, data=login_data, timeout=30)
        response.raise_for_status()
        assert "UserBinder.Password" not in response.text, response.url

        index = self.request("GET", return_url, timeout=30)
        index.raise_for_status()
        self._load_page_config(index.text)

    def _load_page_config(self, page: str) -> None:
        self.config = dict(
            re.findall(r'(API_HOST|WORKOUT_HOST|API_APP_ID):\s*"([^"]+)"', page)
        )
        assert self.config.keys() >= {"WORKOUT_HOST", "API_APP_ID"}, self.config

        match = re.search(r"EU\.currentUser = JSON\.parse\('(.+?)'\);", page)
        assert match, "Could not find EU.currentUser in authenticated page"
        self.user = json.loads(match.group(1).encode().decode("unicode_escape"))
        assert isinstance(self.user.get("id"), str), self.user
        assert isinstance(self.user.get("token"), str), self.user

    def api_post(self, endpoint: str, payload: JsonObject | None = None) -> JsonObject:
        request_payload: JsonObject = {
            "_c": self.user.get("culture") or "en-US",
            "AppId": self.config["API_APP_ID"],
            "token": self.user["token"],
        }
        request_payload.update(payload or {})
        culture = str(request_payload["_c"])

        url = (
            f"https://{self.config['WORKOUT_HOST']}"
            f"/Biometrics/User/{self.user['id']}/{endpoint}"
        )
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

    def measurement_details(self, user_measurement_id: str) -> JsonObject:
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
    ) -> JsonObject:
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


def collect_measurement_ids(*objects: Json) -> list[str]:
    found: dict[str, None] = {}

    def visit(value: Json) -> None:
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


def collect_available_biometric_ids(available: JsonObject) -> list[str]:
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


def collect_trend_measurement_ids(trends: dict[str, JsonObject]) -> list[str]:
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
    _write_json(output_directory / "biometric-trends.json", trends)

    print(f"Found {len(measurement_ids)} measurement sessions", file=sys.stderr)
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
        print(
            f"[{index}/{len(measurement_ids)}] {details['measuredOn']} "
            f"{details.get('measureName', '')} ({status}, {len(measures)} measures)",
            file=sys.stderr,
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
    print(f"Archive written to {args.output.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
