from datetime import datetime, timezone
from typing import List, Dict

from utils import date_only


def match_loads(intake: dict, loads: List[Dict], limit: int = 3) -> dict:
    """
    Exact-match filtering over provided loads.

    Rules:
    - origin, destination, equipment_type: exact (case-insensitive) equality
    - pickup_date: same day via date_only() on intake["pickup_datetime"] vs load["pickup_date"]

    Returns a dict shaped like the current API expects.
    """
    origin = (intake.get("origin") or "").strip().lower()
    dest = (intake.get("destination") or "").strip().lower()
    equipment = (intake.get("equipment_type") or "").strip().lower()
    pickup_date = date_only(intake.get("pickup_datetime"))

    matches: List[Dict] = []
    for load in loads:
        load_origin = (load.get("origin") or "").strip().lower()
        load_dest = (load.get("destination") or "").strip().lower()
        load_equipment = (load.get("equipment_type") or "").strip().lower()
        load_pickup_date = date_only(load.get("pickup_date"))

        if not pickup_date or load_pickup_date != pickup_date:
            continue
        if not origin or load_origin != origin:
            continue
        if not dest or load_dest != dest:
            continue
        if not equipment or load_equipment != equipment:
            continue

        m = dict(load)
        m["match_score"] = 4
        m["match_reasons"] = [
            "Origin exact",
            "Destination exact",
            "Pickup date exact",
            "Equipment exact",
        ]
        matches.append(m)

    matches.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    return {
        "matches": matches[:limit],
        "source": "fake_loads_file",
        "status": "ready",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_available": len(loads),
    }
