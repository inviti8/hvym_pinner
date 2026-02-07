"""Tests 19-20: CID Hunter track → verify → suspect → flag lifecycle."""

from __future__ import annotations

import hashlib

import pytest

from hvym_pinner.api.data_api import DataAggregator
from hvym_pinner.api.mode import DaemonModeController
from hvym_pinner.daemon import PinnerDaemon
from hvym_pinner.hunter.orchestrator import CIDHunterOrchestrator
from hvym_pinner.hunter.registry import PinnerRegistryCacheImpl
from hvym_pinner.hunter.scheduler import PeriodicVerificationScheduler
from hvym_pinner.models.config import DaemonMode, HunterConfig
from hvym_pinner.models.hunter import PinnerInfo
from hvym_pinner.policy.filter import PolicyOfferFilter
from hvym_pinner.stellar.queries import PinnerData
from hvym_pinner.storage.sqlite import SQLiteStateStore

from tests.conftest import TEST_PUBLIC, TEST_SECRET, make_test_config
from tests.factories import make_pin_event, make_pinned_event, make_unpin_event
from tests.mocks import (
    MockExecutor,
    MockFlagSubmitter,
    MockPoller,
    MockQueries,
    MockSubmitter,
    MockVerifier,
)

OTHER_PINNER = "GOTHER_PINNER_ADDRESS_FOR_HUNTING_TESTSXXXXXXXXXXXXXXXXXX"
OTHER_NODE_ID = "12D3KooWTestNodeID"
OTHER_MULTIADDR = "/ip4/1.2.3.4/tcp/4001/p2p/12D3KooWTestNodeID"


@pytest.fixture
def hunter_config():
    return HunterConfig(
        enabled=True,
        cycle_interval=10,
        check_timeout=5,
        max_concurrent_checks=3,
        failure_threshold=3,
        cooldown_after_flag=60,
        pinner_cache_ttl=300,
    )


@pytest.fixture
async def daemon_with_hunter(
    hunter_config, store, mock_poller, mock_executor,
    mock_submitter, mock_verifier, mock_flag_submitter,
):
    """Daemon with CID Hunter enabled, using mock verifier and flag submitter."""
    # MockQueries that knows about the other pinner
    queries = MockQueries(
        wallet_balance=10_000_000,
        pinner_data=PinnerData(
            address=OTHER_PINNER,
            node_id=OTHER_NODE_ID,
            multiaddr=OTHER_MULTIADDR,
            active=True,
            flags=0,
            min_price=100,
            pins_completed=5,
            staked=1_000_000,
            joined_at=0,
        ),
    )

    cfg = make_test_config(hunter=hunter_config)
    d = PinnerDaemon(cfg)
    d.store = store
    d.poller = mock_poller
    d.executor = mock_executor
    d.submitter = mock_submitter
    d.queries = queries
    d.filter = PolicyOfferFilter(
        queries=queries,
        our_address=TEST_PUBLIC,
        min_price=cfg.min_price,
        max_content_size=cfg.max_content_size,
    )
    d.mode_ctrl = DaemonModeController(store, DaemonMode.AUTO)

    # Build a real orchestrator with mock sub-components
    registry = PinnerRegistryCacheImpl(
        store=store, queries=queries, ttl_seconds=300,
    )
    scheduler = PeriodicVerificationScheduler(
        store=store,
        verifier=mock_verifier,
        registry=registry,
        flag_submitter=mock_flag_submitter,
        cycle_interval=10,
        max_concurrent=3,
        failure_threshold=3,
        cooldown_after_flag=60,
    )

    # Create a lightweight orchestrator-like object by re-wiring internals
    hunter = CIDHunterOrchestrator(
        store=store,
        queries=queries,
        config=hunter_config,
        our_address=TEST_PUBLIC,
        contract_id=cfg.contract_id,
        rpc_url=cfg.rpc_url,
        network_passphrase=cfg.network_passphrase,
        keypair=__import__("stellar_sdk").Keypair.from_secret(TEST_SECRET),
        kubo_rpc_url=cfg.kubo_rpc_url,
    )
    # Replace the internal components with our mocks
    hunter._verifier = mock_verifier
    hunter._flag_submitter = mock_flag_submitter
    hunter._registry = registry
    hunter._scheduler = scheduler

    d.hunter = hunter
    d.data_api = DataAggregator(
        store=store,
        queries=queries,
        mode_ctrl=d.mode_ctrl,
        our_address=TEST_PUBLIC,
        start_time=0.0,
        hunter=hunter,
    )
    return d, scheduler


