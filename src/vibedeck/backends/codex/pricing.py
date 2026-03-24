"""Token usage extraction for Codex rollout files."""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path

import yaml

from ..protocol import TokenUsage
from .discovery import _is_transcript_entry, is_bootstrap_user_message

logger = logging.getLogger(__name__)

_pricing_data: dict | None = None


def _iter_entries(session_path: Path):
    try:
        with open(session_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse Codex pricing line in %s", session_path)
    except OSError as exc:
        logger.warning("Failed to read Codex session %s: %s", session_path, exc)


def get_session_model(session_path: Path) -> str | None:
    """Get the most recently observed model for the session."""
    model = None
    for entry in _iter_entries(session_path):
        if entry.get("type") == "turn_context":
            candidate = entry.get("payload", {}).get("model")
            if candidate:
                model = candidate
    return model


def _get_pricing_data() -> dict:
    """Load and cache OpenAI pricing data used by the Codex backend."""
    global _pricing_data
    if _pricing_data is None:
        try:
            pricing_file = files("vibedeck").joinpath("openai_pricing.yaml")
            _pricing_data = yaml.safe_load(pricing_file.read_text())
        except Exception as exc:
            logger.warning("Failed to load openai_pricing.yaml: %s", exc)
            _pricing_data = {"models": {}}
    return _pricing_data


def get_model_pricing(model: str | None) -> dict | None:
    """Return pricing data for a model, if known."""
    if not model:
        return None

    pricing = _get_pricing_data().get("models", {}).get(model)
    if pricing is None:
        logger.warning("No Codex/OpenAI pricing configured for model %s", model)
    return pricing


def calculate_session_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    model: str | None,
) -> float:
    """Estimate session cost from Codex cumulative token totals."""
    pricing = get_model_pricing(model)
    if pricing is None:
        return 0.0

    uncached_input_tokens = max(input_tokens - cache_read_tokens, 0)

    cost = 0.0
    cost += (uncached_input_tokens / 1_000_000) * pricing.get("input", 0)
    cost += (output_tokens / 1_000_000) * pricing.get("output", 0)
    cost += (cache_read_tokens / 1_000_000) * pricing.get("cached_input", 0)
    return cost


def get_session_token_usage(session_path: Path) -> TokenUsage:
    """Map the latest Codex token_count event onto VibeDeck TokenUsage."""
    usage = TokenUsage()
    last_totals: dict | None = None
    latest_model: str | None = None
    models_seen: set[str] = set()

    for entry in _iter_entries(session_path):
        if _is_transcript_entry(entry):
            if is_bootstrap_user_message(entry):
                continue
            payload = entry.get("payload", {})
            if not (
                entry.get("type") == "event_msg" and payload.get("type") == "agent_message"
            ):
                usage.message_count += 1

        if entry.get("type") == "turn_context":
            model = entry.get("payload", {}).get("model")
            if model and model not in models_seen:
                models_seen.add(model)
                usage.models.append(model)
            if model:
                latest_model = model

        if entry.get("type") != "event_msg":
            continue
        payload = entry.get("payload", {})
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        totals = info.get("total_token_usage")
        if totals:
            last_totals = totals

    if last_totals:
        usage.input_tokens = int(last_totals.get("input_tokens", 0))
        usage.output_tokens = int(last_totals.get("output_tokens", 0))
        usage.cache_read_tokens = int(last_totals.get("cached_input_tokens", 0))
        usage.cache_creation_tokens = 0
        usage.cost = calculate_session_cost(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            model=latest_model,
        )

    return usage
