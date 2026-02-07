"""Tests 3-7: Filter rejection paths."""

from __future__ import annotations

import pytest

from hvym_pinner.policy.filter import PolicyOfferFilter, ESTIMATED_TX_FEE
from hvym_pinner.stellar.queries import SlotInfo
from tests.conftest import TEST_PUBLIC
from tests.factories import make_pin_event
from tests.mocks import MockQueries


# ── Test 3: Price too low ─────────────────────────────────────────


async def test_reject_price_too_low(daemon, store, mock_executor):
    """offer_price < min_price → rejected, executor never called."""
    event = make_pin_event(slot_id=3, offer_price=50)

    await daemon._handle_pin_event(event)

    offer = await store.get_offer(3)
    assert offer is not None
    assert offer.status == "rejected"
    assert offer.reject_reason == "price_too_low"
    assert len(mock_executor.pin_calls) == 0


# ── Test 4: Insufficient balance ─────────────────────────────────


async def test_reject_insufficient_balance(daemon, store, mock_executor, mock_queries):
    """Wallet balance below threshold → rejected insufficient_xlm."""
    mock_queries.wallet_balance = 10_000  # well below ESTIMATED_TX_FEE * 2

    event = make_pin_event(slot_id=4, offer_price=1_000_000)

    await daemon._handle_pin_event(event)

    offer = await store.get_offer(4)
    assert offer is not None
    assert offer.status == "rejected"
    assert offer.reject_reason == "insufficient_xlm"
    assert len(mock_executor.pin_calls) == 0


# ── Test 5: Slot expired ─────────────────────────────────────────


async def test_reject_slot_expired(daemon, store, mock_executor, mock_queries):
    """Slot expired on-chain → rejected slot_not_active."""
    mock_queries.slot_expired = True

    event = make_pin_event(slot_id=5, offer_price=1_000_000)

    await daemon._handle_pin_event(event)

    offer = await store.get_offer(5)
    assert offer is not None
    assert offer.status == "rejected"
    assert offer.reject_reason == "slot_not_active"
    assert len(mock_executor.pin_calls) == 0


# ── Test 6: Slot filled (pins_remaining = 0) ─────────────────────


async def test_reject_slot_filled(daemon, store, mock_executor, mock_queries):
    """pins_remaining == 0 → rejected slot_not_active."""
    mock_queries._slot_info = SlotInfo(
        slot_id=6,
        cid_hash="abc",
        publisher="GABCDEF...",
        offer_price=1_000_000,
        pin_qty=3,
        pins_remaining=0,
        escrow_balance=0,
        created_at=0,
        claims=[],
    )

    event = make_pin_event(slot_id=6, offer_price=1_000_000)

    await daemon._handle_pin_event(event)

    offer = await store.get_offer(6)
    assert offer is not None
    assert offer.status == "rejected"
    assert offer.reject_reason == "slot_not_active"
    assert len(mock_executor.pin_calls) == 0


# ── Test 7: Unprofitable ─────────────────────────────────────────


async def test_reject_unprofitable(daemon, store, mock_executor, mock_queries):
    """offer_price <= ESTIMATED_TX_FEE → rejected unprofitable."""
    # Set min_price very low so price check passes first
    daemon.filter.min_price = 1

    event = make_pin_event(slot_id=7, offer_price=50_000)  # < ESTIMATED_TX_FEE (100_000)

    await daemon._handle_pin_event(event)

    offer = await store.get_offer(7)
    assert offer is not None
    assert offer.status == "rejected"
    assert offer.reject_reason == "unprofitable"
    assert len(mock_executor.pin_calls) == 0
