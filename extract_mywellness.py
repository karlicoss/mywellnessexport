#!/usr/bin/env python3
import argparse
import html
import html.parser
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests


DEFAULT_BASE_URL = "https://v1.mywellness.com"
DEFAULT_PORTAL = "cloud"
DEFAULT_PASSWORD_FILE = "password"
DEFAULT_OUTPUT_DIR = "mywellness_export"
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


class MywellnessClient:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        portal: str = DEFAULT_PORTAL,
    ) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.portal = portal.strip("/")
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
        # Page-level service config parsed from EU.config, such as API hosts and app id.
        self.config: dict[str, str] = {}
        # Authenticated user context parsed from EU.currentUser; the token refreshes on API calls.
        self.user: dict[str, object] = {}

    def portal_url(self, path: str = "") -> str:
        return urljoin(f"{self.base_url}/{self.portal}/", path.lstrip("/"))

    def login(self) -> None:
        return_url = self.portal_url("BodyMeasurement/")
        response = self.session.get(
            self.portal_url("User/Register"),
            params={"ReturnUrl": return_url},
            timeout=30,
        )
        response.raise_for_status()

        match = re.search(r'<form[^>]+action=["\']([^"\']+)', response.text, re.IGNORECASE)
        assert match, "Could not find login form action"
        login_url = urljoin(response.url, html.unescape(match.group(1)))
        # The legacy MVC endpoint expects the hidden UserBinder fields it emitted.
        login_data = self.login_form_data(response.text)
        login_data.update(
            {
                "UserBinder.Username": self.username,
                "UserBinder.Password": self.password,
                "UserBinder.KeepMeLogged": "false",
            }
        )
        response = self.session.post(
            login_url,
            data=login_data,
            timeout=30,
        )
        response.raise_for_status()
        assert "UserBinder.Password" not in response.text, response.url

        index = self.session.get(return_url, timeout=30)
        index.raise_for_status()
        self._load_page_config(index.text)

    @staticmethod
    def login_form_data(page: str) -> dict[str, str]:
        class Parser(html.parser.HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.in_form = False
                self.data: dict[str, str] = {}

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                attr = dict(attrs)
                if tag == "form":
                    self.in_form = True
                if self.in_form and tag == "input" and attr.get("name"):
                    self.data[attr["name"]] = attr.get("value") or ""

            def handle_endtag(self, tag: str) -> None:
                if tag == "form":
                    self.in_form = False

        parser = Parser()
        parser.feed(page)
        return parser.data

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
        request_payload: dict[str, object] = {
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
        # These mirror the site's $.ajaxSetup headers for end-user web calls.
        headers = {
            "X-MWAPPS-CLIENT": "enduserweb",
            "X-MWAPPS-APPID": self.config["API_APP_ID"],
        }
        for attempt in range(3):
            # The site's service helper sends culture both in the query string and JSON body.
            response = self.session.post(
                url,
                params={"_c": culture},
                json=request_payload,
                headers=headers,
                timeout=30,
            )
            if response.status_code != 502 or attempt == 2:
                break
            # BiometricTrendsNew intermittently returned 502 during the full sweep.
            time.sleep(1)
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict), data
        assert isinstance(data.get("token"), str), "Missing refreshed token in API response"
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
        *,
        biometric_descriptor_id: str,
        start_day: str,
        trends: str = DEFAULT_TREND_PERIOD,
        number_of_trends: int = DEFAULT_TREND_COUNT,
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
    """Return every userMeasurementId found while recursively walking API objects."""
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
    """Return biometric descriptor ids used to ask BiometricTrendsNew for history."""
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
    """Return measurement ids carried by BiometricTrendsNew trend points."""
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


def scrub_for_export(value: object) -> object:
    """Remove session credentials from API wrapper objects before writing JSON."""
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


def export_body_measurements(client: MywellnessClient, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    descriptors = client.api_post("MeasurementsDescriptorList")
    latest = client.api_post("LastBiometricsMeasurements")
    available = client.api_post("AvailableBiometrics")
    trends = {
        biometric_descriptor_id: client.biometric_trends(
            biometric_descriptor_id=biometric_descriptor_id,
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

    measure_count = 0
    for measurement in details:
        measures = measurement.get("measures")
        assert isinstance(measures, list), measurement
        measure_count += len(measures)

    summary = {
        "detailed_measurement_session_count": len(details),
        "measurement_session_count": len(details),
        "measure_count": measure_count,
        "files": [
            "measurement_details.json",
            "last_biometrics_measurements.json",
            "measurements_descriptor_list.json",
            "available_biometrics.json",
            "biometric_trends.json",
        ],
    }
    write_json(output_dir / "summary.json", summary)

    print(f"Exported {summary['measurement_session_count']} measurement sessions")
    print(f"Exported {summary['measure_count']} measures")
    print(f"Output directory: {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Mywellness body measurements.")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password-file", type=Path, default=Path(DEFAULT_PASSWORD_FILE))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--portal",
        default=DEFAULT_PORTAL,
        help="v1 URL segment for the portal; the public end-user portal is 'cloud'.",
    )
    parser.add_argument("--club", dest="portal", help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = MywellnessClient(
        username=args.username,
        password=read_password(args.password_file),
        base_url=args.base_url,
        portal=args.portal,
    )
    client.login()
    export_body_measurements(client, args.output_dir)


if __name__ == "__main__":
    main()
