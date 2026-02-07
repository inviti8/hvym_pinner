"""CID Hunter orchestrator - ties verification subsystems together."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from stellar_sdk import Keypair

from hvym_pinner.hunter.flag import SorobanFlagSubmitter
from hvym_pinner.hunter.registry import PinnerRegistryCacheImpl
from hvym_pinner.hunter.scheduler import PeriodicVerificationScheduler
from hvym_pinner.hunter.verifier import KuboPinVerifier
from hvym_pinner.models.config import HunterConfig
from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent
from hvym_pinner.models.hunter import (
    CycleReport,
    FlagRecord,
    FlagResult,
    HunterSummary,
    TrackedPin,
    VerificationResult,
)
from hvym_pinner.stellar.queries import ContractQueries
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


class CIDHunterOrchestrator:
    """Orchestrates CID verification and dispute submission.

    Ingests PIN/PINNED/UNPIN events to build a registry of tracked
    (CID, pinner) pairs, then periodically verifies pinners are actually
    serving the content. Automatically flags non-compliant pinners.
    """

    def __init__(
        self,
        store: SQLiteStateStore,
        queries: ContractQueries,
        config: HunterConfig,
        our_address: str,
        contract_id: str,
        rpc_url: str,
        network_passphrase: str,
        keypair: Keypair,
        kubo_rpc_url: str = "http://127.0.0.1:5001",
    ) -> None:
        self._store = store
        self._queries = queries
        self._config = config
        self._our_address = our_address
        self._running = False
        self._scheduler_task: asyncio.Task | None = None

        # Build sub-components
        self._verifier = KuboPinVerifier(
            kubo_rpc_url=kubo_rpc_url,
            check_timeout=config.check_timeout,
            methods=config.verification_methods,
        )
        self._registry = PinnerRegistryCacheImpl(
            store=store,
            queries=queries,
            ttl_seconds=config.pinner_cache_ttl,
        )
        self._flag_submitter = SorobanFlagSubmitter(
            contract_id=contract_id,
            rpc_url=rpc_url,
            network_passphrase=network_passphrase,
            keypair=keypair,
            store=store,
        )
        self._scheduler = PeriodicVerificationScheduler(
            store=store,
            verifier=self._verifier,
            registry=self._registry,
            flag_submitter=self._flag_submitter,
            cycle_interval=config.cycle_interval,
            max_concurrent=config.max_concurrent_checks,
            failure_threshold=config.failure_threshold,
            cooldown_after_flag=config.cooldown_after_flag,
        )

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start the periodic verification scheduler."""
        if not self._config.enabled:
            log.info("CID Hunter is disabled")
            return

        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        log.info(
            "CID Hunter started (cycle_interval=%ds, failure_threshold=%d)",
            self._config.cycle_interval,
            self._config.failure_threshold,
        )

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        log.info("CID Hunter stopped")

    async def _scheduler_loop(self) -> None:
        """Background loop that runs verification cycles periodically."""
        while self._running:
            try:
                await self._scheduler.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Verification cycle error: %s", exc, exc_info=True)

            try:
                await asyncio.sleep(self._config.cycle_interval)
            except asyncio.CancelledError:
                break

    # ── Event ingestion ───────────────────────────────────

    async def on_pin_event(self, event: PinEvent) -> None:
        """Handle a PIN event. If publisher is us, start tracking this CID."""
        if event.publisher != self._our_address:
            return

        cid_hash = hashlib.sha256(event.cid.encode("utf-8")).hexdigest()
        await self._store.save_tracked_cid(
            cid=event.cid,
            cid_hash=cid_hash,
            slot_id=event.slot_id,
            publisher=event.publisher,
            gateway=event.gateway,
            pin_qty=event.pin_qty,
        )
        log.info("Tracking CID %s (slot %d)", event.cid[:20], event.slot_id)

    async def on_pinned_event(self, event: PinnedEvent) -> None:
        """Handle a PINNED event. Register the pinner for verification if CID is ours.

        Note: PINNED events only have cid_hash, not the full CID.
        We match by looking up our tracked_cids table.
        """
        # We need to match cid_hash to our tracked CIDs
        # The store has tracked_cids with cid_hash
        # For now, match by slot_id since that's also available
        # This is simpler and avoids hash format mismatches
        pinner_info = await self._registry.get_pinner_info(event.pinner)
        if pinner_info is None:
            log.debug("No pinner info for %s, skipping PINNED tracking", event.pinner[:16])
            return

        # Check if we're tracking any CID in this slot
        cid = await self._store.get_tracked_cid_by_slot(event.slot_id)
        if cid is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        pin = TrackedPin(
            cid=cid,
            cid_hash=event.cid_hash,
            pinner_address=event.pinner,
            pinner_node_id=pinner_info.node_id,
            pinner_multiaddr=pinner_info.multiaddr,
            slot_id=event.slot_id,
            claimed_at=now,
        )
        await self._store.save_tracked_pin(pin)
        log.info(
            "Tracking pinner %s for CID %s (slot %d)",
            event.pinner[:16], cid[:20], event.slot_id,
        )

    async def on_unpin_event(self, event: UnpinEvent) -> None:
        """Handle an UNPIN event. Stop tracking CIDs from freed slots."""
        # Mark tracked pins for this slot as no longer needing verification
        pins = await self._store.get_tracked_pins()
        for pin in pins:
            if pin.slot_id == event.slot_id and pin.status not in ("flag_submitted",):
                await self._store.update_tracked_pin(
                    pin.cid, pin.pinner_address, status="slot_freed",
                )
        log.debug("Stopped tracking slot %d (UNPIN)", event.slot_id)

    # ── Manual operations ─────────────────────────────────

    async def verify_now(
        self, cid: str | None = None, pinner_address: str | None = None
    ) -> list[VerificationResult]:
        """Trigger immediate verification of specific CID/pinner or all tracked."""
        pins = await self._store.get_tracked_pins(["tracking", "verified", "suspect"])

        if cid:
            pins = [p for p in pins if p.cid == cid]
        if pinner_address:
            pins = [p for p in pins if p.pinner_address == pinner_address]

        results = []
        for pin in pins:
            pinner_info = await self._registry.get_pinner_info(pin.pinner_address)
            if pinner_info is None:
                continue
            result = await self._verifier.verify(
                cid=pin.cid,
                pinner_node_id=pinner_info.node_id,
                pinner_multiaddr=pinner_info.multiaddr,
            )
            await self._store.record_verification(pin.cid, pin.pinner_address, result)
            results.append(result)

        return results

    async def flag_now(self, pinner_address: str) -> FlagResult:
        """Manually flag a pinner (bypass failure threshold)."""
        return await self._flag_submitter.submit_flag(pinner_address)

    # ── State queries (for Data API) ──────────────────────

    async def get_tracked_pins(self) -> list[TrackedPin]:
        return await self._store.get_tracked_pins()

    async def get_suspects(self) -> list[TrackedPin]:
        return await self._store.get_tracked_pins(["suspect"])

    async def get_flag_history(self) -> list[FlagRecord]:
        return await self._store.get_flag_history()

    async def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        return await self._store.get_cycle_history(limit)

    async def get_hunter_summary(self) -> HunterSummary:
        all_pins = await self._store.get_tracked_pins()
        flags = await self._store.get_flag_history()
        cycles = await self._store.get_cycle_history(1)

        verified = len([p for p in all_pins if p.status == "verified"])
        suspect = len([p for p in all_pins if p.status == "suspect"])
        flagged = len([p for p in all_pins if p.status == "flag_submitted"])

        total_bounties = sum(f.bounty_earned or 0 for f in flags)

        return HunterSummary(
            enabled=self._config.enabled,
            total_tracked_pins=len(all_pins),
            verified_count=verified,
            suspect_count=suspect,
            flagged_count=flagged,
            total_checks_lifetime=sum(p.total_checks for p in all_pins),
            total_flags_lifetime=len(flags),
            bounties_earned_stroops=total_bounties,
            bounties_earned_xlm=f"{total_bounties / 10_000_000:.7f} XLM",
            last_cycle_at=cycles[0].completed_at if cycles else None,
            next_cycle_at=self._scheduler.next_cycle_at(),
            cycle_interval_seconds=self._config.cycle_interval,
        )
