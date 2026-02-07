"""Tests 1-2: Auto mode full lifecycle (happy path)."""

from __future__ import annotations

import pytest

from tests.conftest import TEST_PUBLIC
from tests.factories import make_pin_event


# ── Test 1: Auto mode full lifecycle (Tier 1) ─────────────────────


async def test_auto_mode_full_lifecycle(daemon, store, mock_executor, mock_submitter):
    """PIN event → filter accepts → pin → claim → status transitions correct."""
    event = make_pin_event(slot_id=1, offer_price=1_000_000)

    await daemon._handle_pin_event(event)

    # Offer should be claimed
    offer = await store.get_offer(1)
    assert offer is not None
    assert offer.status == "claimed"
    assert offer.filename == "test-asset.glb"

    # Executor was called
    assert len(mock_executor.pin_calls) == 1
    assert mock_executor.pin_calls[0] == (event.cid, event.gateway)

    # Submitter was called
    assert len(mock_submitter.claim_calls) == 1
    assert mock_submitter.claim_calls[0] == 1

    # Pin record saved
    assert await store.is_cid_pinned(event.cid)

    # Earnings reflect the offer price
    earnings = await store.get_earnings()
    assert earnings.total_earned == 1_000_000
    assert earnings.claims_count == 1

    # Activity log contains expected entries
    activity = await store.get_recent_activity(50)
    event_types = [a.event_type for a in activity]
    assert "offer_seen" in event_types
    assert "pin_started" in event_types
    assert "pin_success" in event_types
    assert "claim_success" in event_types
