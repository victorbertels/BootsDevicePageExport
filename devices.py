"""Fetch every device for a picker account and write them as CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import requests

# Offline but seen inside this window counts as sleeping, not down.
DEFAULT_SLEEP_HOURS = 4

COLUMNS = [
    "status",
    "userName",
    "locationName",
    "locationTags",
    "name",
    "model",
    "brand",
    "os",
    "osVersion",
    "version",
    "buildNumber",
    "apiLevel",
    "deviceId",
    "stale",
    "locationId",
    "deviceKey",
    "uniqueId",
    "userId",
    "accountId",
    "createdAt",
    "updatedAt",
    "_id",
]


def _headers(api_token: str) -> dict:
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }


def _tag_names(tags) -> list[str]:
    names = []
    for tag in tags or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or tag.get("tag") or "").strip()
        else:
            name = str(tag).strip() if tag else ""
        if name and name not in names:
            names.append(name)
    return names


def get_all_locations(account_id: str, api_token: str) -> list[dict]:
    """Every location for an account: id, name, tags. Same pagination as RetailTools."""
    locations = []
    page = 1
    max_results = 500
    while page < 500:
        url = (
            f'https://api.deliverect.io/locations?where={{"account":"{account_id}"}}'
            f"&page={page}&max_results={max_results}"
        )
        response = requests.get(url, headers=_headers(api_token), timeout=60)
        if response.status_code == 401:
            raise RuntimeError("Token rejected (401). Paste a fresh Deliverect access token.")
        if response.status_code != 200:
            raise RuntimeError(
                f"Locations API error ({response.status_code}): {(response.text or '')[:300]}"
            )
        items = response.json().get("_items") or []
        if not items:
            break
        for location in items:
            location_id = location.get("_id")
            if not location_id:
                continue
            locations.append({
                "id": location_id,
                "name": location.get("name") or "",
                "tags": _tag_names(location.get("tags")),
            })
        if len(items) < max_results:
            break
        page += 1
    return locations


def attach_locations(devices: list[dict], locations: list[dict]) -> list[dict]:
    """Fill locationName and locationTags from locationId."""
    by_id = {loc["id"]: loc for loc in locations}
    for device in devices:
        loc = by_id.get(device.get("locationId") or "")
        device["locationName"] = loc["name"] if loc else ""
        device["locationTags"] = ", ".join(loc["tags"]) if loc else ""
    return devices


def device_tags(device: dict) -> set[str]:
    return {tag.strip() for tag in str(device.get("locationTags") or "").split(",") if tag.strip()}


def unique_location_tags(devices: list[dict]) -> list[str]:
    tags: set[str] = set()
    for device in devices:
        tags.update(device_tags(device))
    return sorted(tags)


def filter_devices_by_tags(devices: list[dict], selected_tags: list[str]) -> list[dict]:
    """Keep devices whose location has any of the selected tags. Empty selection = all."""
    if not selected_tags:
        return devices
    selected = set(selected_tags)
    return [device for device in devices if selected & device_tags(device)]


def filter_locations_by_tags(locations: list[dict], selected_tags: list[str]) -> list[dict]:
    if not selected_tags:
        return locations
    selected = set(selected_tags)
    return [loc for loc in locations if selected & set(loc.get("tags") or [])]


def _format_last_online(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    # 2026-09-23T12:44:45.290Z → 2026-09-23 12:44
    if len(text) >= 16 and text[10] == "T":
        return text[:10] + " " + text[11:16]
    return text


def _parse_updated_at(value: str) -> datetime | None:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def location_go_live(
    locations: list[dict],
    devices: list[dict],
    *,
    sleep_hours: float = DEFAULT_SLEEP_HOURS,
    now: datetime | None = None,
) -> list[dict]:
    """One row per location.

    Online: a device is ONLINE now.
    Sleeping: nothing is ONLINE, but a device was seen inside ``sleep_hours``.
    Not online / No device come first.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=sleep_hours)
    in_scope = {loc["id"] for loc in locations}
    by_location: dict[str, list[dict]] = {}
    for device in devices:
        location_id = device.get("locationId") or ""
        if location_id in in_scope:
            by_location.setdefault(location_id, []).append(device)

    rank = {"Not online": 0, "No device": 1, "Sleeping": 2, "Online": 3}
    rows = []
    for loc in locations:
        loc_devices = by_location.get(loc["id"], [])
        online = sum(1 for device in loc_devices if str(device.get("status") or "").upper() == "ONLINE")
        last_values = [str(device.get("updatedAt") or "") for device in loc_devices]
        last = max(last_values, default="")
        last_at = max(
            (parsed for parsed in (_parse_updated_at(value) for value in last_values) if parsed),
            default=None,
        )
        if online:
            status = "Online"
        elif last_at is not None and last_at >= cutoff:
            status = "Sleeping"
        elif loc_devices:
            status = "Not online"
        else:
            status = "No device"
        rows.append({
            "Store": loc.get("name") or loc["id"],
            "Tags": ", ".join(loc.get("tags") or []),
            "Status": status,
            "Devices online": online,
            "Devices": len(loc_devices),
            "Last online": _format_last_online(last),
        })
    rows.sort(key=lambda row: (rank.get(row["Status"], 9), row["Store"].lower()))
    return rows


def fetch_all_devices(
    account_id: str,
    api_token: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """POST each page until `total` is reached. Empty locationIds = all locations."""
    devices: list[dict] = []
    seen: set[str] = set()
    page = 1
    total = 0

    while page < 500:
        url = (
            f"https://picker-backend.deliverect.com/portal/devices/accounts/{account_id}"
            f"?sortBy=updatedAt&sortDirection=desc&page={page}"
        )
        response = requests.post(
            url,
            json={"locationIds": []},
            headers=_headers(api_token),
            timeout=60,
        )
        if response.status_code == 401:
            raise RuntimeError("Token rejected (401). Paste a fresh Deliverect access token.")
        response.raise_for_status()
        body = response.json()
        batch = body.get("data") or []
        total = int(body.get("total") or total)

        added = 0
        for device in batch:
            device_id = str(device.get("_id") or "")
            if device_id and device_id in seen:
                continue
            if device_id:
                seen.add(device_id)
            devices.append(device)
            added += 1

        if progress_callback:
            progress_callback(len(devices), total or len(devices))

        if not batch or added == 0 or (total and len(devices) >= total):
            break
        page += 1

    return devices


def present_devices(devices: list[dict]) -> list[dict]:
    extra: list[str] = []
    seen = set(COLUMNS)
    for device in devices:
        for key in device:
            if key not in seen and key != "__v":
                seen.add(key)
                extra.append(key)
    columns = COLUMNS + extra
    return [{key: device.get(key, "") for key in columns} for device in devices]


def devices_to_csv(devices: list[dict]) -> str:
    rows = present_devices(devices)
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
