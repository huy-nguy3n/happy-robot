import json, uuid
import os, re, time
import socket
from datetime import datetime, timezone
from decimal import Decimal
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote
from functools import lru_cache
import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Required fields for creating a new request (POST without request_id)
REQUIRED = [
    "mc_number",
    "origin",
    "destination",
    "pickup_datetime",
    "equipment_type",
]

# Optional fields we add to after the Inbound Call (POST with request_id)
OPTIONAL = [
    "delivery_datetime",
    "carrier_name",
    "rate_offer",
    "counter_offer",
    "outcome",
    "sentiment",
]

def _json_default(o):
    """
    Converts Decimal to int (when whole) or float; falls back to `str()` for
    unknown types. This makes DynamoDB's Decimal values JSON‑safe in responses.
    """
    if isinstance(o, Decimal):
        # Prefer ints when possible, otherwise float
        return int(o) if o % 1 == 0 else float(o)
    return str(o)


def _resp(status, body):
    """
    Build an API Gateway/Lambda proxy response.
    """
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-API-Key",
            "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        },
        "body": json.dumps(body, default=_json_default)
    }

def _init_table():
    table_name = os.getenv("RESULTS_TABLE")
    if not table_name:
        return None
    try:
        return boto3.resource("dynamodb").Table(table_name)
    except Exception:
        return None


TABLE = None


def _get_table():
    global TABLE
    if TABLE is None:
        TABLE = _init_table()
    return TABLE

@lru_cache(maxsize=1)
def _load_fake_loads():
    """Load sample load postings from local JSON for demo/testing.

    The file is expected at `./data/fake_loads.json` relative to this module.
    Result is memoized for the lifetime of the Lambda container.

    Returns:
    list[dict]: List of loads (or empty list on error/format mismatch).
    """
    path = os.path.join(os.path.dirname(__file__), "data", "fake_loads.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def _tokens(s: str):
    """
    Split a string into tokens (words, numbers, etc.)
    """
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 2]


def _date_only(s):
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


def _sanitize_mc(mc: str) -> str:
    """
    Normalize an MC number by removing all non‑digits.
    """
    return re.sub(r"[^0-9]", "", str(mc or ""))


def _fmcsa_verify(mc: str):
    """Verify a carrier via FMCSA QC Services API and normalize key fields.


    Environment variables:
        FMCSA_WEBKEY (required): API webKey issued by FMCSA.
        FMCSA_BASE_URL (optional): Base URL; defaults to official endpoint.
        FMCSA_MAX_RETRIES (optional): Number of retries for timeouts (default 0).
        FMCSA_BACKOFF_SECONDS (optional): Base backoff between retries.

    Behavior:
    • If webKey or MC number missing, returns `{valid: False, error: ...}`.
    • Calls a single endpoint `/carriers/{mc}?webKey=...`.
    • On success, extracts `allowedToOperate`, (US)DOT number, and legal name.
    • Redacts the webKey when echoing the endpoint.
    • Retries only on timeouts (socket/URLError with timeout reason).
    • Returns a uniform error object on failures.

    Returns:
        dict: { valid, allowed_to_operate, dot_number, carrier_name,
        endpoint, checked_at, raw?, error? }
    """
    mc_clean = _sanitize_mc(mc)
    webkey = os.getenv("FMCSA_WEBKEY", "").strip()
    base = os.getenv("FMCSA_BASE_URL", "https://mobile.fmcsa.dot.gov/qc/services").rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    if not webkey or not mc_clean:
        return {"valid": False, "error": "missing_webkey_or_mc", "checked_at": now}

    # Single-call approach: treat provided number as the identifier for /carriers/{num}
    url = f"{base}/carriers/{quote(mc_clean)}?webKey={quote(webkey)}"

    # Retries are supported but default to 0 because API Gateway's REST integration has ~29s hard limit.
    # 28s timeout, give max success, a retry would exceed the window and 504 the client.
    max_retries = int(os.getenv("FMCSA_MAX_RETRIES", "0"))
    backoff = float(os.getenv("FMCSA_BACKOFF_SECONDS", "0.75"))
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            req = Request(url, headers={"Accept": "application/json"})
            # Use 28s, 1s less than API Gateway's ~29s cap
            with urlopen(req, timeout=28) as resp:
                payload = resp.read().decode("utf-8", "ignore")
            data = json.loads(payload or "{}")
            content = data.get("content") if isinstance(data, dict) else None

            allowed = None
            dot = None
            legal = None
            if isinstance(content, dict):
                carrier = content.get("carrier") if isinstance(content.get("carrier"), dict) else None
                source = carrier or content
                allowed = source.get("allowedToOperate")
                dot = source.get("dotNumber") or source.get("usdotNumber")
                legal = source.get("legalName")

            is_valid = str(allowed).upper() == "Y"
            return {
                "valid": bool(is_valid),
                "allowed_to_operate": allowed,
                "dot_number": dot,
                "carrier_name": legal,
                "endpoint": url.replace(webkey, "****"),
                "checked_at": now,
                "raw": data,
                "error": None if content else (data.get("content") or "not_found"),
            }
        except socket.timeout as e:
            last_err = e
        except URLError as e:
            # Retry only on timeouts
            reason_text = str(getattr(e, "reason", e))
            if "timed out" in reason_text.lower():
                last_err = e
            else:
                return {
                    "valid": False,
                    "endpoint": url.replace(webkey, "****"),
                    "checked_at": now,
                    "error": str(e),
                }
        except Exception as e:
            return {
                "valid": False,
                "endpoint": url.replace(webkey, "****"),
                "checked_at": now,
                "error": str(e),
            }

        # If we got here, sleep (exponentially) and retry if attempts remain
        if attempt < max_retries:
            time.sleep(backoff * (2 ** attempt))
            continue
        break

    return {
        "valid": False,
        "endpoint": url.replace(webkey, "****"),
        "checked_at": now,
        "error": str(last_err) if last_err else "request_failed",
    }


