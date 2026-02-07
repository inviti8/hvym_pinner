"""Tier 4 E2E tests: real IPFS + real Stellar testnet, full pipeline.

Tests the complete flow: content from fake gateway -> Kubo pin -> on-chain claim.
Requires: local Kubo daemon + Stellar testnet + funded accounts.

Run: uv run pytest -m e2e -v
"""

from __future__ import annotations

import time

import pytest

from hvym_pinner.api.data_api import DataAggregator
from hvym_pinner.api.mode import DaemonModeController
from hvym_pinner.daemon import PinnerDaemon
from hvym_pinner.models.config import DaemonMode
from hvym_pinner.models.events import PinEvent
from hvym_pinner.policy.filter import PolicyOfferFilter
from hvym_pinner.storage.sqlite import SQLiteStateStore
from tests.conftest import make_test_config
from tests.mocks import MockPoller
from tests.tier4.conftest import (
    FAKE_GATEWAY_PORT,
    PINNER_A_PUBLIC,
    create_test_slot,
    make_client,
    simulate_query,
    timed_op,
    TimingCollector,
)

pytestmark = pytest.mark.e2e


# ── Test 1: Direct pipeline (components wired manually) ──────────


async def test_e2e_direct_pipeline(
    registered_pinners,
    publisher_keypair,
    pinner_a_keypair,
    fake_gateway,
    real_executor,
    real_submitter,
    real_queries,
    timing,
    tier4_state,
):
    """Full pipeline: gateway fetch -> Kubo pin -> on-chain collect_pin.

    Wires components directly (no daemon), exercising:
      Publisher create_pin -> Executor.pin(cid, gateway) -> Submitter.submit_claim
    """
    gateway_url, cid, content = fake_gateway

    # Step 1: Publisher creates an on-chain slot with the real CID
    client = make_client()
    try:
        slot_id = await create_test_slot(
            client,
            publisher_keypair,
            timing,
            cid=cid,
            filename="e2e-test.bin",
            gateway=gateway_url,
            offer_price=10_000_000,
            pin_qty=3,
            label="e2e_create_pin",
        )
        tier4_state["slot_ids"].append(slot_id)
    finally:
        try:
            await client.server.close()
        except Exception:
            pass

    # Step 2: Executor pins from the fake gateway (real Kubo)
    async with timed_op(timing, "e2e_executor_pin") as rec:
        pin_result = await real_executor.pin(cid, gateway_url)
        rec["result"] = f"success={pin_result.success}, bytes={pin_result.bytes_pinned}"

    assert pin_result.success, f"Pin failed: {pin_result.error}"
    tier4_state["cids_pinned"].append(cid)

    # Step 3: Verify content is actually pinned on local Kubo
    async with timed_op(timing, "e2e_verify_pinned") as rec:
        is_pinned = await real_executor.verify_pinned(cid)
        rec["result"] = f"pinned={is_pinned}"

    assert is_pinned, "CID should be pinned on local Kubo"

    # Step 4: Pinner A claims on-chain via submitter
    async with timed_op(timing, "e2e_submit_claim") as rec:
        claim_result = await real_submitter.submit_claim(slot_id)
        rec["tx_hash"] = claim_result.tx_hash
        rec["result"] = f"success={claim_result.success}"

    assert claim_result.success, f"Claim failed: {claim_result.error}"
    assert claim_result.tx_hash, "Claim should have a tx hash"

    # Step 5: Verify on-chain state — pins_remaining should be decremented
    verify_client = make_client()
    try:
        async with timed_op(timing, "e2e_verify_onchain") as rec:
            slot = await simulate_query(verify_client, "get_slot", slot_id)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"

        assert slot is not None, "Slot should still exist"
        assert slot.pins_remaining == 2, f"Expected 2 pins remaining, got {slot.pins_remaining}"
    finally:
        try:
            await verify_client.server.close()
        except Exception:
            pass


# ── Test 2: Daemon event processing (production code path) ───────


