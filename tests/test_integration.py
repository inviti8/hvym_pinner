"""Tests 21-23: Concurrent events, persistence, dashboard accuracy."""

from __future__ import annotations

import pytest

from hvym_pinner.models.config import DaemonMode
from tests.conftest import TEST_PUBLIC
from tests.factories import make_pin_event


# ── Test 21: Concurrent PIN events in one batch ───────────────────


async def test_concurrent_pin_events(daemon, store, mock_executor, mock_submitter):
    """3 PIN events in one batch → all 3 claimed, earnings = sum of all 3."""
    events = [
        make_pin_event(slot_id=211, offer_price=1_000_000, cid="QmCID_A"),
        make_pin_event(slot_id=212, offer_price=2_000_000, cid="QmCID_B"),
        make_pin_event(slot_id=213, offer_price=500_000, cid="QmCID_C"),
    ]

    for event in events:
        await daemon._handle_pin_event(event)

    # All 3 should be claimed
    for slot_id in [211, 212, 213]:
        offer = await store.get_offer(slot_id)
        assert offer is not None
        assert offer.status == "claimed"

    # Executor called 3 times
    assert len(mock_executor.pin_calls) == 3

    # Submitter called 3 times
    assert len(mock_submitter.claim_calls) == 3

    # Earnings = sum
    earnings = await store.get_earnings()
    assert earnings.total_earned == 3_500_000
    assert earnings.claims_count == 3


# ── Test 22: Cursor persistence and resume ────────────────────────


async def test_cursor_persistence_and_resume(daemon, store, mock_poller):
    """Process events → save cursor → new daemon resumes from cursor."""
    # Set a cursor
    mock_poller._cursor = "100050-0"
    ledger = await mock_poller.get_cursor()
    assert ledger == 100050

    await store.set_cursor(ledger)

    # Verify cursor persisted
    saved = await store.get_cursor()
    assert saved == 100050

    # A new poller reading from the same store resumes correctly
    from tests.mocks import MockPoller
    new_poller = MockPoller()
    restored_ledger = await store.get_cursor()
    new_poller.set_cursor(f"{restored_ledger}-0")
    assert await new_poller.get_cursor() == 100050


# ── Test 23: Dashboard snapshot accuracy ──────────────────────────


async def test_dashboard_snapshot_accuracy(daemon, store, mock_queries):
    """Process mixed events → dashboard snapshot reflects all state."""
    # 1 claimed
    event1 = make_pin_event(slot_id=231, offer_price=1_000_000, cid="QmClaimed")
    await daemon._handle_pin_event(event1)

    # 1 rejected (price too low)
    event2 = make_pin_event(slot_id=232, offer_price=50, cid="QmRejected")
    await daemon._handle_pin_event(event2)

    # 1 awaiting approval
    daemon.mode_ctrl.set_mode(DaemonMode.APPROVE)
    event3 = make_pin_event(slot_id=233, offer_price=1_000_000, cid="QmQueued")
    await daemon._handle_pin_event(event3)

    # Get dashboard
    dashboard = await daemon.data_api.get_dashboard()

    assert dashboard.mode == "approve"
    assert dashboard.pinner_address == TEST_PUBLIC
    assert dashboard.offers_seen == 3
    assert dashboard.offers_rejected >= 1
    assert dashboard.offers_awaiting_approval == 1
    assert dashboard.claims_completed == 1
    assert dashboard.earnings.total_earned_stroops == 1_000_000
    assert dashboard.pins_active >= 1

    # Approval queue has the queued offer
    assert len(dashboard.approval_queue) == 1
    assert dashboard.approval_queue[0].slot_id == 233

    # Activity feed has entries
    assert len(dashboard.recent_activity) > 0
