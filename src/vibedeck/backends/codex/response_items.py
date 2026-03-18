"""Shared parsing helpers for Codex response_item payloads."""

from __future__ import annotations

import json


def get_function_call_output_state(payload: dict) -> tuple[str | list | None, bool]:
    """Return normalized function_call_output content and error state."""
    is_error = bool(
        payload.get("is_error")
        or payload.get("error")
        or payload.get("status") in {"error", "failed"}
    )

    content = payload.get("output")
    if content is None and payload.get("error") is not None:
        content = payload.get("error")
    if content is not None and not isinstance(content, (str, list)):
        content = json.dumps(content, ensure_ascii=False)

    return content, is_error
