"""Truthful static capability cards for optional asset intelligence providers."""

from __future__ import annotations

import copy
from typing import Any


_CAPABILITY_CARDS: dict[str, dict[str, Any]] = {
    "caption": {
        "id": "caption",
        "status": "unavailable",
        "reason": "No caption provider runtime or model smoke is part of this static milestone.",
        "action": "Authorize a separately bounded provider integration and smoke before exposing captioning.",
        "metadata_only": True,
        "execution": "not_run",
    },
    "tag": {
        "id": "tag",
        "status": "unavailable",
        "reason": "No tagging provider runtime or model smoke is part of this static milestone.",
        "action": "Authorize a separately bounded provider integration and smoke before exposing tagging.",
        "metadata_only": True,
        "execution": "not_run",
    },
    "embedding": {
        "id": "embedding",
        "status": "partial",
        "reason": "Typed embedding fingerprint metadata can be validated, but no embedding vector, provider, or model is executed.",
        "action": "Use metadata only; authorize a bounded provider smoke before any similarity or retrieval claim.",
        "metadata_only": True,
        "execution": "not_run",
    },
}


def provider_capability_cards() -> dict[str, Any]:
    """Return detached static cards; this function never probes a provider."""

    cards = [copy.deepcopy(_CAPABILITY_CARDS[key]) for key in sorted(_CAPABILITY_CARDS)]
    return {
        "contract": "asset-intelligence-provider-cards.v1",
        "status": "partial",
        "reason": "Cards describe static integration truth only; no provider was discovered or executed.",
        "action": "Treat unavailable/partial states as non-operational until a separately authorized smoke passes.",
        "cards": cards,
        "execution": "not_run",
    }
