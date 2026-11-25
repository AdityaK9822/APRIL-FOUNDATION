"""Pure serializers shared by the API and its tests."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from ..memory.base import SessionInfo


def event_to_dict(event) -> dict:
    payload = event.payload
    if dataclasses.is_dataclass(payload):
        payload = dataclasses.asdict(payload)
    timestamp = getattr(event, "timestamp", None)
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    return {
        "type": event.event_type,
        "payload": payload,
        "timestamp": timestamp,
        "id": event.id,
    }


def session_payload(session_id: str, history: list[dict]) -> dict:
    return {
        "session_id": session_id,
        "messages": [
            {"role": row.get("role"), "content": row.get("content")}
            for row in history
        ],
    }


def session_info_to_dict(info: SessionInfo) -> dict:
    return {
        "id": info.id,
        "created_at": info.created_at,
        "metadata": info.metadata,
        "message_count": info.message_count,
    }