async def test_e2e_daemon_event_processing(
    registered_pinners,
    publisher_keypair,
    pinner_a_keypair,
    fake_gateway,
    real_executor,
    real_submitter,
    real_queries,
    timing,
    tier4_state,
):
    """Tests the flow through PinnerDaemon._handle_pin_event() — the actual
    production code path.

    Constructs a PinnerDaemon with real components (executor, submitter,
    queries) but in-memory SQLite and a MockPoller (events injected directly).
    """
    gateway_url, cid, content = fake_gateway

    # Step 1: Publisher creates an on-chain slot with the real CID
    client = make_client()
    try:
        slot_id = await create_test_slot(
            client,
            publisher_keypair,
            timing,
            cid=cid,
            filename="e2e-daemon-test.bin",
            gateway=gateway_url,
            offer_price=10_000_000,
            pin_qty=3,
            label="daemon_create_pin",
        )
        tier4_state["slot_ids"].append(slot_id)
    finally:
        try:
            await client.server.close()
        except Exception:
            pass

    # Step 2: Build PinnerDaemon with real components, mock poller
    cfg = make_test_config()
    daemon = PinnerDaemon(cfg)

    # Swap in real + test components (pattern from MEMORY.md)
    store = SQLiteStateStore(":memory:")
    await store.initialize()

    daemon.store = store
    daemon.poller = MockPoller()
    daemon.executor = real_executor
    daemon.submitter = real_submitter
    daemon.queries = real_queries
    daemon.filter = PolicyOfferFilter(
        queries=real_queries,
        our_address=pinner_a_keypair.public_key,
        min_price=cfg.min_price,
        max_content_size=cfg.max_content_size,
    )
    daemon.mode_ctrl = DaemonModeController(store, DaemonMode.AUTO)
    daemon.data_api = DataAggregator(
        store=store,
        queries=real_queries,
        mode_ctrl=daemon.mode_ctrl,
        our_address=pinner_a_keypair.public_key,
        start_time=time.monotonic(),
    )

    # Step 3: Construct PinEvent matching the slot we just created
    event = PinEvent(
        slot_id=slot_id,
        cid=cid,
        filename="e2e-daemon-test.bin",
        gateway=gateway_url,
        offer_price=10_000_000,
        pin_qty=3,
        publisher=publisher_keypair.public_key,
        ledger_sequence=0,
    )

    # Step 4: Run _handle_pin_event — this triggers the full auto-mode flow
    async with timed_op(timing, "daemon_handle_pin_event") as rec:
        await daemon._handle_pin_event(event)
        rec["result"] = "completed"

    tier4_state["cids_pinned"].append(cid)

    # Step 5: Verify — Kubo
    async with timed_op(timing, "daemon_verify_kubo") as rec:
        is_pinned = await real_executor.verify_pinned(cid)
        rec["result"] = f"pinned={is_pinned}"

    assert is_pinned, "CID should be pinned on local Kubo after daemon flow"

    # Step 6: Verify — SQLite state
    offer = await store.get_offer(slot_id)
    assert offer is not None, "Offer should be saved in SQLite"
    assert offer.status == "claimed", f"Offer status should be 'claimed', got '{offer.status}'"

    pins = await store.get_all_pins()
    pin_cids = [p.cid for p in pins]
    assert cid in pin_cids, "Pin record should exist in SQLite"

    earnings = await store.get_earnings()
    assert earnings.claims_count >= 1, "At least one claim should be recorded"
    assert earnings.total_earned >= 10_000_000, "Earnings should include the offer price"

    # Step 7: Verify — on-chain
    verify_client = make_client()
    try:
        async with timed_op(timing, "daemon_verify_onchain") as rec:
            slot = await simulate_query(verify_client, "get_slot", slot_id)
            rec["result"] = f"pins_remaining={slot.pins_remaining}"

        assert slot.pins_remaining == 2, f"Expected 2 pins remaining, got {slot.pins_remaining}"
    finally:
        try:
            await verify_client.server.close()
        except Exception:
            pass

    await store.close()


# ── Test 3: Timing report ────────────────────────────────────────


async def test_e2e_timing_report(timing, tier4_state):
    """Lightweight summary of Tier 4 E2E operations.

    Reports on slots created and CIDs pinned during this session.
    """
    slot_count = len(tier4_state.get("slot_ids", []))
    cid_count = len(tier4_state.get("cids_pinned", []))

    timing.add(
        "session_summary",
        0,
        result=f"slots_created={slot_count}, cids_pinned={cid_count}",
    )

    # These are informational — the test always passes
    assert slot_count >= 0, "Slot count should be non-negative"
    assert cid_count >= 0, "CID count should be non-negative"
