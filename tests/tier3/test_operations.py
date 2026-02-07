"""Tier 3 sequential operations: create_pin, collect_pin with timing."""

from __future__ import annotations

import uuid

import pytest
from stellar_sdk import Keypair

from hvym_pinner.bindings.hvym_pin_service import ClientAsync
from tests.tier3.conftest import (
    create_test_slot,
    make_client,
    simulate_query,
    timed_op,
    TimingCollector,
)

pytestmark = pytest.mark.testnet


def _unique_cid() -> str:
    """Generate a unique fake CID for each test slot."""
    return f"QmTest{uuid.uuid4().hex[:38]}"


# ── Module-scoped shared slot for collect tests ──────────────────

_shared_cid: str = _unique_cid()


@pytest.fixture(scope="module")
async def shared_slot(
    registered_pinners,
    publisher_keypair,
    tier3_state,
):
    """Create a single slot shared by the collect_pin tests in this module."""
    client = make_client()
    timing = TimingCollector()
    try:
        slot_id = await create_test_slot(
            client,
            publisher_keypair,
            timing,
            cid=_shared_cid,
            pin_qty=3,
            label="shared_slot_create",
        )
        tier3_state["slot_ids"].append(slot_id)
        return slot_id
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


# ── Tests ────────────────────────────────────────────────────────


async def test_create_single_slot(
    registered_pinners,
    publisher_keypair,
    timing,
    tier3_state,
):
    """Publisher creates a pin slot, verify on-chain state."""
    client = make_client()
    try:
        cid = _unique_cid()
        slot_id = await create_test_slot(
            client, publisher_keypair, timing, cid=cid, pin_qty=3,
        )
        tier3_state["slot_ids"].append(slot_id)

        # Verify on-chain
        async with timed_op(timing, "get_slot (verify)") as rec:
            slot = await simulate_query(client, "get_slot", slot_id)
            rec["result"] = f"publisher={slot.publisher.address[:12]}... pins_remaining={slot.pins_remaining}"

        assert slot is not None
        assert slot.publisher.address == publisher_keypair.public_key
        assert slot.pins_remaining == 3
        assert slot.offer_price == 10_000_000
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


async def test_collect_pin_pinner_a(
    shared_slot,
    pinner_a_keypair,
    timing,
):
    """Pinner A collects from the shared slot. collect_pin returns amount paid."""
    client = make_client()
    try:
        async with timed_op(timing, "collect_pin[pinner_a]") as rec:
            tx = await client.collect_pin(
                caller=pinner_a_keypair.public_key,
                slot_id=shared_slot,
                source=pinner_a_keypair.public_key,
                signer=pinner_a_keypair,
            )
            amount_paid = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
            rec["result"] = f"amount_paid={amount_paid}"

        assert amount_paid == 10_000_000  # offer_price in stroops

        # Verify on-chain state
        async with timed_op(timing, "get_slot (verify)") as rec:
            slot = await simulate_query(client, "get_slot", shared_slot)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"

        assert slot.pins_remaining == 2
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


async def test_collect_pin_pinner_b(
    shared_slot,
    pinner_b_keypair,
    timing,
):
    """Pinner B collects from the same shared slot."""
    client = make_client()
    try:
        async with timed_op(timing, "collect_pin[pinner_b]") as rec:
            tx = await client.collect_pin(
                caller=pinner_b_keypair.public_key,
                slot_id=shared_slot,
                source=pinner_b_keypair.public_key,
                signer=pinner_b_keypair,
            )
            amount_paid = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
            rec["result"] = f"amount_paid={amount_paid}"

        assert amount_paid == 10_000_000

        # Verify on-chain state
        async with timed_op(timing, "get_slot (verify)") as rec:
            slot = await simulate_query(client, "get_slot", shared_slot)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"

        assert slot.pins_remaining == 1
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


async def test_full_create_collect_cycle(
    registered_pinners,
    publisher_keypair,
    pinner_a_keypair,
    timing,
    tier3_state,
):
    """Full cycle: create slot -> pinner collects -> verify -> cancel remainder."""
    pub_client = make_client()
    pin_client = make_client()
    try:
        cid = _unique_cid()

        # Step 1: Create slot
        slot_id = await create_test_slot(
            pub_client, publisher_keypair, timing, cid=cid, pin_qty=3,
            label="cycle_create",
        )
        tier3_state["slot_ids"].append(slot_id)

        # Step 2: Pinner A collects
        async with timed_op(timing, "cycle_collect") as rec:
            tx = await pin_client.collect_pin(
                caller=pinner_a_keypair.public_key,
                slot_id=slot_id,
                source=pinner_a_keypair.public_key,
                signer=pinner_a_keypair,
            )
            amount_paid = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
            rec["result"] = f"amount_paid={amount_paid}"

        assert amount_paid == 10_000_000

        # Step 3: Verify on-chain
        async with timed_op(timing, "cycle_verify") as rec:
            slot = await simulate_query(pub_client, "get_slot", slot_id)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"

        assert slot.pins_remaining == 2

        # Step 4: Cancel remainder
        async with timed_op(timing, "cycle_cancel") as rec:
            tx = await pub_client.cancel_pin(
                caller=publisher_keypair.public_key,
                slot_id=slot_id,
                source=publisher_keypair.public_key,
                signer=publisher_keypair,
            )
            refund = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
            rec["result"] = f"refund={refund}"

        # Remove from cleanup since we cancelled it
        if slot_id in tier3_state["slot_ids"]:
            tier3_state["slot_ids"].remove(slot_id)
    finally:
        for c in [pub_client, pin_client]:
            try:
                await c.server.close()
            except Exception:
                pass
