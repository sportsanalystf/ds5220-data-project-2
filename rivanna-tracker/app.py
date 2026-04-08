#!/usr/bin/env python3
"""
Rivanna River Water Tracker
Monitors the South Fork Rivanna River near Charlottesville, VA using the
USGS Instantaneous Values API. Tracks discharge (ft³/s) and gage height (ft)
every 30 minutes, persists data in DynamoDB, and publishes an evolving plot
and CSV data file to an S3 static website bucket.

USGS Site: 02032515 — S F Rivanna River Near Charlottesville, VA
"""

import os
import json
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

import requests
import boto3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns

# ── Configuration ────────────────────────────────────────────────────────────
USGS_SITE_ID = "02032515"
USGS_API_URL = (
    "https://waterservices.usgs.gov/nwis/iv/"
    f"?format=json&sites={USGS_SITE_ID}"
    "&parameterCd=00060,00065"  # discharge + gage height
    "&period=PT1H"              # most recent 1 hour of readings
)

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "rivanna-tracking")
S3_BUCKET = os.environ.get("S3_BUCKET", "your-bucket-name")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

SITE_LABEL = "rivanna"

# Thresholds for trend detection
SURGE_THRESHOLD_CFS = 50    # discharge jump ≥ 50 cfs → SURGE (rain event)
STABLE_THRESHOLD_CFS = 5    # delta within ±5 cfs → STABLE

# ── AWS Clients ──────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE)
s3 = boto3.client("s3", region_name=AWS_REGION)


def fetch_usgs_data():
    """Call the USGS Instantaneous Values API and return the latest reading."""
    resp = requests.get(USGS_API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    ts_list = data["value"]["timeSeries"]

    discharge = None
    gage_height = None
    timestamp = None

    for ts in ts_list:
        var_code = ts["variable"]["variableCode"][0]["value"]
        values = ts["values"][0]["value"]
        if not values:
            continue
        latest = values[-1]  # most recent reading

        if var_code == "00060":  # discharge
            discharge = float(latest["value"])
            timestamp = latest["dateTime"]
        elif var_code == "00065":  # gage height
            gage_height = float(latest["value"])
            if timestamp is None:
                timestamp = latest["dateTime"]

    if discharge is None or gage_height is None:
        raise ValueError("Missing discharge or gage height from USGS API")

    # Parse and normalize timestamp to UTC ISO 8601
    dt = datetime.fromisoformat(timestamp)
    dt_utc = dt.astimezone(timezone.utc)
    ts_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "timestamp": ts_iso,
        "discharge_cfs": discharge,
        "gage_height_ft": gage_height,
    }


