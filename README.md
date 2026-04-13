# DS5220 Data Project 2 — Charlottesville Weather Tracker

A containerized, scheduled data pipeline running on Kubernetes (K3S) on AWS EC2. Every hour, a CronJob pod fetches live weather conditions for Charlottesville, VA, persists the record to DynamoDB, and publishes an evolving time-series plot and CSV to a public S3 website.

---

## Repository Structure

```
ds5220-data-project-2/
├── iss-reboost/          # Sample ISS tracker (provided)
├── weather/
│   ├── collect.py        # Main application logic
│   ├── Dockerfile        # Container image definition
│   ├── requirements.txt  # Python dependencies
│   └── weather-job.yaml  # Kubernetes CronJob manifest
├── iss-job.yaml
├── rivanna-job.yaml
└── simple-job.yaml
```

---

## Data Source

**API:** [Open-Meteo](https://open-meteo.com/en/docs) — free, no API key required, updates hourly.

**Endpoint:**
```
https://api.open-meteo.com/v1/forecast
  ?latitude=38.0293&longitude=-78.4767
  &current=temperature_2m,wind_speed_10m,precipitation,
           relative_humidity_2m,weather_code
  &temperature_unit=fahrenheit
  &wind_speed_unit=mph
  &timezone=America/New_York
```

**Location:** Charlottesville, VA (lat `38.0293`, lon `-78.4767`)

**Fields collected per run:**

| Field | Description | Units |
|---|---|---|
| `temperature_f` | Air temperature at 2 m above ground | °F |
| `wind_speed_mph` | Wind speed at 10 m above ground | mph |
| `precipitation` | Precipitation in the current hour | mm |
| `humidity` | Relative humidity at 2 m | % |
| `weather_code` | WMO weather interpretation code | integer |

---

## Scheduled Process

The pipeline is defined as a Kubernetes CronJob (`weather-job.yaml`) that fires **every hour on the hour** (`0 * * * *`). `concurrencyPolicy: Forbid` ensures only one pod runs at a time. On each execution, `collect.py` performs the following steps in order:

1. **Fetch** — calls the Open-Meteo API and extracts the five current-conditions fields above.
2. **Persist** — writes a timestamped record to a DynamoDB table (`weather-tracking`) keyed on `location_id` (partition key: `"charlottesville-va"`) and `timestamp` (sort key: ISO 8601 UTC string).
3. **Query history** — reads the full record set from DynamoDB, sorted chronologically, to build the cumulative dataset.
4. **Generate plot** — renders a 3-panel matplotlib time-series chart (temperature, wind speed, humidity) and saves it to `/tmp/plot.png`.
5. **Generate CSV** — writes all accumulated records to `/tmp/data.csv`.
6. **Publish** — uploads both files to the public S3 website bucket (`amzn-s3-ds5220-dp2`) with `public-read` ACL, overwriting the previous versions so the URLs always serve the latest data.

AWS credentials are never stored in the container or manifest. The EC2 instance running K3S has an IAM role attached at the instance level; `boto3` retrieves temporary credentials automatically via the EC2 Instance Metadata Service (IMDS).

---

## Output Data and Plot

### Live URLs

| Artifact | URL |
|---|---|
| Plot | http://amzn-s3-ds5220-dp2.s3-website-us-east-1.amazonaws.com/plot.png |
| Data CSV | http://amzn-s3-ds5220-dp2.s3-website-us-east-1.amazonaws.com/data.csv |

### CSV Schema

```
timestamp,temperature_f,wind_speed_mph,precipitation,humidity,weather_code
2026-04-08T16:00:20Z,48.7,5.8,0,28,0
...
```

Each row represents one hourly observation. Timestamps are UTC ISO 8601. The file grows by one row per CronJob run and is overwritten in S3 on each execution, so it always reflects the full accumulated history.

### Plot

The plot is a 3-panel time-series chart with a shared x-axis (UTC time) covering the full collection window:

- **Top panel (red):** Temperature (°F) — shows the diurnal cycle clearly, with daily swings of 40+ °F between overnight lows (~32–35°F) and afternoon highs (~79°F).
- **Middle panel (blue):** Wind speed (mph) — calm early in the window (2–5 mph), spiking to ~13.8 mph around April 11 before subsiding.
- **Bottom panel (green):** Relative humidity (%) — inversely tracks temperature, peaking above 65% on cool nights and dropping to the low 20s on warm afternoons.

The plot is regenerated and overwritten on every run, so it always shows the most current data window.

---

## Infrastructure

| Component | Detail |
|---|---|
| EC2 instance | `t3.large`, Ubuntu 24.04 LTS, Elastic IP |
| Kubernetes | K3S (lightweight single-node) |
| Container registry | GHCR — `ghcr.io/sportsanalystf/ds5220-weather:latest` |
| DynamoDB table | `weather-tracking` (partition key: `location_id`, sort key: `timestamp`) |
| S3 bucket | `amzn-s3-ds5220-dp2` (static website hosting enabled) |
| Schedule | Every hour — `0 * * * *` |

---

## Dependencies

```
requests==2.31.0
boto3==1.34.0
matplotlib==3.8.0
```

Base image: `python:3.11-slim`
