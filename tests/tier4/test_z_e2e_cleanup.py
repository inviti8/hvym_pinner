"""Tier 4 cleanup: cancel remaining slots, unpin CIDs, leave as pinner."""

from __future__ import annotations

import httpx
import pytest

from tests.tier4.conftest import make_client, simulate_query, timed_op

pytestmark = pytest.mark.e2e


async def test_e2e_cancel_remaining_slots(
    publisher_keypair,
    timing,
    tier4_state,
):
    """Cancel any unclaimed slots created during the Tier 4 session."""
    slot_ids = tier4_state.get("slot_ids", [])
    if not slot_ids:
        pytest.skip("No slots to cancel")

    client = make_client()
    try:
        cancelled = 0
        skipped = 0
        for slot_id in list(slot_ids):
            try:
                async with timed_op(timing, f"cancel_slot_{slot_id}") as rec:
                    tx = await client.cancel_pin(
                        caller=publisher_keypair.public_key,
                        slot_id=slot_id,
                        source=publisher_keypair.public_key,
                        signer=publisher_keypair,
                    )
                    refund = await tx.sign_and_submit()
                    rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                    rec["result"] = f"refund={refund}"
                    cancelled += 1
            except Exception as e:
                timing.add(f"cancel_slot_{slot_id} (skip)", 0, result=str(e)[:80])
                skipped += 1

        timing.add("cancel_summary", 0, result=f"cancelled={cancelled}, skipped={skipped}")
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


async def test_e2e_unpin_cids(
    timing,
    tier4_state,
):
    """Unpin all CIDs pinned during the Tier 4 session from local Kubo."""
    cids = tier4_state.get("cids_pinned", [])
    if not cids:
        pytest.skip("No CIDs to unpin")

    unpinned = 0
    skipped = 0
    async with httpx.AsyncClient() as client:
        for cid in list(set(cids)):  # deduplicate
            try:
                async with timed_op(timing, f"unpin_{cid[:16]}") as rec:
                    resp = await client.post(
                        "http://127.0.0.1:5001/api/v0/pin/rm",
                        params={"arg": cid},
                    )
                    if resp.status_code == 200:
                        unpinned += 1
                        rec["result"] = "unpinned"
                    else:
                        skipped += 1
                        rec["result"] = f"status={resp.status_code}"
            except Exception as e:
                timing.add(f"unpin_{cid[:16]} (skip)", 0, result=str(e)[:80])
                skipped += 1

    timing.add("unpin_summary", 0, result=f"unpinned={unpinned}, skipped={skipped}")


async def test_e2e_leave_as_pinner(
    pinner_a_keypair,
    timing,
):
    """Pinner A leaves, recovering their stake."""
    client = make_client()
    try:
        # Check if still registered
        async with timed_op(timing, "is_pinner[pinner_a]") as rec:
            is_reg = await simulate_query(client, "is_pinner", pinner_a_keypair.public_key)
            rec["result"] = str(is_reg)

        if not is_reg:
            timing.add("leave[pinner_a] (skip)", 0, result="not registered")
            return

        # Leave
        async with timed_op(timing, "leave_as_pinner[pinner_a]") as rec:
            tx = await client.leave_as_pinner(
                caller=pinner_a_keypair.public_key,
                source=pinner_a_keypair.public_key,
                signer=pinner_a_keypair,
            )
            stake_returned = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
            rec["result"] = f"stake_returned={stake_returned}"

        # Verify no longer a pinner
        async with timed_op(timing, "verify_left[pinner_a]") as rec:
            still_pinner = await simulate_query(client, "is_pinner", pinner_a_keypair.public_key)
            rec["result"] = f"is_pinner={still_pinner}"

        assert not still_pinner, "Pinner A should no longer be registered"
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