def get_last_entry():
    """Query DynamoDB for the most recent entry."""
    resp = table.query(
        KeyConditionExpression="site_id = :sid",
        ExpressionAttributeValues={":sid": SITE_LABEL},
        ScanIndexForward=False,  # descending by sort key
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def classify_trend(delta_cfs):
    """Classify the discharge trend based on change from last reading."""
    if delta_cfs >= SURGE_THRESHOLD_CFS:
        return "SURGE"
    elif delta_cfs > STABLE_THRESHOLD_CFS:
        return "RISING"
    elif delta_cfs < -STABLE_THRESHOLD_CFS:
        return "FALLING"
    else:
        return "STABLE"


def write_entry(reading, delta_cfs, trend):
    """Write a new record to DynamoDB."""
    item = {
        "site_id": SITE_LABEL,
        "timestamp": reading["timestamp"],
        "discharge_cfs": Decimal(str(reading["discharge_cfs"])),
        "gage_height_ft": Decimal(str(reading["gage_height_ft"])),
        "delta_cfs": Decimal(str(round(delta_cfs, 2))),
        "trend": trend,
        "site_name": "S F Rivanna River Near Charlottesville, VA",
        "usgs_site_id": USGS_SITE_ID,
    }
    table.put_item(Item=item)


def get_all_entries():
    """Read the full history from DynamoDB."""
    items = []
    resp = table.query(
        KeyConditionExpression="site_id = :sid",
        ExpressionAttributeValues={":sid": SITE_LABEL},
        ScanIndexForward=True,
    )
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.query(
            KeyConditionExpression="site_id = :sid",
            ExpressionAttributeValues={":sid": SITE_LABEL},
            ScanIndexForward=True,
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp["Items"])
    return items


def generate_plot(entries):
    """Create a dual-axis time-series plot of discharge and gage height."""
    timestamps = [datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) for e in entries]
    discharge = [float(e["discharge_cfs"]) for e in entries]
    gage = [float(e["gage_height_ft"]) for e in entries]

    sns.set_theme(style="darkgrid")
    fig, ax1 = plt.subplots(figsize=(14, 6))

    color_discharge = "#2196F3"
    color_gage = "#FF9800"

    ax1.set_xlabel("Time (UTC)")
    ax1.set_ylabel("Discharge (ft³/s)", color=color_discharge)
    ax1.plot(timestamps, discharge, color=color_discharge, linewidth=1.5, label="Discharge")
    ax1.fill_between(timestamps, discharge, alpha=0.15, color=color_discharge)
    ax1.tick_params(axis="y", labelcolor=color_discharge)

    # Mark surge events
    for i, e in enumerate(entries):
        if e.get("trend") == "SURGE":
            ax1.axvline(x=timestamps[i], color="red", alpha=0.4, linestyle="--", linewidth=1)
            ax1.annotate("SURGE", xy=(timestamps[i], discharge[i]),
                         fontsize=7, color="red", ha="center",
                         xytext=(0, 10), textcoords="offset points")

    ax2 = ax1.twinx()
    ax2.set_ylabel("Gage Height (ft)", color=color_gage)
    ax2.plot(timestamps, gage, color=color_gage, linewidth=1.5, linestyle="--", label="Gage Height")
    ax2.tick_params(axis="y", labelcolor=color_gage)

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)

    plt.title(
        f"S F Rivanna River — Charlottesville, VA (USGS {USGS_SITE_ID})\n"
        f"{len(entries)} readings | {timestamps[0].strftime('%b %d')} – {timestamps[-1].strftime('%b %d, %Y')}",
        fontsize=13, fontweight="bold",
    )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf


def generate_csv(entries):
    """Create a CSV data file from all entries."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "discharge_cfs", "gage_height_ft",
        "delta_cfs", "trend", "site_name", "usgs_site_id",
    ])
    for e in entries:
        writer.writerow([
            e["timestamp"],
            float(e["discharge_cfs"]),
            float(e["gage_height_ft"]),
            float(e.get("delta_cfs", 0)),
            e.get("trend", ""),
            e.get("site_name", ""),
            e.get("usgs_site_id", ""),
        ])
    return output.getvalue().encode("utf-8")


def upload_to_s3(data_bytes, key, content_type):
    """Upload bytes to S3 bucket."""
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=data_bytes,
        ContentType=content_type,
    )
    print(f"  Uploaded s3://{S3_BUCKET}/{key}")


def main():
    print("=" * 70)
    print("RIVANNA RIVER WATER TRACKER")
    print("=" * 70)

    # 1. Fetch latest USGS data
    reading = fetch_usgs_data()
    print(f"  USGS reading: discharge={reading['discharge_cfs']} cfs, "
          f"gage={reading['gage_height_ft']} ft @ {reading['timestamp']}")

    # 2. Compare with previous entry
    last = get_last_entry()
    if last:
        delta = reading["discharge_cfs"] - float(last["discharge_cfs"])
    else:
        delta = 0.0
        print("  No previous entry found — first run.")

    trend = classify_trend(delta)

    # 3. Write to DynamoDB
    write_entry(reading, delta, trend)

    status = (
        f"RIVANNA | discharge={reading['discharge_cfs']} cfs | "
        f"gage={reading['gage_height_ft']} ft | "
        f"delta={delta:+.1f} cfs | {trend}"
    )
    if trend == "SURGE":
        status += "  *** SURGE DETECTED — possible rain event ***"
    print(f"  {status}")

    # 4. Read full history and generate outputs
    entries = get_all_entries()
    print(f"  Total entries in DynamoDB: {len(entries)}")

    if len(entries) >= 2:
        plot_buf = generate_plot(entries)
        upload_to_s3(plot_buf.read(), "plot.png", "image/png")

        csv_bytes = generate_csv(entries)
        upload_to_s3(csv_bytes, "data.csv", "text/csv")
    else:
        print("  Skipping plot/CSV — need at least 2 entries.")

    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
