"""Tests 8-11: Pin and claim error handling."""

from __future__ import annotations

import pytest

from tests.factories import make_pin_event
from tests.mocks import MockExecutor, MockSubmitter


# ── Test 8: Pin timeout/failure ───────────────────────────────────


async def test_error_pin_timeout(daemon, store, mock_submitter):
    """Pin fails → status pin_failed, claim never attempted."""
    daemon.executor = MockExecutor(
        succeed=False, error="gateway timeout after 3 attempts",
    )

    event = make_pin_event(slot_id=8, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(8)
    assert offer is not None
    assert offer.status == "pin_failed"
    assert "gateway timeout" in (offer.reject_reason or "")

    # Claim never attempted
    assert len(mock_submitter.claim_calls) == 0

    # Activity log has pin_failed
    activity = await store.get_recent_activity(50)
    assert any(a.event_type == "pin_failed" for a in activity)


# ── Test 9: Claim already_claimed ─────────────────────────────────


async def test_error_claim_already_claimed(daemon, store, mock_executor):
    """Pin succeeds, claim returns already_claimed → claim_failed."""
    daemon.submitter = MockSubmitter(succeed=False, error="already_claimed")

    event = make_pin_event(slot_id=9, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(9)
    assert offer is not None
    assert offer.status == "claim_failed"
    assert "already_claimed" in (offer.reject_reason or "")

    # Pin was saved (pin succeeded before claim failed)
    assert await store.is_cid_pinned(event.cid)

    # No earnings
    earnings = await store.get_earnings()
    assert earnings.total_earned == 0


# ── Test 10: Claim slot_expired ───────────────────────────────────


async def test_error_claim_slot_expired(daemon, store):
    """Pin succeeds, claim returns slot_expired → claim_failed."""
    daemon.submitter = MockSubmitter(succeed=False, error="slot_expired")

    event = make_pin_event(slot_id=10, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(10)
    assert offer is not None
    assert offer.status == "claim_failed"
    assert "slot_expired" in (offer.reject_reason or "")


# ── Test 11: Claim not_pinner ─────────────────────────────────────


async def test_error_claim_not_pinner(daemon, store):
    """Pin succeeds, claim returns not_pinner → claim_failed."""
    daemon.submitter = MockSubmitter(succeed=False, error="not_pinner")

    event = make_pin_event(slot_id=11, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(11)
    assert offer is not None
    assert offer.status == "claim_failed"
    assert "not_pinner" in (offer.reject_reason or "")
