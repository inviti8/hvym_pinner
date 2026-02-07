"""Main daemon loop - wires all components together."""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from stellar_sdk import Keypair

from hvym_pinner.api.data_api import DataAggregator
from hvym_pinner.api.mode import DaemonModeController
from hvym_pinner.hunter.orchestrator import CIDHunterOrchestrator
from hvym_pinner.ipfs.executor import KuboPinExecutor
from hvym_pinner.models.config import DaemonConfig, DaemonMode
from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent
from hvym_pinner.policy.filter import PolicyOfferFilter
from hvym_pinner.stellar.poller import SorobanEventPoller
from hvym_pinner.stellar.queries import ContractQueries
from hvym_pinner.stellar.submitter import SorobanClaimSubmitter
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)

NETWORK_PASSPHRASES = {
    "testnet": "Test SDF Network ; September 2015",
    "mainnet": "Public Global Stellar Network ; September 2015",
}


class PinnerDaemon:
    """Autonomous IPFS pinning daemon.

    Orchestrates event polling, offer filtering, IPFS pinning,
    and claim submission in both auto and approve modes.
    """

    def __init__(self, cfg: DaemonConfig) -> None:
        self._cfg = cfg
        self._running = False
        self._start_time = time.monotonic()

        passphrase = cfg.network_passphrase or NETWORK_PASSPHRASES.get(cfg.network, "")
        keypair = Keypair.from_secret(cfg.keypair_secret)
        public_key = keypair.public_key

        # Core components
        self.store = SQLiteStateStore(cfg.db_path)
        self.poller = SorobanEventPoller(cfg.rpc_url, cfg.contract_id)
        self.queries = ContractQueries(cfg.contract_id, cfg.rpc_url, passphrase)
        self.filter = PolicyOfferFilter(
            self.queries, public_key, cfg.min_price, cfg.max_content_size,
        )
        self.executor = KuboPinExecutor(
            cfg.kubo_rpc_url, cfg.pin_timeout, cfg.max_content_size, cfg.fetch_retries,
        )
        self.submitter = SorobanClaimSubmitter(
            cfg.contract_id, cfg.rpc_url, passphrase, keypair,
        )
        self.mode_ctrl = DaemonModeController(self.store, cfg.mode)
        self.data_api: DataAggregator = None  # type: ignore[assignment]  # set after hunter init

        # CID Hunter (optional - disabled by default)
        self.hunter: CIDHunterOrchestrator | None = None
        if cfg.hunter.enabled:
            self.hunter = CIDHunterOrchestrator(
                store=self.store,
                queries=self.queries,
                config=cfg.hunter,
                our_address=public_key,
                contract_id=cfg.contract_id,
                rpc_url=cfg.rpc_url,
                network_passphrase=passphrase,
                keypair=keypair,
                kubo_rpc_url=cfg.kubo_rpc_url,
            )

        # Data API (needs hunter ref, so built after hunter init)
        self.data_api = DataAggregator(
            self.store, self.queries, self.mode_ctrl, public_key, self._start_time,
            hunter=self.hunter,
        )

        self._public_key = public_key

    async def start(self) -> None:
        """Initialize components and run the main loop."""
        log.info("Starting hvym_pinner daemon")
        log.info("  Mode: %s", self._cfg.mode.value)
        log.info("  Address: %s", self._public_key)
        log.info("  Contract: %s", self._cfg.contract_id)
        log.info("  RPC: %s", self._cfg.rpc_url)
        log.info("  Kubo: %s", self._cfg.kubo_rpc_url)

        # Initialize state store
        await self.store.initialize()

        # Restore cursor from last run
        saved_ledger = await self.store.get_cursor()
        if saved_ledger:
            # Convert ledger to cursor string format
            cursor_str = f"{saved_ledger}-0"
            self.poller.set_cursor(cursor_str)
            log.info("Restored cursor: ledger %d", saved_ledger)

        # Restore mode from persisted config
        daemon_cfg = await self.store.get_daemon_config()
        try:
            self.mode_ctrl.set_mode(DaemonMode(daemon_cfg.mode))
        except ValueError:
            pass

        self._running = True
        await self.store.log_activity("daemon_started", "Daemon started")

        # Start CID Hunter if enabled
        if self.hunter:
            await self.hunter.start()

        try:
            await self._main_loop()
        finally:
            if self.hunter:
                await self.hunter.stop()
            await self.store.log_activity("daemon_stopped", "Daemon stopped")
            await self.store.close()
            log.info("Daemon shut down cleanly")

    async def stop(self) -> None:
        """Signal the daemon to stop gracefully."""
        log.info("Stop requested")
        self._running = False

    async def _main_loop(self) -> None:
        """The core polling and processing loop."""
        while self._running:
            try:
                # 1. Poll for new events
                events = await self.poller.poll()

                for event in events:
                    if isinstance(event, PinEvent):
                        await self._handle_pin_event(event)
                    elif isinstance(event, PinnedEvent):
                        await self._handle_pinned_event(event)
                    elif isinstance(event, UnpinEvent):
                        await self._handle_unpin_event(event)

                # 2. Process approved offers (from frontend, in approve mode)
                approved = await self.store.get_offers_by_status("approved")
                for offer in approved:
                    pin_event = PinEvent(
                        slot_id=offer.slot_id,
                        cid=offer.cid,
                        gateway=offer.gateway,
                        offer_price=offer.offer_price,
                        pin_qty=offer.pin_qty,
                        publisher=offer.publisher,
                        ledger_sequence=offer.ledger_sequence,
                    )
                    await self._execute_pin_and_claim(pin_event)

                # 3. Save cursor
                ledger = await self.poller.get_cursor()
                if ledger:
                    await self.store.set_cursor(ledger)

                # 4. Wait before next poll
                await asyncio.sleep(self._cfg.poll_interval)

            except asyncio.CancelledError:
                log.info("Main loop cancelled")
                break
            except Exception as exc:
                log.error("Main loop error: %s", exc, exc_info=True)
                await self.store.log_activity("error", str(exc))
                await asyncio.sleep(self._cfg.error_backoff)

    async def _handle_pin_event(self, event: PinEvent) -> None:
        """Process a new PIN event (offer from a publisher)."""
        log.info(
            "PIN event: slot=%d cid=%s price=%d publisher=%s",
            event.slot_id, event.cid[:20], event.offer_price, event.publisher[:16],
        )

        # Save to offers table
        await self.store.save_offer(event)
        await self.store.log_activity(
            "offer_seen",
            f"PIN offer: slot {event.slot_id}, {event.offer_price} stroops",
            slot_id=event.slot_id,
            cid=event.cid,
        )

        # Filter
        result = await self.filter.evaluate(event)
        if not result.accepted:
            await self.store.update_offer_status(
                event.slot_id, "rejected", reject_reason=result.reason,
            )
            await self.store.log_activity(
                "offer_rejected",
                f"Rejected: {result.reason}",
                slot_id=event.slot_id,
            )
            return

        # Forward to CID Hunter for tracking
        if self.hunter:
            await self.hunter.on_pin_event(event)

        # Mode branch
        mode = self.mode_ctrl.get_mode()
        if mode == DaemonMode.APPROVE:
            await self.store.update_offer_status(event.slot_id, "awaiting_approval")
            await self.store.log_activity(
                "offer_queued",
                f"Queued for approval: slot {event.slot_id}",
                slot_id=event.slot_id,
                cid=event.cid,
            )
            return

        # Auto mode: pin and claim immediately
        await self._execute_pin_and_claim(event)

    async def _handle_pinned_event(self, event: PinnedEvent) -> None:
        """Process a PINNED event (another pinner claimed a slot)."""
        log.info(
            "PINNED event: slot=%d pinner=%s remaining=%d",
            event.slot_id, event.pinner[:16], event.pins_remaining,
        )
        await self.store.log_activity(
            "slot_claimed",
            f"Slot {event.slot_id} claimed by {event.pinner[:16]}..., "
            f"{event.pins_remaining} remaining",
            slot_id=event.slot_id,
            amount=event.amount,
        )

        # Forward to CID Hunter for pinner tracking
        if self.hunter:
            await self.hunter.on_pinned_event(event)

        # Update our offer record if we have one
        offer = await self.store.get_offer(event.slot_id)
        if offer and event.pins_remaining <= 0:
            await self.store.update_offer_status(event.slot_id, "filled")

    async def _handle_unpin_event(self, event: UnpinEvent) -> None:
        """Process an UNPIN event (slot freed)."""
        log.info("UNPIN event: slot=%d", event.slot_id)

        # Forward to CID Hunter to stop tracking freed slots
        if self.hunter:
            await self.hunter.on_unpin_event(event)

        await self.store.update_offer_status(event.slot_id, "expired")
        await self.store.log_activity(
            "offer_expired",
            f"Slot {event.slot_id} freed",
            slot_id=event.slot_id,
        )

    async def _execute_pin_and_claim(self, event: PinEvent) -> None:
        """Pin content to Kubo, then submit collect_pin() on-chain."""
        await self.store.update_offer_status(event.slot_id, "pinning")
        await self.store.log_activity(
            "pin_started",
            f"Pinning CID: {event.cid[:30]}",
            slot_id=event.slot_id,
            cid=event.cid,
        )

        # 1. Pin the content
        pin_result = await self.executor.pin(event.cid, event.gateway)
        if not pin_result.success:
            await self.store.update_offer_status(
                event.slot_id, "pin_failed", reject_reason=pin_result.error,
            )
            await self.store.log_activity(
                "pin_failed",
                f"Pin failed: {pin_result.error}",
                slot_id=event.slot_id,
                cid=event.cid,
            )
            return

        # Save pin record
        await self.store.save_pin(event.cid, event.slot_id, pin_result.bytes_pinned)
        await self.store.log_activity(
            "pin_success",
            f"Pinned {event.cid[:20]} ({pin_result.bytes_pinned or '?'} bytes)",
            slot_id=event.slot_id,
            cid=event.cid,
        )

        # 2. Submit collect_pin()
        await self.store.update_offer_status(event.slot_id, "claiming")
        claim_result = await self.submitter.submit_claim(event.slot_id)

        if claim_result.success:
            # Record the amount earned (from the offer price)
            claim_result.amount_earned = event.offer_price
            await self.store.save_claim(claim_result)
            await self.store.update_offer_status(event.slot_id, "claimed")
            await self.store.log_activity(
                "claim_success",
                f"Claimed slot {event.slot_id}: +{event.offer_price} stroops",
                slot_id=event.slot_id,
                cid=event.cid,
                amount=event.offer_price,
            )
        else:
            await self.store.update_offer_status(
                event.slot_id, "claim_failed", reject_reason=claim_result.error,
            )
            await self.store.log_activity(
                "claim_failed",
                f"Claim failed: {claim_result.error}",
                slot_id=event.slot_id,
                cid=event.cid,
            )


async def run_daemon(cfg: DaemonConfig) -> None:
    """Entry point for running the daemon."""
    daemon = PinnerDaemon(cfg)

    loop = asyncio.get_event_loop()

    def _signal_handler():
        asyncio.ensure_future(daemon.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    await daemon.start()
