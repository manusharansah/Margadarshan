"""
Plain-language order intake.

A dealer can type "500 units to Dhulikhel by tomorrow 5 pm". Gemma extracts the
fields; **code** does everything that must be exact:

* relative durations ("in 6 hours") arrive as ``deadline_hours`` and are range-checked;
* absolute times ("tomorrow 5 pm") arrive as a local ISO datetime and code computes the
  hours from the current Nepal time (UTC+05:45) - the model is never trusted with date arithmetic;
* the destination text is resolved against real place names in the loaded network;
* nothing is executed: the form is pre-filled and the dealer confirms.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, List, Optional

from .gemma_reasoner import GemmaClient

NEPAL_TZ = dt.timezone(dt.timedelta(hours=5, minutes=45), "NPT")
MAX_TEXT = 500

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "destination_text": {"type": ["string", "null"]},
        "quantity": {"type": ["integer", "null"]},
        "deadline_hours": {"type": ["number", "null"]},
        "deadline_local_iso": {"type": ["string", "null"]},
        "notes": {"type": "string"},
    },
    "required": ["destination_text", "quantity", "deadline_hours", "deadline_local_iso"],
}


def parse_order(text: str, client: GemmaClient, search_places: Callable[[str], List[dict]],
                now: Optional[dt.datetime] = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Describe the order first.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Keep the description under {MAX_TEXT} characters.")
    now = (now or dt.datetime.now(NEPAL_TZ)).astimezone(NEPAL_TZ)

    prompt = (
        "Extract a delivery order from the dealer's message. Fields: destination_text (the place name as written, "
        "no extra words), quantity (integer units), and the deadline. If the deadline is a duration ('in 6 hours', "
        "'within a day') put it in deadline_hours (a day = 24). If it is a clock time or date ('tomorrow 5pm', 'by "
        "Friday noon'), put a LOCAL datetime in deadline_local_iso as YYYY-MM-DDTHH:MM and leave deadline_hours null. "
        "Use null for anything not stated; never guess. notes: one short sentence on anything ambiguous.\n"
        f"Current Nepal time: {now.strftime('%Y-%m-%dT%H:%M')} ({now.strftime('%A')}).\n"
        f"Message: {text!r}\n"
        'Respond with exactly one JSON object with keys destination_text, quantity, deadline_hours, '
        "deadline_local_iso, notes."
    )
    data = client.generate_json(prompt, INTAKE_SCHEMA)

    missing: List[str] = []
    issues: List[str] = []

    qty = data.get("quantity")
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        qty = None
        missing.append("quantity")

    hours: Optional[float] = None
    iso, dh = data.get("deadline_local_iso"), data.get("deadline_hours")
    if isinstance(iso, str) and iso.strip():
        try:
            due = dt.datetime.fromisoformat(iso.strip()).replace(tzinfo=NEPAL_TZ)
            hours = (due - now).total_seconds() / 3600
            if hours <= 0:
                issues.append("That deadline is already in the past.")
                hours = None
        except ValueError:
            issues.append("Couldn't read the deadline time.")
    elif isinstance(dh, (int, float)) and not isinstance(dh, bool) and dh > 0:
        hours = float(dh)
    if hours is not None and hours > 720:
        issues.append("Deadlines beyond 30 days aren't supported.")
        hours = None
    if hours is None:
        missing.append("deadline")
    else:
        hours = round(hours, 2)

    dest_text = data.get("destination_text")
    candidates: List[dict] = []
    if isinstance(dest_text, str) and dest_text.strip():
        candidates = search_places(dest_text.strip())
    if not candidates:
        missing.append("destination")
    exact = [c for c in candidates if c["name"].lower() == (dest_text or "").strip().lower()]
    destination = exact[0] if exact else (candidates[0] if len(candidates) == 1 else None)

    return {
        "quantity": qty, "deadline_hours": hours, "destination": destination,
        "destination_candidates": candidates[:5], "destination_text": dest_text,
        "missing": missing, "issues": issues, "notes": str(data.get("notes") or ""),
        "understood_at": now.isoformat(timespec="minutes"),
    }
