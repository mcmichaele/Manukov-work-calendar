#!/usr/bin/env python3
"""
Reads a schedule screenshot, extracts Manukov's shifts using Claude,
and appends new events to manukov.ics
"""

import sys
import base64
import json
import uuid
import re
import anthropic
from datetime import datetime, timedelta
from pathlib import Path

ICS_FILE = "manukov.ics"

PROMPT = """You are reading a work schedule spreadsheet image for May (or another month).

The schedule is a calendar grid. Each day cell has up to 3 names stacked vertically:
- Row 1 = Morning Shift (8AM-8PM)
- Row 2 = Swing Shift (Mon-Thu: 2PM-2AM, Fri-Sun: 12PM-12AM)
- Row 3 = Night Shift (8PM-8AM next day)

Find every cell where "Manukov" appears and return ONLY a JSON array like this:
[
  {"date": "2026-05-05", "weekday": "Tuesday", "shift": "Morning", "start": "08:00", "end": "20:00"},
  {"date": "2026-05-06", "weekday": "Wednesday", "shift": "Morning", "start": "08:00", "end": "20:00"}
]

Rules:
- date format: YYYY-MM-DD
- shift is exactly one of: Morning, Swing, Night
- start/end are 24h format HH:MM
- Morning: 08:00-20:00
- Swing Mon-Thu: 14:00-02:00 (next day)
- Swing Fri-Sun: 12:00-00:00 (next day)
- Night: 20:00-08:00 (next day)
- Return ONLY the JSON array, no other text
"""


def image_to_base64(path: str) -> tuple[str, str]:
    suffix = Path(path).suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    media_type = media_map.get(suffix, "image/png")
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode(), media_type


def extract_shifts(image_path: str) -> list[dict]:
    client = anthropic.Anthropic()
    b64, media_type = image_to_base64(image_path)

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def make_ics_dt(date_str: str, time_str: str) -> str:
    """Convert date + HH:MM to ICS datetime, handling next-day overflow."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    h, m = map(int, time_str.split(":"))
    if h == 0 or h == 2:  # midnight/2am means next calendar day
        d += timedelta(days=1)
        if h == 0:
            h = 0
    return d.strftime("%Y%m%d") + f"T{h:02d}{m:02d}00"


def shift_crosses_midnight(start: str, end: str) -> bool:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return (eh * 60 + em) <= (sh * 60 + sm) or eh in (0, 2)


def build_vevent(shift: dict) -> list[str]:
    dtstart = make_ics_dt(shift["date"], shift["start"])

    end_time = shift["end"]
    if shift_crosses_midnight(shift["start"], end_time):
        # end is on next day
        d = datetime.strptime(shift["date"], "%Y-%m-%d") + timedelta(days=1)
        h, m = map(int, end_time.split(":"))
        dtend = d.strftime("%Y%m%d") + f"T{h:02d}{m:02d}00"
    else:
        dtend = make_ics_dt(shift["date"], end_time)

    return [
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{shift['shift']} Shift",
        "END:VEVENT",
    ]


def get_existing_dates(ics_content: str) -> set[str]:
    """Extract all DTSTART dates already in the file to avoid duplicates."""
    return set(re.findall(r"DTSTART:(\d{8})", ics_content))


def append_to_ics(shifts: list[dict], ics_path: str):
    with open(ics_path, "r") as f:
        content = f.read()

    existing_dates = get_existing_dates(content)

    new_vevents = []
    for shift in shifts:
        dtstart_date = datetime.strptime(shift["date"], "%Y-%m-%d").strftime("%Y%m%d")
        # Skip if we already have events on this date for this shift
        # (simple dedup: skip if same date already in file)
        if dtstart_date not in existing_dates:
            new_vevents.extend(build_vevent(shift))
            existing_dates.add(dtstart_date)
        else:
            # Check more carefully: same date could have different shifts
            # Use UID-based dedup — just always add, UIDs are unique
            new_vevents.extend(build_vevent(shift))

    if not new_vevents:
        print("No new shifts to add.")
        return

    # Insert new VEVENTs before END:VCALENDAR
    updated = content.replace(
        "END:VCALENDAR",
        "\r\n".join(new_vevents) + "\r\nEND:VCALENDAR"
    )

    with open(ics_path, "w") as f:
        f.write(updated)

    print(f"Added {len(shifts)} shift(s) to {ics_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python process_schedule.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    print(f"Processing: {image_path}")

    shifts = extract_shifts(image_path)
    print(f"Found {len(shifts)} Manukov shift(s):")
    for s in shifts:
        print(f"  {s['date']} ({s['weekday']}) - {s['shift']} {s['start']}-{s['end']}")

    append_to_ics(shifts, ICS_FILE)


if __name__ == "__main__":
    main()
