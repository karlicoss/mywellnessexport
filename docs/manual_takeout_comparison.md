# Manual Takeout Comparison

This note compares the archive produced by this exporter with a manual
Mywellness data takeout. It intentionally avoids recording account identifiers,
session identifiers, takeout filenames, or exact measurement dates.

## Shape

The manual takeout biometrics file is a flat list of rows with only:

```text
name
measuredOn
value
```

The exporter archive is session-oriented. Each file under `sessions/` is the
raw `MeasurementDetails` response for one measurement session, so it preserves
more context:

```text
session timestamp
measurement id
facility id
measuredWhere
measureName
measure categories
measure descriptors
display/stored values
units and ideal ranges
```

The manual takeout also contains indoor and outdoor activity files. Those are
outside the current exporter scope.

## Coverage

The exporter archive can contain newer biometrics than a manual takeout,
because it is fetched live. For overlapping biometrics, profile measurements
and Biostrength measurements matched cleanly by metric name and value.

Body composition measurements mostly line up too, but the two surfaces use
different metric labels and different value formatting. Examples:

```text
Manual takeout             Exporter archive
Basal Metabolic Rate       Basal metabolic rate (BMR)
Fat Mass                   Body fat mass (BFM)
Total Body Water Perc      % Total body water
```

The manual takeout keeps a flatter, user-facing metric list. The exporter keeps
the richer API objects and lets the DAL normalize display later.

## Known Gap

The only meaningful biometric found in the manual takeout but not in the
exporter session details was:

```text
Fitness Age
```

It did not appear in the `MeasurementDetails` sessions or in the checked
biometric discovery artifacts. If this metric matters, it likely needs a
different endpoint or a dedicated fallback path.

## Decision

Use `sessions/*.json` as the authoritative biometrics archive. It is richer
than the takeout biometrics list, keeps real session grouping, and supports
incremental refreshes.

The manual takeout remains useful as a sanity check and as a reminder that
activities are separate data we do not export yet.
