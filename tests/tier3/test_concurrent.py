"""Tier 3 concurrent stress tests: parallel ops, cross-account races."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from tests.tier3.conftest import (
    create_test_slot,
    make_client,
    simulate_query,
    timed_op,
    TimingCollector,
)

pytestmark = pytest.mark.testnet


def _unique_cid() -> str:
    return f"QmTest{uuid.uuid4().hex[:38]}"


async def test_concurrent_create_5_slots(
    registered_pinners,
    publisher_keypair,
    timing,
    tier3_state,
):
    """Publisher creates 5 slots concurrently.

    Stellar requires sequential sequence numbers per account, so true
    concurrency may fail. If so, falls back to rapid-sequential and
    reports throughput. All slots are cancelled afterward to free capacity
    (contract has only 10 slots total).
    """
    cids = [_unique_cid() for _ in range(5)]
    slot_ids = []

    # Attempt concurrent (likely fails due to sequence number conflicts)
    async with timed_op(timing, "concurrent_create_5 (attempt)") as rec:
        try:
            tasks = []
            for i, cid in enumerate(cids):
                client = make_client()
                t = TimingCollector()
                tasks.append(
                    create_test_slot(
                        client, publisher_keypair, t, cid=cid,
                        label=f"concurrent_slot_{i}",
                    )
                )
            results = await asyncio.gather(*tasks, return_exceptions=True)

            successes = [r for r in results if isinstance(r, int)]
            failures = [r for r in results if isinstance(r, Exception)]
            slot_ids.extend(successes)
            rec["result"] = f"concurrent: {len(successes)} ok, {len(failures)} failed"

            if failures:
                # Expected: sequence number conflicts for same-account concurrency
                rec["result"] += " (seq# conflict, falling back to sequential)"
        except Exception as e:
            rec["result"] = f"concurrent failed: {e}"

    # Fall back to rapid-sequential if concurrent didn't fully succeed
    if len(slot_ids) < 5:
        needed = 5 - len(slot_ids)
        client = make_client()
        try:
            for i in range(needed):
                fresh_cid = _unique_cid()
                async with timed_op(timing, f"sequential_slot_{len(slot_ids)}") as rec:
                    sid = await create_test_slot(
                        client, publisher_keypair, TimingCollector(),
                        cid=fresh_cid, label=f"seq_{i}",
                    )
                    slot_ids.append(sid)
                    rec["result"] = f"slot_id={sid}"
        finally:
            try:
                await client.server.close()
            except Exception:
                pass

    assert len(slot_ids) == 5, f"Expected 5 slots, got {len(slot_ids)}"

    # Cancel all 5 slots to free capacity for remaining tests
    cancel_client = make_client()
    try:
        for sid in slot_ids:
            async with timed_op(timing, f"cancel_slot_{sid}") as rec:
                tx = await cancel_client.cancel_pin(
                    caller=publisher_keypair.public_key,
                    slot_id=sid,
                    source=publisher_keypair.public_key,
                    signer=publisher_keypair,
                )
                refund = await tx.sign_and_submit()
                rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                rec["result"] = f"refund={refund}"
    finally:
        try:
            await cancel_client.server.close()
        except Exception:
            pass


async def test_concurrent_collect_different_pinners(
    registered_pinners,
    publisher_keypair,
    pinner_a_keypair,
    pinner_b_keypair,
    timing,
    tier3_state,
):
    """Two pinners collect from the same slot concurrently.

    Different source accounts = independent sequence numbers = true parallelism.
    collect_pin returns the amount paid (offer_price), not pins_remaining.
    """
    # Create a slot with pin_qty=3
    pub_client = make_client()
    try:
        cid = _unique_cid()
        slot_id = await create_test_slot(
            pub_client, publisher_keypair, timing, cid=cid, pin_qty=3,
            label="cross_account_create",
        )
        tier3_state["slot_ids"].append(slot_id)
    finally:
        try:
            await pub_client.server.close()
        except Exception:
            pass

    # Both pinners collect concurrently
    async def collect_for(kp, label):
        client = make_client()
        try:
            t0 = time.perf_counter()
            tx = await client.collect_pin(
                caller=kp.public_key,
                slot_id=slot_id,
                source=kp.public_key,
                signer=kp,
            )
            amount_paid = await tx.sign_and_submit()
            elapsed = time.perf_counter() - t0
            tx_hash = tx.send_transaction_response.hash if tx.send_transaction_response else None
            timing.add(label, elapsed, tx_hash, f"amount_paid={amount_paid}")
            return amount_paid
        finally:
            try:
                await client.server.close()
            except Exception:
                pass

    async with timed_op(timing, "concurrent_collect (total)") as rec:
        results = await asyncio.gather(
            collect_for(pinner_a_keypair, "collect[pinner_a]"),
            collect_for(pinner_b_keypair, "collect[pinner_b]"),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, int)]
        failures = [r for r in results if isinstance(r, Exception)]
        rec["result"] = f"{len(successes)} ok, {len(failures)} failed"

    # Different accounts CAN conflict when modifying the same slot's state.
    # Soroban may reject one tx if both try to modify the same storage key
    # in the same ledger. If one fails, retry sequentially.
    if len(successes) == 2:
        assert all(s == 10_000_000 for s in successes)
    elif len(successes) == 1:
        # One succeeded, retry the failed one sequentially
        assert successes[0] == 10_000_000
        failed_kp = None
        for kp, r in zip([pinner_a_keypair, pinner_b_keypair], results):
            if isinstance(r, Exception):
                failed_kp = kp
                break
        assert failed_kp is not None
        label = "pinner_a" if failed_kp == pinner_a_keypair else "pinner_b"
        retry_client = make_client()
        try:
            async with timed_op(timing, f"collect[{label}] (retry)") as rec:
                tx = await retry_client.collect_pin(
                    caller=failed_kp.public_key,
                    slot_id=slot_id,
                    source=failed_kp.public_key,
                    signer=failed_kp,
                )
                amount = await tx.sign_and_submit()
                rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                rec["result"] = f"amount_paid={amount}"
            assert amount == 10_000_000
        finally:
            try:
                await retry_client.server.close()
            except Exception:
                pass
    else:
        pytest.fail(f"Expected at least 1 success: {results}")

    # Verify on-chain: pins_remaining should be 1 (3 - 2 collects)
    verify_client = make_client()
    try:
        async with timed_op(timing, "verify_slot_after_collect") as rec:
            slot = await simulate_query(verify_client, "get_slot", slot_id)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"
        assert slot.pins_remaining == 1
    finally:
        try:
            await verify_client.server.close()
        except Exception:
            pass


async def test_rapid_sequential_cycles(
    registered_pinners,
    publisher_keypair,
    pinner_a_keypair,
    timing,
    tier3_state,
):
    """Tight loop: create -> collect -> cancel -> repeat, 5 iterations.

    Each slot is cancelled after collection to free capacity
    (contract has only 10 slots total).
    """
    pub_client = make_client()
    pin_client = make_client()
    try:
        total_start = time.perf_counter()

        for i in range(5):
            cid = _unique_cid()

            # Create
            async with timed_op(timing, f"rapid_create_{i}") as rec:
                tx = await pub_client.create_pin(
                    caller=publisher_keypair.public_key,
                    cid=cid.encode("utf-8"),
                    filename=f"rapid_{i}.bin".encode("utf-8"),
                    gateway=b"https://pintheon.xyz",
                    offer_price=10_000_000,
                    pin_qty=3,
                    source=publisher_keypair.public_key,
                    signer=publisher_keypair,
                )
                slot_id = await tx.sign_and_submit()
                rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                rec["result"] = f"slot_id={slot_id}"

            # Collect
            async with timed_op(timing, f"rapid_collect_{i}") as rec:
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

            # Cancel remainder to free the slot
            async with timed_op(timing, f"rapid_cancel_{i}") as rec:
                tx = await pub_client.cancel_pin(
                    caller=publisher_keypair.public_key,
                    slot_id=slot_id,
                    source=publisher_keypair.public_key,
                    signer=publisher_keypair,
                )
                refund = await tx.sign_and_submit()
                rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
                rec["result"] = f"refund={refund}"

        total_elapsed = time.perf_counter() - total_start
        timing.add("rapid_total (5 cycles)", total_elapsed, result=f"{total_elapsed / 5:.2f}s/cycle")
    finally:
        for c in [pub_client, pin_client]:
            try:
                await c.server.close()
            except Exception:
                pass


async def test_concurrent_readonly_queries(
    testnet_reachable,
    timing,
):
    """Fire 10 concurrent simulations to measure read parallelism."""
    client = make_client()
    try:
        query_methods = [
            "pin_fee", "join_fee", "pinner_stake_amount",
            "min_pin_qty", "min_offer_price",
            "get_pinner_count",
            "pin_fee", "join_fee",  # duplicates to reach 10
            "pinner_stake_amount", "min_offer_price",
        ]

        # Sequential baseline
        async with timed_op(timing, "sequential_10_queries") as rec:
            for method in query_methods:
                await simulate_query(client, method)
            rec["result"] = f"{len(query_methods)} queries"

        seq_duration = timing.records[-1].duration_s

        # Concurrent
        async with timed_op(timing, "concurrent_10_queries") as rec:
            tasks = [simulate_query(client, m) for m in query_methods]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successes = [r for r in results if not isinstance(r, Exception)]
            rec["result"] = f"{len(successes)}/{len(query_methods)} ok"

        conc_duration = timing.records[-1].duration_s

        timing.add(
            "speedup",
            0,
            result=f"sequential={seq_duration:.2f}s, concurrent={conc_duration:.2f}s, "
                   f"ratio={seq_duration / conc_duration:.1f}x"
            if conc_duration > 0 else "n/a",
        )

        assert len(successes) == len(query_methods)
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
