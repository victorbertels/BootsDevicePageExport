"""JWT helpers matching RetailTools authentication/tokening.py."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL", "").strip()


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload without verifying the signature."""
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        raw = _b64url_decode(parts[1])
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def email_from_jwt_sub(token: str) -> str:
    """Return the user email from JWT `email` or Auth0-style `sub`."""
    claims = decode_jwt_payload(token)
    email = str(claims.get("email") or "").strip()
    if email:
        return email
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        return ""
    candidate = sub.rsplit("|", 1)[-1].strip()
    return candidate if "@" in candidate else ""


def track_page(page_name: str, token: str = "") -> None:
    """POST page, timestamp, and JWT email to Zapier when a webhook is configured."""
    webhook_url = os.getenv("ZAPIER_WEBHOOK_URL", "").strip() or ZAPIER_WEBHOOK_URL
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={
                "page": page_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "email": email_from_jwt_sub(token),
            },
            timeout=5,
        )
    except Exception:
        pass
