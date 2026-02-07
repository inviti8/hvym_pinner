"""Tests 15-18: Approval workflow and mode switching."""

from __future__ import annotations

import pytest

from hvym_pinner.models.config import DaemonMode
from tests.factories import make_pin_event


# ── Test 15: Approve mode queues offer ─────────────────────────────


async def test_approve_mode_queues_offer(daemon, store, mock_executor, mock_submitter):
    """Mode=APPROVE → filter accepts → offer queued, not executed."""
    daemon.mode_ctrl.set_mode(DaemonMode.APPROVE)

    event = make_pin_event(slot_id=15, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(15)
    assert offer is not None
    assert offer.status == "awaiting_approval"

    # Executor and submitter never called
    assert len(mock_executor.pin_calls) == 0
    assert len(mock_submitter.claim_calls) == 0


# ── Test 16: Approve then execute ──────────────────────────────────


async def test_approve_then_execute(daemon, store, mock_executor, mock_submitter):
    """Queued offer → approve → daemon processes → claimed."""
    daemon.mode_ctrl.set_mode(DaemonMode.APPROVE)

    event = make_pin_event(slot_id=16, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    assert (await store.get_offer(16)).status == "awaiting_approval"

    # Approve via data_api
    results = await daemon.data_api.approve_offers([16])
    assert results[0].success

    assert (await store.get_offer(16)).status == "approved"

    # Simulate what the main loop does: process approved offers
    approved = await store.get_offers_by_status("approved")
    for offer in approved:
        from hvym_pinner.models.events import PinEvent
        pin_event = PinEvent(
            slot_id=offer.slot_id,
            cid=offer.cid,
            filename=offer.filename,
            gateway=offer.gateway,
            offer_price=offer.offer_price,
            pin_qty=offer.pin_qty,
            publisher=offer.publisher,
            ledger_sequence=offer.ledger_sequence,
        )
        await daemon._execute_pin_and_claim(pin_event)

    offer = await store.get_offer(16)
    assert offer.status == "claimed"
    assert len(mock_executor.pin_calls) == 1
    assert len(mock_submitter.claim_calls) == 1


# ── Test 17: Reject queued offer ──────────────────────────────────


async def test_reject_queued_offer(daemon, store, mock_executor, mock_submitter):
    """Queued offer → reject → operator_rejected."""
    daemon.mode_ctrl.set_mode(DaemonMode.APPROVE)

    event = make_pin_event(slot_id=17, offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    results = await daemon.data_api.reject_offers([17])
    assert results[0].success

    offer = await store.get_offer(17)
    assert offer.status == "rejected"
    assert offer.reject_reason == "operator_rejected"

    # Nothing executed
    assert len(mock_executor.pin_calls) == 0
    assert len(mock_submitter.claim_calls) == 0


# ── Test 18: Mode switch mid-flight ──────────────────────────────


async def test_mode_switch_mid_flight(daemon, store, mock_executor, mock_submitter):
    """Auto→Approve→Auto: mode switch affects event handling correctly."""
    # 1. Auto mode: process one event (claimed)
    event1 = make_pin_event(slot_id=181, offer_price=1_000_000)
    await daemon._handle_pin_event(event1)
    assert (await store.get_offer(181)).status == "claimed"
    assert len(mock_executor.pin_calls) == 1

    # 2. Switch to approve mode
    await daemon.data_api.set_mode("approve")
    assert daemon.mode_ctrl.get_mode() == DaemonMode.APPROVE

    # 3. New PIN event should queue
    event2 = make_pin_event(slot_id=182, offer_price=1_000_000, cid="QmSecondCID")
    await daemon._handle_pin_event(event2)
    assert (await store.get_offer(182)).status == "awaiting_approval"
    assert len(mock_executor.pin_calls) == 1  # still 1, not executed

    # 4. Switch back to auto
    await daemon.data_api.set_mode("auto")
    assert daemon.mode_ctrl.get_mode() == DaemonMode.AUTO

    # 5. Queued offer does NOT auto-execute (must be explicitly approved)
    event3 = make_pin_event(slot_id=183, offer_price=1_000_000, cid="QmThirdCID")
    await daemon._handle_pin_event(event3)
    # Third event goes through auto (pin+claim)
    assert (await store.get_offer(183)).status == "claimed"
    # But queued one from approve mode is still waiting
    assert (await store.get_offer(182)).status == "awaiting_approval"
