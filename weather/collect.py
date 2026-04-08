import os
import json
import boto3
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone
from decimal import Decimal

# --- Config ---
LOCATION_ID   = "charlottesville-va"
LAT           = 38.0293
LON           = -78.4767
TABLE_NAME    = os.environ.get("DYNAMODB_TABLE", "weather-tracking")
S3_BUCKET     = os.environ.get("S3_BUCKET", "amzn-s3-ds5220-dp2")
AWS_REGION    = os.environ.get("AWS_REGION", "us-east-1")

# --- AWS clients ---
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table    = dynamodb.Table(TABLE_NAME)
s3       = boto3.client("s3", region_name=AWS_REGION)

def fetch_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,wind_speed_10m,precipitation,relative_humidity_2m,weather_code"
        "&temperature_unit=fahrenheit"
        "&wind_speed_unit=mph"
        "&timezone=America/New_York"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()["current"]
    return {
        "temperature_f":  data["temperature_2m"],
        "wind_speed_mph": data["wind_speed_10m"],
        "precipitation":  data["precipitation"],
        "humidity":       data["relative_humidity_2m"],
        "weather_code":   data["weather_code"],
    }

def write_to_dynamo(weather):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {
        "location_id":    LOCATION_ID,
        "timestamp":      ts,
        "temperature_f":  Decimal(str(weather["temperature_f"])),
        "wind_speed_mph": Decimal(str(weather["wind_speed_mph"])),
        "precipitation":  Decimal(str(weather["precipitation"])),
        "humidity":       Decimal(str(weather["humidity"])),
        "weather_code":   weather["weather_code"],
    }
    table.put_item(Item=item)
    print(
        f"{LOCATION_ID} | "
        f"temp={weather['temperature_f']}°F | "
        f"wind={weather['wind_speed_mph']}mph | "
        f"precip={weather['precipitation']}mm | "
        f"humidity={weather['humidity']}%"
    )
    return ts

def read_history():
    resp = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("location_id").eq(LOCATION_ID)
    )
    items = sorted(resp["Items"], key=lambda x: x["timestamp"])
    return items

def generate_plot(items):
    timestamps = [datetime.strptime(i["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for i in items]
    temps      = [float(i["temperature_f"])  for i in items]
    winds      = [float(i["wind_speed_mph"]) for i in items]
    humidity   = [float(i["humidity"])       for i in items]

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle("Charlottesville, VA — Weather Tracker (72hr)", fontsize=14, fontweight="bold")

    axes[0].plot(timestamps, temps, color="#e05c5c", linewidth=2, marker="o", markersize=3)
    axes[0].set_ylabel("Temperature (°F)")
    axes[0].grid(True, alpha=0.3)
    axes[0].fill_between(timestamps, temps, alpha=0.15, color="#e05c5c")

    axes[1].plot(timestamps, winds, color="#5c8de0", linewidth=2, marker="o", markersize=3)
    axes[1].set_ylabel("Wind Speed (mph)")
    axes[1].grid(True, alpha=0.3)
    axes[1].fill_between(timestamps, winds, alpha=0.15, color="#5c8de0")

    axes[2].plot(timestamps, humidity, color="#5ce08a", linewidth=2, marker="o", markersize=3)
    axes[2].set_ylabel("Humidity (%)")
    axes[2].set_xlabel("Time (UTC)")
    axes[2].grid(True, alpha=0.3)
    axes[2].fill_between(timestamps, humidity, alpha=0.15, color="#5ce08a")

    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.autofmt_xdate(rotation=30)

    plt.tight_layout()
    plt.savefig("/tmp/plot.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Plot saved — {len(items)} data points")

def generate_csv(items):
    lines = ["timestamp,temperature_f,wind_speed_mph,precipitation,humidity,weather_code"]
    for i in items:
        lines.append(
            f"{i['timestamp']},"
            f"{i['temperature_f']},"
            f"{i['wind_speed_mph']},"
            f"{i['precipitation']},"
            f"{i['humidity']},"
            f"{i['weather_code']}"
        )
    with open("/tmp/data.csv", "w") as f:
        f.write("\n".join(lines))

def upload_to_s3():
    for fname, content_type in [("plot.png", "image/png"), ("data.csv", "text/csv")]:
        s3.upload_file(
            f"/tmp/{fname}", S3_BUCKET, fname,
            ExtraArgs={"ContentType": content_type, "ACL": "public-read"}
        )
        print(f"Uploaded {fname} → s3://{S3_BUCKET}/{fname}")

if __name__ == "__main__":
    weather = fetch_weather()
    write_to_dynamo(weather)
    items = read_history()
    generate_plot(items)
    generate_csv(items)
    upload_to_s3()
