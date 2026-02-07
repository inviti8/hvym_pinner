"""StateStore protocol - persists daemon state for crash recovery and frontend data."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.events import PinEvent
from hvym_pinner.models.records import (
    ActivityRecord,
    ClaimResult,
    DaemonConfigRecord,
    EarningsSummary,
    OfferRecord,
    PinRecord,
)
from hvym_pinner.models.hunter import (
    CycleReport,
    FlagRecord,
    PinnerInfo,
    TrackedPin,
    VerificationResult,
)


class StateStore(Protocol):
    """Persists daemon state for crash recovery and frontend data."""

    # ── Lifecycle ──────────────────────────────────────────

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        ...

    async def close(self) -> None:
        """Close the database connection."""
        ...

    # ── Cursor ─────────────────────────────────────────────

    async def get_cursor(self) -> int | None:
        ...

    async def set_cursor(self, ledger: int) -> None:
        ...

    # ── Daemon config ──────────────────────────────────────

    async def get_daemon_config(self) -> DaemonConfigRecord:
        ...

    async def set_daemon_config(
        self,
        mode: str | None = None,
        min_price: int | None = None,
        max_content_size: int | None = None,
    ) -> None:
        ...

    # ── Offers ─────────────────────────────────────────────

    async def save_offer(self, event: PinEvent, status: str = "pending") -> None:
        ...

    async def get_offer(self, slot_id: int) -> OfferRecord | None:
        ...

    async def update_offer_status(
        self, slot_id: int, status: str, reject_reason: str | None = None
    ) -> None:
        ...

    async def get_offers_by_status(self, status: str) -> list[OfferRecord]:
        ...

    async def get_approval_queue(self) -> list[OfferRecord]:
        ...

    async def get_all_offers(self) -> list[OfferRecord]:
        ...

    # ── Claims & earnings ──────────────────────────────────

    async def save_claim(self, claim: ClaimResult) -> None:
        ...

    async def get_earnings(self, since: str | None = None) -> EarningsSummary:
        ...

    # ── Pins ───────────────────────────────────────────────

    async def save_pin(self, cid: str, slot_id: int, bytes_pinned: int | None) -> None:
        ...

    async def is_cid_pinned(self, cid: str) -> bool:
        ...

    async def get_all_pins(self) -> list[PinRecord]:
        ...

    # ── Activity log ───────────────────────────────────────

    async def log_activity(
        self,
        event_type: str,
        message: str,
        slot_id: int | None = None,
        cid: str | None = None,
        amount: int | None = None,
    ) -> None:
        ...

    async def get_recent_activity(self, limit: int = 50) -> list[ActivityRecord]:
        ...

    # ── Hunter: tracked pins ───────────────────────────────

    async def save_tracked_cid(
        self, cid: str, cid_hash: str, slot_id: int, publisher: str,
        gateway: str | None, pin_qty: int,
    ) -> None:
        ...

    async def save_tracked_pin(self, pin: TrackedPin) -> None:
        ...

    async def get_tracked_pins(
        self, status: list[str] | None = None
    ) -> list[TrackedPin]:
        ...

    async def update_tracked_pin(
        self,
        cid: str,
        pinner_address: str,
        status: str | None = None,
        consecutive_failures: int | None = None,
        last_verified_at: str | None = None,
        last_checked_at: str | None = None,
        flagged_at: str | None = None,
        flag_tx_hash: str | None = None,
    ) -> None:
        ...

    # ── Hunter: verification log ───────────────────────────

    async def record_verification(
        self, cid: str, pinner_address: str, result: VerificationResult,
    ) -> None:
        ...

    async def save_cycle_report(self, report: CycleReport) -> None:
        ...

    async def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        ...

    # ── Hunter: flags ──────────────────────────────────────

    async def save_flag(self, record: FlagRecord) -> None:
        ...

    async def get_flag_history(self) -> list[FlagRecord]:
        ...

    # ── Hunter: pinner cache ───────────────────────────────

    async def get_cached_pinner(self, address: str) -> PinnerInfo | None:
        ...

    async def cache_pinner(self, info: PinnerInfo) -> None:
        ...
