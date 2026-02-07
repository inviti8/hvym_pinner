"""Verification scheduler - periodic verification cycles."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from hvym_pinner.hunter.flag import SorobanFlagSubmitter
from hvym_pinner.hunter.registry import PinnerRegistryCacheImpl
from hvym_pinner.hunter.verifier import KuboPinVerifier
from hvym_pinner.models.config import ScheduleConfig
from hvym_pinner.models.hunter import CycleReport, FlagRecord, TrackedPin
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


class PeriodicVerificationScheduler:
    """Runs periodic verification cycles across all tracked pins.

    Each cycle:
    1. Gets all tracked pins with status 'tracking' or 'verified'
    2. Runs verification for each (CID, pinner) pair (with concurrency limit)
    3. Updates failure counts and status
    4. Auto-flags pinners that exceed the failure threshold
    5. Records the cycle report
    """

    def __init__(
        self,
        store: SQLiteStateStore,
        verifier: KuboPinVerifier,
        registry: PinnerRegistryCacheImpl,
        flag_submitter: SorobanFlagSubmitter,
        cycle_interval: int = 3600,
        max_concurrent: int = 5,
        failure_threshold: int = 3,
        cooldown_after_flag: int = 86400,
    ) -> None:
        self._store = store
        self._verifier = verifier
        self._registry = registry
        self._flag_submitter = flag_submitter
        self._cycle_interval = cycle_interval
        self._max_concurrent = max_concurrent
        self._failure_threshold = failure_threshold
        self._cooldown_after_flag = cooldown_after_flag
        self._next_cycle: str | None = None

    def next_cycle_at(self) -> str | None:
        return self._next_cycle

    def get_schedule_config(self) -> ScheduleConfig:
        return ScheduleConfig(
            cycle_interval=self._cycle_interval,
            max_concurrent=self._max_concurrent,
            failure_threshold=self._failure_threshold,
        )

    async def run_cycle(self) -> CycleReport:
        """Run one full verification cycle."""
        started = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()

        # Get pins to check (tracking + verified, skip recently flagged)
        pins = await self._store.get_tracked_pins(["tracking", "verified", "suspect"])

        total = len(pins)
        passed = 0
        failed = 0
        flagged = 0
        skipped = 0
        errors = 0

        # Run checks with concurrency limit
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _check_one(pin: TrackedPin) -> str:
            nonlocal passed, failed, flagged, skipped, errors
            async with semaphore:
                return await self._verify_pin(pin)

        tasks = [_check_one(pin) for pin in pins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                errors += 1
            elif result == "passed":
                passed += 1
            elif result == "failed":
                failed += 1
            elif result == "flagged":
                flagged += 1
            elif result == "skipped":
                skipped += 1
            elif result == "error":
                errors += 1

        duration = int((time.monotonic() - start_time) * 1000)
        completed = datetime.now(timezone.utc).isoformat()

        report = CycleReport(
            started_at=started,
            completed_at=completed,
            total_checked=total,
            passed=passed,
            failed=failed,
            flagged=flagged,
            skipped=skipped,
            errors=errors,
            duration_ms=duration,
        )

        await self._store.save_cycle_report(report)
        log.info(
            "Verification cycle complete: %d checked, %d passed, %d failed, %d flagged in %dms",
            total, passed, failed, flagged, duration,
        )

        return report

    async def _verify_pin(self, pin: TrackedPin) -> str:
        """Verify a single tracked pin and update state."""
        now = datetime.now(timezone.utc).isoformat()

        # Skip if already flagged
        if pin.status == "flag_submitted":
            return "skipped"

        # Get pinner info for verification
        pinner_info = await self._registry.get_pinner_info(pin.pinner_address)
        if pinner_info is None:
            log.warning("No pinner info for %s, skipping", pin.pinner_address[:16])
            return "skipped"

        if not pinner_info.active:
            log.debug("Pinner %s is inactive, skipping", pin.pinner_address[:16])
            return "skipped"

        # Run verification
        try:
            result = await self._verifier.verify(
                cid=pin.cid,
                pinner_node_id=pinner_info.node_id,
                pinner_multiaddr=pinner_info.multiaddr,
            )
        except Exception as exc:
            log.error("Verification error for %s / %s: %s", pin.cid[:16], pin.pinner_address[:16], exc)
            return "error"

        # Record result
        await self._store.record_verification(pin.cid, pin.pinner_address, result)

        if result.passed:
            await self._store.update_tracked_pin(
                pin.cid, pin.pinner_address,
                status="verified",
                consecutive_failures=0,
                last_verified_at=now,
                last_checked_at=now,
            )
            return "passed"
        else:
            new_failures = pin.consecutive_failures + 1
            new_status = "suspect" if new_failures >= self._failure_threshold else pin.status
            await self._store.update_tracked_pin(
                pin.cid, pin.pinner_address,
                status=new_status,
                consecutive_failures=new_failures,
                last_checked_at=now,
            )

            # Auto-flag if threshold exceeded
            if new_failures >= self._failure_threshold and pin.status != "flag_submitted":
                if not await self._flag_submitter.has_already_flagged(pin.pinner_address):
                    flag_result = await self._flag_submitter.submit_flag(pin.pinner_address)
                    if flag_result.success:
                        await self._store.update_tracked_pin(
                            pin.cid, pin.pinner_address,
                            status="flag_submitted",
                            flagged_at=now,
                            flag_tx_hash=flag_result.tx_hash,
                        )
                        await self._store.save_flag(FlagRecord(
                            pinner_address=pin.pinner_address,
                            tx_hash=flag_result.tx_hash or "",
                            flag_count_after=flag_result.flag_count,
                            bounty_earned=flag_result.bounty_earned,
                        ))
                        return "flagged"

            return "failed"
