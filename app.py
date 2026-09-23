"""Streamlit UI: password gate, then export devices with a Deliverect token."""

from __future__ import annotations

import os
import secrets
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from devices import (
    attach_locations,
    devices_to_csv,
    DEFAULT_SLEEP_HOURS,
    fetch_all_devices,
    filter_devices_by_tags,
    filter_locations_by_tags,
    get_all_locations,
    location_go_live,
    present_devices,
    unique_location_tags,
)
from tokening import track_page

# First Streamlit call, before st.secrets.
st.set_page_config(page_title="Devices export", layout="wide")

_STREAMLIT_SECRET_KEYS = ("APP_PASSWORD", "ACCOUNT_ID", "ZAPIER_WEBHOOK_URL")


def _hydrate_env_from_streamlit_secrets() -> None:
    """Load the Streamlit vault. That also copies string secrets into the environment."""
    try:
        streamlit_secrets = st.secrets
    except Exception:
        return
    for key in _STREAMLIT_SECRET_KEYS:
        if (os.getenv(key) or "").strip():
            continue
        try:
            value = streamlit_secrets[key]
        except Exception:
            continue
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()


_hydrate_env_from_streamlit_secrets()
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
ACCOUNT_ID = os.getenv("ACCOUNT_ID", "").strip()


def _require_password() -> None:
    if st.session_state.get("authenticated"):
        return

    st.title("Devices export")
    st.caption("Enter the password to continue.")
    if not APP_PASSWORD:
        st.error("APP_PASSWORD is not configured. Set it in the environment or .env file.")
        st.stop()

    with st.form("login", clear_on_submit=False):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            if secrets.compare_digest(password, APP_PASSWORD):
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("Incorrect password.")
    st.stop()


_require_password()

if not ACCOUNT_ID:
    st.error("ACCOUNT_ID is not set. Add it to your .env file.")
    st.stop()

st.title("Devices export")
st.caption(
    "Pages through picker-backend devices, fills in location name and tags, and downloads CSV."
)

with st.sidebar:
    st.subheader("Settings")
    api_token = st.text_input(
        "Deliverect Access Token",
        type="password",
        help="Bearer token for picker-backend.",
        key="api_token",
    )
    if st.button("Sign out", width="stretch"):
        st.session_state.pop("authenticated", None)
        st.rerun()

run_btn = st.button("Export devices", type="primary")
progress_placeholder = st.empty()
status_placeholder = st.empty()

if run_btn:
    token = str(api_token or "").strip()
    if not token:
        st.error("Paste an API token.")
        st.stop()

    track_page("Boots devices export", token)

    def on_progress(done: int, total: int) -> None:
        pct = min(done / total, 1.0) if total else 0.0
        try:
            progress_placeholder.progress(pct, text=f"{done} / {total} devices")
        except TypeError:
            progress_placeholder.progress(pct)

    status_placeholder.markdown("*Fetching locations…*")
    try:
        locations = get_all_locations(ACCOUNT_ID, token)
        status_placeholder.markdown("*Fetching devices…*")
        devices = fetch_all_devices(ACCOUNT_ID, token, progress_callback=on_progress)
        attach_locations(devices, locations)
    except Exception as exc:
        st.error(f"Export failed: {exc}")
        st.stop()

    if not devices and not locations:
        st.warning("No devices or locations returned for this account.")
        st.stop()

    try:
        progress_placeholder.progress(1.0, text="Complete")
    except TypeError:
        progress_placeholder.progress(1.0)
    status_placeholder.markdown(f"**{len(devices)} devices**")

    st.session_state["devices_rows"] = devices
    st.session_state["locations"] = locations

if "devices_rows" not in st.session_state:
    st.info("Paste a token in the sidebar and click **Export devices**.")
    st.stop()

rows = st.session_state["devices_rows"]
locations = st.session_state.get("locations") or []
tag_names = set(unique_location_tags(rows))
for loc in locations:
    tag_names.update(loc.get("tags") or [])
selected_tags = st.multiselect(
    "Filter by location tags (devices at a location with any of these tags). Leave empty for all.",
    options=sorted(tag_names),
    default=[],
    key="device_tag_filter",
)
filtered = filter_devices_by_tags(rows, selected_tags)
locations_in_scope = filter_locations_by_tags(locations, selected_tags)
st.caption(f"Showing {len(filtered)} of {len(rows)} devices.")
if filtered:
    presented = present_devices(filtered)
    st.dataframe(presented, width="stretch", hide_index=True)
    st.download_button(
        label=f"Download CSV ({len(filtered)} devices)",
        data=devices_to_csv(filtered),
        file_name=f"devices_{date.today().isoformat()}.csv",
        mime="text/csv",
        type="primary",
    )
else:
    st.info("No devices match the selected tags.")

st.subheader("Go-live: one device online per store")
sleep_hours = st.number_input(
    "Count as sleeping if last seen within (hours)",
    min_value=1,
    max_value=24,
    value=DEFAULT_SLEEP_HOURS,
    step=1,
    help="Offline devices seen inside this window are sleeping, not down.",
)
go_live = location_go_live(locations_in_scope, filtered, sleep_hours=sleep_hours)
if not go_live:
    st.info("No stores in this view.")
    st.stop()

not_ready = sum(1 for row in go_live if row["Status"] in ("Not online", "No device"))
sleeping = sum(1 for row in go_live if row["Status"] == "Sleeping")
if not_ready:
    detail = f"{not_ready} of {len(go_live)} stores are not online."
    if sleeping:
        detail += f" {sleeping} are sleeping."
    st.error(detail)
elif sleeping:
    st.success(f"All {len(go_live)} stores are online or sleeping (seen in the last {int(sleep_hours)}h).")
else:
    st.success(f"All {len(go_live)} stores have at least one device online.")

frame = pd.DataFrame(go_live)

def _paint_go_live(row: pd.Series) -> list[str]:
    if row["Status"] == "Online":
        color = "#dcfce7"
    elif row["Status"] == "Sleeping":
        color = "#fef3c7"
    else:
        color = "#fecaca"
    return [f"background-color: {color}"] * len(row)

st.dataframe(
    frame.style.apply(_paint_go_live, axis=1),
    width="stretch",
    hide_index=True,
)
