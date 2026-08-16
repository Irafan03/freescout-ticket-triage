"""
freescout_api.py — Thin client for the mikeyperes/freescout-api-webhooks REST API.

All requests are authenticated with the X-FreeScout-API-Key header.
Generate a key at: Manage → API & Webhooks → New Key

Endpoints used:
  GET  /api/v1/conversations          — poll for new tickets
  PUT  /api/v1/conversations/{id}     — write back triage meta, mailbox
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

try:
    from config import (
        FREESCOUT_URL,
        FREESCOUT_API_KEY,
        FREESCOUT_MAILBOX_ID,
        FREESCOUT_NEEDS_REVIEW_MAILBOX_ID,
    )
except ModuleNotFoundError:
    from backend.config import (
        FREESCOUT_URL,
        FREESCOUT_API_KEY,
        FREESCOUT_MAILBOX_ID,
        FREESCOUT_NEEDS_REVIEW_MAILBOX_ID,
    )

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {
        "X-Api-Key":    FREESCOUT_API_KEY,
        "Accept":       "application/json",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return FREESCOUT_URL.rstrip("/")


# ── Read ──────────────────────────────────────────────────────────────────────

def list_conversations(
    mailbox_id: int | None = None,
    status: str = "active",
    page: int = 1,
    per_page: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetch a page of conversations from FreeScout.
    Returns an empty list if the API key is not configured or the request fails.
    """
    if not FREESCOUT_API_KEY:
        logger.warning("FREESCOUT_API_KEY not set — polling disabled")
        return []

    params: dict[str, Any] = {
        "status":   status,
        "page":     page,
        "per_page": per_page,
    }
    if mailbox_id is not None:
        params["mailbox_id"] = mailbox_id

    try:
        resp = httpx.get(
            f"{_base()}/api/v1/conversations",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("data", [])
    except httpx.HTTPStatusError as e:
        logger.error("FreeScout API error listing conversations: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error listing conversations: %s", e)
        return []


# ── Write-back ────────────────────────────────────────────────────────────────

def apply_triage_result(conversation_id: int, result: dict[str, Any]) -> bool:
    """
    Write the triage result into FreeScout via a single PUT.

    Sets priority, tags, and all triage meta fields so the sidebar
    ServiceProvider (ApiWebhooksServiceProvider.php) can render them.
    Also moves the conversation to the Needs Review mailbox when confidence
    is below threshold.

    Returns True on success, False on failure.
    """
    if not FREESCOUT_API_KEY:
        logger.warning("FREESCOUT_API_KEY not set — write-back skipped")
        return False

    category     = result.get("category", "general")
    priority     = result.get("priority", "normal")
    sentiment    = result.get("sentiment", "neutral")
    confidence   = float(result.get("confidence", 0.0))
    reason       = result.get("reason", "")
    needs_review = bool(result.get("needs_review", False))
    draft_reply  = result.get("draft_reply", "")

    update_payload: dict[str, Any] = {
        "priority":            priority,
        "tags":                [category, f"priority:{priority}", f"sentiment:{sentiment}"],
        "triage_confidence":   confidence,
        "triage_needs_review": needs_review,
        "triage_reason":       reason,
        "triage_draft_reply":  draft_reply,
    }

    # Move to Needs Review mailbox when configured
    if needs_review and FREESCOUT_NEEDS_REVIEW_MAILBOX_ID != FREESCOUT_MAILBOX_ID:
        update_payload["mailbox_id"] = FREESCOUT_NEEDS_REVIEW_MAILBOX_ID

    try:
        resp = httpx.put(
            f"{_base()}/api/v1/conversations/{conversation_id}",
            headers=_headers(),
            json=update_payload,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(
            "Triage written  conversation=%d  category=%s  priority=%s  "
            "confidence=%.2f  needs_review=%s",
            conversation_id, category, priority, confidence, needs_review,
        )
        return True
    except httpx.HTTPStatusError as e:
        logger.error(
            "Triage PUT failed  conversation=%d  %s — %s",
            conversation_id, e, e.response.text[:200],
        )
        return False
    except Exception as e:
        logger.error("Triage PUT error  conversation=%d  %s", conversation_id, e)
        return False
