"""Tests 12-14: PIN → PINNED → UNPIN state transitions."""

from __future__ import annotations

import pytest

from tests.conftest import TEST_PUBLIC
from tests.factories import make_pin_event, make_pinned_event, make_unpin_event


# ── Test 12: PINNED event with pins_remaining=0 marks offer as filled ──


async def test_pinned_event_updates_slot(daemon, store):
    """PINNED event with pins_remaining=0 → offer status becomes 'filled'."""
    pin_event = make_pin_event(slot_id=12, offer_price=1_000_000)
    await daemon._handle_pin_event(pin_event)

    # Should now be 'claimed'
    offer = await store.get_offer(12)
    assert offer is not None
    assert offer.status == "claimed"

    # PINNED event with no pins remaining → filled
    pinned = make_pinned_event(
        slot_id=12,
        cid=pin_event.cid,
        pinner=TEST_PUBLIC,
        pins_remaining=0,
    )
    await daemon._handle_pinned_event(pinned)

    offer = await store.get_offer(12)
    assert offer.status == "filled"


# ── Test 13: UNPIN event expires the offer ─────────────────────────


async def test_unpin_event_expires_offer(daemon, store):
    """UNPIN event → offer status becomes 'expired', activity logged."""
    pin_event = make_pin_event(slot_id=13, offer_price=1_000_000)
    await daemon._handle_pin_event(pin_event)

    unpin = make_unpin_event(slot_id=13, cid=pin_event.cid)
    await daemon._handle_unpin_event(unpin)

    offer = await store.get_offer(13)
    assert offer is not None
    assert offer.status == "expired"

    activity = await store.get_recent_activity(50)
    assert any(a.event_type == "offer_expired" for a in activity)


# ── Test 14: PINNED from another pinner with remaining > 0 ────────


async def test_pinned_event_from_other_pinner(daemon, store):
    """PINNED by someone else with pins_remaining > 0 → status stays."""
    pin_event = make_pin_event(slot_id=14, offer_price=1_000_000)
    await daemon._handle_pin_event(pin_event)

    status_before = (await store.get_offer(14)).status

    # PINNED from a different pinner, still pins remaining
    pinned = make_pinned_event(
        slot_id=14,
        cid=pin_event.cid,
        pinner="GOTHER_PINNER_ADDRESS_NOT_US_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        pins_remaining=2,
    )
    await daemon._handle_pinned_event(pinned)

    offer = await store.get_offer(14)
    # Status should NOT change to 'filled' since pins remain
    assert offer.status == status_before
