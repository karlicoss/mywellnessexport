# Mywellness exporter

This repository follows the export/DAL split described in
[Building data liberation infrastructure](https://beepb00p.xyz/exports.html):

- the export layer logs into Mywellness and preserves raw API responses;
- the DAL works offline and interprets one exported snapshot.

The implementation lives under `src/mywellnessexport`:

```text
src/mywellnessexport/
  common.py  shared raw archive helpers
  export.py  authentication, API access, and raw export
  dal.py     offline access and typed Mywellness bindings
```

## Exporting

Create `secrets.json`:

```json
{
  "email": "name@example.com",
  "password": "your Mywellness password"
}
```

Run the exporter:

```console
uv run --extra export -m mywellnessexport.export \
  --secrets secrets.json \
  --output exports/2026-06-24
```

For incremental updates, reuse the same output directory. Existing session
files are checked for consistency and reused; missing session details are
downloaded.

Authentication uses the Mywellness application JSON API.
The retired legacy HTML portal is not required.

## Raw Archive

The exporter writes:

```text
mywellness-export/
  profile.json
  service-config.json
  measurements-descriptor-list.json
  available-biometrics.json
  sessions/
    <timestamp>_<measurement-id>.json
```

Each file under `sessions/` is a raw `MeasurementDetails` response.

The raw source of body scan history is currently:

```text
AvailableBiometrics -> BiometricTrendsNew -> MeasurementDetails
```

The API parameter spelling `Montly` is intentional; `Monthly` returned HTTP
500 while investigating.

## Offline DAL

Print measurement tables without `requests` or network access:

```console
uv run --extra table -m mywellnessexport.dal --source exports/2026-06-24
```

The CLI prints body composition first, then a separate Biostrength table if
performance measurements are present. Pass `--summary` to print counts/range
instead.

Programmatic access:

```python
from mywellnessexport.dal import DAL

dal = DAL("exports/2026-06-24")

for session in dal.sessions():
    print(session.measured_on, session.measure_name)
    for measure in session.measures():
        print(measure.name, measure.display_value)
```

`sessions()` exposes every exported record. Use `body_scan_sessions()` if you
want to skip `WebSiteUserProfile` sessions with no `facilityId`; those look
like signup/profile biometrics rather than device measurement sessions.
