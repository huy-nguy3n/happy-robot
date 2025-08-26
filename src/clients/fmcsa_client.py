import json
import os
import time
import socket
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote

from utils import sanitize_mc as _sanitize_mc


class FmcsaClient:
    """Thin client for FMCSA QC Services API.

    Reads configuration from environment variables by default:
      - FMCSA_WEBKEY (required)
      - FMCSA_BASE_URL (default: https://mobile.fmcsa.dot.gov/qc/services)
      - FMCSA_MAX_RETRIES (default: 0)
      - FMCSA_BACKOFF_SECONDS (default: 0.75)
      - FMCSA_TIMEOUT_SECONDS (default: 28)
    """

    def __init__(
        self,
        webkey: str | None = None,
        base_url: str | None = None,
        max_retries: int | None = None,
        backoff_seconds: float | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.webkey = (webkey or os.getenv("FMCSA_WEBKEY", "")).strip()
        self.base_url = (base_url or os.getenv("FMCSA_BASE_URL", "https://mobile.fmcsa.dot.gov/qc/services")).rstrip("/")
        self.max_retries = int(os.getenv("FMCSA_MAX_RETRIES", str(max_retries if max_retries is not None else 0)))
        self.backoff = float(os.getenv("FMCSA_BACKOFF_SECONDS", str(backoff_seconds if backoff_seconds is not None else 0.75)))
        self.timeout = int(os.getenv("FMCSA_TIMEOUT_SECONDS", str(timeout_seconds if timeout_seconds is not None else 28)))

    def verify(self, mc: str) -> dict:
        """Verify a carrier via `/carriers/{mc}?webKey=...` and normalize fields.

        Returns dict with keys: valid, allowed_to_operate, dot_number, carrier_name,
        endpoint (redacted webKey), checked_at, raw?, error?
        """
        now = datetime.now(timezone.utc).isoformat()
        mc_clean = _sanitize_mc(mc)
        if not self.webkey or not mc_clean:
            return {"valid": False, "error": "missing_webkey_or_mc", "checked_at": now}

        url = f"{self.base_url}/carriers/{quote(mc_clean)}?webKey={quote(self.webkey)}"

        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                req = Request(url, headers={"Accept": "application/json"})
                with urlopen(req, timeout=self.timeout) as resp:
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
                    "endpoint": url.replace(self.webkey, "****"),
                    "checked_at": now,
                    "raw": data,
                    "error": None if content else (data.get("content") or "not_found"),
                }
            except socket.timeout as e:
                last_err = e
            except URLError as e:
                reason_text = str(getattr(e, "reason", e))
                if "timed out" in reason_text.lower():
                    last_err = e
                else:
                    return {
                        "valid": False,
                        "endpoint": url.replace(self.webkey, "****"),
                        "checked_at": now,
                        "error": str(e),
                    }
            except Exception as e:
                return {
                    "valid": False,
                    "endpoint": url.replace(self.webkey, "****"),
                    "checked_at": now,
                    "error": str(e),
                }

            if attempt < self.max_retries:
                time.sleep(self.backoff * (2 ** attempt))
                continue
            break

        return {
            "valid": False,
            "endpoint": url.replace(self.webkey, "****"),
            "checked_at": now,
            "error": str(last_err) if last_err else "request_failed",
        }
