"""CID Hunter protocols - verification, scheduling, flagging, registry cache."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent
from hvym_pinner.models.hunter import (
    CycleReport,
    FlagRecord,
    FlagResult,
    HunterSummary,
    PinnerInfo,
    TrackedPin,
    VerificationResult,
)
from hvym_pinner.models.config import ScheduleConfig


class PinVerifier(Protocol):
    """Verifies a pinner is actually serving a CID on the IPFS network."""

    async def verify(
        self, cid: str, pinner_node_id: str, pinner_multiaddr: str
    ) -> VerificationResult:
        """Run full verification pipeline against a single (CID, pinner) pair."""
        ...


class VerificationScheduler(Protocol):
    """Schedules periodic verification of tracked pins."""

    async def run_cycle(self) -> CycleReport:
        """Run one full verification cycle across all tracked pins."""
        ...

    def next_cycle_at(self) -> str | None:
        """ISO 8601 timestamp of next scheduled cycle. None if not running."""
        ...

    def get_schedule_config(self) -> ScheduleConfig:
        """Current scheduling parameters."""
        ...


class FlagSubmitter(Protocol):
    """Submits flag_pinner() transactions to the contract."""

    async def submit_flag(self, pinner_address: str) -> FlagResult:
        """Build, sign, and submit flag_pinner() transaction."""
        ...

    async def has_already_flagged(self, pinner_address: str) -> bool:
        """Check if we've already flagged this pinner."""
        ...


class PinnerRegistryCache(Protocol):
    """Local cache of on-chain pinner registry data for verification."""

    async def get_pinner_info(self, address: str) -> PinnerInfo | None:
        """Get pinner IPFS node details. Fetches from chain if not cached."""
        ...

    async def refresh(self, address: str) -> PinnerInfo | None:
        """Force refresh from chain."""
        ...


class CIDHunter(Protocol):
    """Orchestrates CID verification and dispute submission."""

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Start the verification scheduler."""
        ...

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        ...

    # ── Event ingestion ─────────────────────────────────────

    async def on_pin_event(self, event: PinEvent) -> None:
        """Handle a PIN event. If publisher is us, start tracking this CID."""
        ...

    async def on_pinned_event(self, event: PinnedEvent) -> None:
        """Handle a PINNED event. Register the pinner for verification if CID is ours."""
        ...

    async def on_unpin_event(self, event: UnpinEvent) -> None:
        """Handle an UNPIN event. Stop tracking CIDs from freed slots."""
        ...

    # ── Manual operations ───────────────────────────────────

    async def verify_now(
        self, cid: str | None = None, pinner_address: str | None = None
    ) -> list[VerificationResult]:
        """Trigger immediate verification."""
        ...

    async def flag_now(self, pinner_address: str) -> FlagResult:
        """Manually flag a pinner (bypass failure threshold)."""
        ...

    # ── State queries (for Data API) ────────────────────────

    async def get_tracked_pins(self) -> list[TrackedPin]:
        ...

    async def get_suspects(self) -> list[TrackedPin]:
        ...

    async def get_flag_history(self) -> list[FlagRecord]:
        ...

    async def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        ...

    async def get_hunter_summary(self) -> HunterSummary:
        ...
