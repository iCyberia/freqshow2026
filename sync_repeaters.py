#!/usr/bin/env python3
import json
from pathlib import Path

import pandas as pd

URL_2M_FL = "https://www.repeaterbook.com/repeaters/Display_SS.php?state_id=12&band=4"
OUT = Path("/home/hiroshi/FreqShow_v3/freqshow_repeaters.json")


def normalize_columns(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df


def pick_table(dfs):
    for df in dfs:
        df = normalize_columns(df)
        cols = set(df.columns)
        if {"Frequency", "Access", "Location"}.issubset(cols):
            return df
    raise RuntimeError("Could not find repeater table with Frequency/Access/Location columns.")


def parse_offset_and_access(access_raw: str):
    access_raw = str(access_raw).strip()
    if not access_raw or access_raw == "nan":
        return "", ""

    if access_raw.startswith("+"):
        return "+", access_raw[1:].strip()
    if access_raw.startswith("-"):
        return "-", access_raw[1:].strip()

    return "", access_raw


def parse_status(row):
    # RepeaterBook's status column can be sparse/icon-like in HTML.
    # For now, anything explicitly marked Unknown becomes Unknown, else Operational.
    status_text = ""
    for key in row.index:
        if "Status" in str(key):
            status_text = str(row.get(key, "")).strip()
            break

    if "Unknown" in status_text:
        return "Unknown"
    return "Operational"


def load_existing():
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "bands": [
            {"key": "2m", "label": "2m", "repeaters": []},
            {"key": "70cm", "label": "70cm", "repeaters": []},
        ]
    }


def upsert_band(data, key, label, repeaters):
    for band in data.get("bands", []):
        if band.get("key") == key:
            band["label"] = label
            band["repeaters"] = repeaters
            return
    data.setdefault("bands", []).append({
        "key": key,
        "label": label,
        "repeaters": repeaters,
    })


def sync_2m():
    dfs = pd.read_html(URL_2M_FL)
    df = pick_table(dfs)

    repeaters = []
    for _, row in df.iterrows():
        try:
            freq = float(str(row["Frequency"]).strip())
        except Exception:
            continue

        access_raw = str(row.get("Access", "")).strip()
        location = str(row.get("Location", "")).strip()
        offset, access = parse_offset_and_access(access_raw)
        status = parse_status(row)

        repeaters.append({
            "freq_mhz": round(freq, 4),
            "offset": offset,
            "access": access,
            "location": location,
            "status": status
        })

    repeaters.sort(key=lambda r: (r["freq_mhz"], r["location"]))
    return repeaters


def main():
    data = load_existing()
    repeaters_2m = sync_2m()
    upsert_band(data, "2m", "2m", repeaters_2m)

    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {len(repeaters_2m)} 2m repeaters to {OUT}")


if __name__ == "__main__":
    main()
