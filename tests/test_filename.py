"""Tests 28-30: Filename threading through the stack."""

from __future__ import annotations

import pytest

from hvym_pinner.models.config import DaemonMode
from hvym_pinner.models.events import PinEvent
from tests.factories import make_pin_event


# ── Test 28: Filename persisted in offer record ──────────────────


async def test_filename_persisted_in_offer(daemon, store):
    """PinEvent with filename → store.get_offer() returns correct filename."""
    event = make_pin_event(slot_id=28, filename="scene.glb")
    await daemon._handle_pin_event(event)

    offer = await store.get_offer(28)
    assert offer is not None
    assert offer.filename == "scene.glb"


# ── Test 29: Filename in offer snapshot via Data API ─────────────


async def test_filename_in_offer_snapshot(daemon, store):
    """PinEvent with filename → data_api.get_offers() returns correct filename."""
    event = make_pin_event(slot_id=29, filename="model.glb")
    await daemon._handle_pin_event(event)

    offers = await daemon.data_api.get_offers()
    assert len(offers) >= 1
    match = [o for o in offers if o.slot_id == 29]
    assert len(match) == 1
    assert match[0].filename == "model.glb"


# ── Test 30: Filename survives approved-offer reconstruction ──────


async def test_filename_in_approved_offer_reconstruction(daemon, store):
    """
    Mode=APPROVE → PIN with filename → queued → approved →
    daemon reconstructs PinEvent from OfferRecord → filename preserved.
    """
    daemon.mode_ctrl.set_mode(DaemonMode.APPROVE)

    event = make_pin_event(slot_id=30, filename="character.glb", offer_price=1_000_000)
    await daemon._handle_pin_event(event)

    # Approve
    await daemon.data_api.approve_offers([30])

    # Reconstruct PinEvent the same way the daemon main loop does
    approved = await store.get_offers_by_status("approved")
    assert len(approved) == 1
    offer = approved[0]

    reconstructed = PinEvent(
        slot_id=offer.slot_id,
        cid=offer.cid,
        filename=offer.filename,
        gateway=offer.gateway,
        offer_price=offer.offer_price,
        pin_qty=offer.pin_qty,
        publisher=offer.publisher,
        ledger_sequence=offer.ledger_sequence,
    )

    assert reconstructed.filename == "character.glb"
    assert reconstructed.cid == event.cid
    assert reconstructed.gateway == event.gateway
