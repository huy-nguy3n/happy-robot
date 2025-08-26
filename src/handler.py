import json, uuid
import re
from datetime import datetime, timezone
from repos.load_repo import LoadRepository
from repos.result_repo import ResultRepository
from services.matching_service import match_loads
from clients.fmcsa_client import FmcsaClient
from utils import json_default as _utils_json_default, date_only as _utils_date_only, sanitize_mc as _utils_sanitize_mc

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
    """Delegate to `utils.json_default` for JSON-safe serialization."""
    return _utils_json_default(o)


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

RESULT_REPO = ResultRepository()


def _sanitize_mc(mc: str) -> str:
    """Delegate to `utils.sanitize_mc` for MC normalization."""
    return _utils_sanitize_mc(mc)


def _fetch_loads(intake: dict):
    """Thin wrapper: read loads and apply matching service (limit 3)."""
    loads = LoadRepository().list()
    return match_loads(intake, loads, limit=3)


def _compute_result(intake: dict, request_id: str):
    """
    Compute the composite result for a new intake.

    Runs FMCSA verification and local load matching, then packages a
    response shape that can be saved and summarized.
    """
    fmcsa = FmcsaClient().verify(intake["mc_number"])
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
        item = RESULT_REPO.get(req_id)
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
        existing = RESULT_REPO.get(req_id)
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

        if not RESULT_REPO.save(req_id, existing):
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
    RESULT_REPO.save(request_id, result)

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
