import os
import time
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class ResultRepository:
    """DynamoDB-backed repository for intake results."""

    def __init__(self, table_name: Optional[str] = None) -> None:
        self.table_name = table_name or os.getenv("RESULTS_TABLE", "").strip()
        self._table = None
        if self.table_name:
            try:
                self._table = boto3.resource("dynamodb").Table(self.table_name)
            except Exception:
                self._table = None

    def save(self, request_id: str, result: Dict[str, Any]) -> bool:
        """Save a result document with TTL and request_id."""
        if not self._table:
            return False
        ttl_seconds = int(os.getenv("RESULT_TTL_SECONDS", "86400"))  # default 1 day
        item = dict(result)
        item["request_id"] = request_id
        item["ttl"] = int(time.time()) + ttl_seconds
        try:
            self._table.put_item(Item=item)
            return True
        except (BotoCoreError, ClientError):
            return False

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a previously saved result by request_id."""
        if not self._table:
            return None
        try:
            resp = self._table.get_item(Key={"request_id": request_id})
            return resp.get("Item")
        except (BotoCoreError, ClientError):
            return None