def _fetch_loads(intake: dict):
    """Find up to three best‑matching demo loads for the given intake.

    Matching logic:
    • Require same pickup date as `intake["pickup_datetime"]` (date‑only compare).
    • +3 if any origin token appears in load origin.
    • +3 if any destination token appears in load destination.
    • +2 if equipment types roughly match (substring either direction).
    • Attach `match_reasons` and `match_score` for transparency; sort desc.

    Args:
    intake (dict): The intake payload with origin/destination/equipment/date.

    Returns:
    dict: { matches: list[dict], source, status, checked_at, total_available }
    """
    # Read and filter fake loads from local JSON for testing
    loads = _load_fake_loads()
    origin_tokens = _tokens(intake.get("origin"))
    dest_tokens = _tokens(intake.get("destination"))
    equipment = (intake.get("equipment_type") or "").lower()
    pickup_date = _date_only(intake.get("pickup_datetime"))

    matches = []
    for load in loads:
        load_origin = (load.get("origin") or "").lower()
        load_dest = (load.get("destination") or "").lower()
        load_equipment = (load.get("equipment_type") or "").lower()
        load_pickup_date = _date_only(load.get("pickup_date"))

        # Same-day pickup date is required
        if pickup_date and load_pickup_date != pickup_date:
            continue

        score = 0
        if origin_tokens and any(tok in load_origin for tok in origin_tokens):
            score += 3
        if dest_tokens and any(tok in load_dest for tok in dest_tokens):
            score += 3
        if equipment and (equipment in load_equipment or load_equipment in equipment):
            score += 2

        if score > 0:
            reasons = []
            if origin_tokens and any(tok in load_origin for tok in origin_tokens):
                reasons.append("Origin match")
            if dest_tokens and any(tok in load_dest for tok in dest_tokens):
                reasons.append("Destination match")
            if equipment and (equipment in load_equipment or load_equipment in equipment):
                reasons.append("Equipment match")
            if pickup_date and load_pickup_date == pickup_date:
                reasons.append("Pickup date match")
            m = dict(load)
            m["match_score"] = score
            m["match_reasons"] = reasons
            matches.append(m)

    matches.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return {
        "matches": matches[:3],
        "source": "fake_loads_file",
        "status": "ready",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_available": len(loads)
    }


