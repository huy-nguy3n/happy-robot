import re
from datetime import datetime
from decimal import Decimal


def json_default(o):
    """
    Converts Decimal to int (when whole) or float; falls back to `str()` for
    unknown types. This makes DynamoDB's Decimal values JSON‑safe in responses.
    """
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    return str(o)


def date_only(s):
    """
    Extract an ISO date (`YYYY-MM-DD`) from inputs.
    Tries `datetime.fromisoformat` first; falls back to regex search.
    Returns `None` if no reasonable date is found.
    """
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).date().isoformat()
    except Exception:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(s or ""))
        return m.group(1) if m else None


def sanitize_mc(mc: str) -> str:
    """
    Normalize an MC number by removing all non‑digits.
    """
    return re.sub(r"[^0-9]", "", str(mc or ""))