# ── Test 19: Hunter track → verify → suspect → flag lifecycle ─────


async def test_hunter_track_verify_flag_lifecycle(
    daemon_with_hunter, store, mock_verifier, mock_flag_submitter,
):
    """
    PIN event (our CID) → PINNED event (other pinner) → tracked pin →
    3 failed verification cycles → suspect → auto-flag → flag_submitted.
    """
    daemon, scheduler = daemon_with_hunter

    # 1. PIN event from us (publisher = our address)
    pin_event = make_pin_event(
        slot_id=19,
        offer_price=1_000_000,
        publisher=TEST_PUBLIC,
    )
    await daemon._handle_pin_event(pin_event)

    # 2. PINNED event from another pinner
    pinned = make_pinned_event(
        slot_id=19,
        cid=pin_event.cid,
        pinner=OTHER_PINNER,
        pins_remaining=2,
    )
    await daemon.hunter.on_pinned_event(pinned)

    # Should now have a tracked pin
    tracked = await store.get_tracked_pins()
    assert len(tracked) == 1
    assert tracked[0].pinner_address == OTHER_PINNER
    assert tracked[0].status == "tracking"

    # 3. Run 3 failed verification cycles
    mock_verifier.passed = False

    for i in range(3):
        await scheduler.run_cycle()

    # After 3 failures, should be flagged
    tracked = await store.get_tracked_pins()
    assert len(tracked) == 1
    pin = tracked[0]
    assert pin.status == "flag_submitted"
    assert pin.flag_tx_hash is not None

    # Flag was submitted
    assert len(mock_flag_submitter.flag_calls) == 1
    assert mock_flag_submitter.flag_calls[0] == OTHER_PINNER

    # Flag record saved
    flags = await store.get_flag_history()
    assert len(flags) == 1
    assert flags[0].pinner_address == OTHER_PINNER


# ── Test 20: UNPIN stops tracking ─────────────────────────────────


async def test_hunter_unpin_stops_tracking(
    daemon_with_hunter, store, mock_verifier,
):
    """PIN → PINNED → tracked → UNPIN → slot_freed, skipped in cycles."""
    daemon, scheduler = daemon_with_hunter

    pin_event = make_pin_event(
        slot_id=20,
        offer_price=1_000_000,
        publisher=TEST_PUBLIC,
    )
    await daemon._handle_pin_event(pin_event)

    pinned = make_pinned_event(
        slot_id=20,
        cid=pin_event.cid,
        pinner=OTHER_PINNER,
        pins_remaining=2,
    )
    await daemon.hunter.on_pinned_event(pinned)

    # Tracked
    tracked = await store.get_tracked_pins()
    assert len(tracked) == 1
    assert tracked[0].status == "tracking"

    # UNPIN
    unpin = make_unpin_event(slot_id=20, cid=pin_event.cid)
    await daemon.hunter.on_unpin_event(unpin)

    tracked = await store.get_tracked_pins()
    assert len(tracked) == 1
    assert tracked[0].status == "slot_freed"

    # Verification cycle should skip freed pins
    mock_verifier.passed = False
    report = await scheduler.run_cycle()
    # No checks should have been performed (only tracking/verified/suspect are checked)
    assert mock_verifier.verify_calls == []