def _save_result(request_id: str, result: dict):
    """
    Persist a result object to DynamoDB with a Time To Live.
    - Cost control, sensitive data, outdated loads
    """
    table = _get_table()
    if table is None:
        return False
    item = dict(result)
    ttl_seconds = int(os.getenv("RESULT_TTL_SECONDS", "86400"))  # 1 day default
    item["request_id"] = request_id
    item["ttl"] = int(time.time()) + ttl_seconds
    try:
        table.put_item(Item=item)
        return True
    except (BotoCoreError, ClientError):
        return False


def _get_result(request_id: str):
    """
    Fetch a previously saved result by `request_id` from DynamoDB.
    """
    table = _get_table()
    if table is None:
        return None
    try:
        resp = table.get_item(Key={"request_id": request_id})
        return resp.get("Item")
    except (BotoCoreError, ClientError):
        return None


def _compute_result(intake: dict, request_id: str):
    """
    Compute the composite result for a new intake.

    Runs FMCSA verification and local load matching, then packages a
    response shape that can be saved and summarized.
    """
    fmcsa = _fmcsa_verify(intake["mc_number"])
    loads = _fetch_loads(intake)
    return {
        "request_id": request_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "intake": intake,
        "fmcsa": fmcsa,
        "loads": loads,
        "status": "ready",
    }


def lambda_handler(event, context):
    """AWS Lambda entry point (API Gateway proxy integration compatible).

    Supports three flows:
    1) CORS preflight (OPTIONS): returns 200 with headers.
    2) GET /{request_id} or ?request_id=... : fetch previously saved result.
    3) POST:
    a. With `request_id` -> partial update of OPTIONAL intake fields.
    b. Without `request_id` -> create new record; requires all REQUIRED fields.


    Request/Response (high level):
    • GET success -> { ok: True, result: <saved item> }
    • POST create -> { ok: True, request_id, received_at, summary }
    • POST update -> { ok: True, request_id, updated_at }
    • Errors -> { ok: False, error: "..." [, errors: {field: reason} ] }
    """
    method = (event.get("httpMethod") or "").upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if method == "GET":
        path_params = event.get("pathParameters") or {}
        qsp = event.get("queryStringParameters") or {}
        req_id = path_params.get("request_id") or qsp.get("request_id")
        if not req_id:
            return _resp(400, {"ok": False, "error": "Missing request_id"})
        item = _get_result(req_id)
        if not item:
            return _resp(404, {"ok": False, "error": "Not found"})
        return _resp(200, {"ok": True, "result": item})

    # Default to POST
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"ok": False, "error": "Invalid JSON"})

    # Option B: If a request_id is provided, treat this POST as an update/enrichment
    if "request_id" in body:
        req_id = str(body.get("request_id") or "").strip()
        if not req_id:
            return _resp(400, {"ok": False, "error": "Invalid request_id"})
        existing = _get_result(req_id)
        if not existing:
            return _resp(404, {"ok": False, "error": "Not found"})

        # Collect provided optional fields to merge under result.intake
        updates = {k: body[k] for k in OPTIONAL if k in body}
        if not updates:
            return _resp(400, {"ok": False, "error": "No updatable fields provided"})

        existing_intake = existing.get("intake") or {}
        existing_intake.update(updates)
        existing["intake"] = existing_intake
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()

        if not _save_result(req_id, existing):
            return _resp(500, {"ok": False, "error": "Failed to save update"})
        return _resp(200, {"ok": True, "request_id": req_id, "updated_at": existing["updated_at"]})

    # Otherwise, create a new record (original flow)
    # Build per-field validation errors using a single generic message
    errors = {}
    for k in REQUIRED:
        v = body.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors[k] = "This field is missing or incorrect"
    if errors:
        return _resp(400, {"ok": False, "error": "validation_error", "errors": errors})

    request_id = str(uuid.uuid4())

    intake = {
        "mc_number": _sanitize_mc(body["mc_number"]),
        "origin": str(body["origin"]).strip(),
        "destination": str(body["destination"]).strip(),
        "pickup_datetime": str(body["pickup_datetime"]).strip(),
        "equipment_type": str(body["equipment_type"]).strip(),
    }

    result = _compute_result(intake, request_id)
    _save_result(request_id, result)

    return _resp(200, {
        "ok": True,
        "request_id": request_id,
        "received_at": result["received_at"],
        "summary": {
            "mc_valid": result["fmcsa"]["valid"],
            "matches_count": len(result["loads"]["matches"]),
            "status": result["status"],
        },
    })
