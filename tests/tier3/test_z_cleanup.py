"""Tier 3 cleanup: cancel remaining slots, leave_as_pinner."""

from __future__ import annotations

import pytest

from tests.tier3.conftest import make_client, simulate_query, timed_op

pytestmark = pytest.mark.testnet


async def test_cancel_remaining_slots(
    publisher_keypair,
    timing,
    tier3_state,
):
    """Cancel any unclaimed slots created during the test session."""
    slot_ids = tier3_state.get("slot_ids", [])
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
                # Slot may already be cancelled or fully collected
                timing.add(f"cancel_slot_{slot_id} (skip)", 0, result=str(e)[:80])
                skipped += 1

        timing.add("cancel_summary", 0, result=f"cancelled={cancelled}, skipped={skipped}")
    finally:
        try:
            await client.server.close()
        except Exception:
            pass


async def test_leave_as_pinner(
    pinner_a_keypair,
    pinner_b_keypair,
    timing,
):
    """Both pinners leave, recovering their stake."""
    for kp, label in [(pinner_a_keypair, "pinner_a"), (pinner_b_keypair, "pinner_b")]:
        client = make_client()
        try:
            # Check if still registered
            async with timed_op(timing, f"is_pinner[{label}]") as rec:
                is_reg = await simulate_query(client, "is_pinner", kp.public_key)
                rec["result"] = str(is_reg)

            if not is_reg:
                timing.add(f"leave[{label}] (skip)", 0, result="not registered")
                continue

            # Leave
            async with timed_op(timing, f"leave_as_pinner[{label}]") as rec:
                tx = await client.leave_as_pinner(
                    caller=kp.public_key,
                    source=kp.public_key,
                    signer=kp,
                )
                stake_returned = await tx.sign_and_submit()
                rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                rec["result"] = f"stake_returned={stake_returned}"

            # Verify no longer a pinner
            async with timed_op(timing, f"verify_left[{label}]") as rec:
                still_pinner = await simulate_query(client, "is_pinner", kp.public_key)
                rec["result"] = f"is_pinner={still_pinner}"

            assert not still_pinner, f"{label} should no longer be registered"
        finally:
            try:
                await client.server.close()
            except Exception:
                pass
