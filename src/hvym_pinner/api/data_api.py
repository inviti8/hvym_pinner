"""Data API aggregator - builds frontend snapshots from component state."""

from __future__ import annotations

import logging
import time

from hvym_pinner.api.mode import DaemonModeController
from hvym_pinner.models.config import DaemonMode
from hvym_pinner.models.records import ActionResult
from hvym_pinner.models.snapshots import (
    ActivityEntry,
    DashboardSnapshot,
    EarningsSnapshot,
    OfferSnapshot,
    PinSnapshot,
    WalletSnapshot,
)
from hvym_pinner.stellar.queries import ContractQueries, STROOPS_PER_XLM
from hvym_pinner.policy.filter import ESTIMATED_TX_FEE
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


def _xlm_str(stroops: int) -> str:
    """Format stroops as human-readable XLM string."""
    xlm = stroops / STROOPS_PER_XLM
    return f"{xlm:.7f} XLM"


def _offer_to_snapshot(offer) -> OfferSnapshot:
    """Convert an OfferRecord to an OfferSnapshot."""
    return OfferSnapshot(
        slot_id=offer.slot_id,
        cid=offer.cid,
        gateway=offer.gateway,
        offer_price=offer.offer_price,
        offer_price_xlm=_xlm_str(offer.offer_price),
        pin_qty=offer.pin_qty,
        pins_remaining=offer.pins_remaining,
        publisher=offer.publisher,
        status=offer.status,
        net_profit=offer.net_profit or 0,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


class DataAggregator:
    """Builds JSON-serializable snapshots from daemon component state.

    This is the sole interface between the daemon backend and any UI client.
    """

    def __init__(
        self,
        store: SQLiteStateStore,
        queries: ContractQueries,
        mode_ctrl: DaemonModeController,
        our_address: str,
        start_time: float | None = None,
    ) -> None:
        self._store = store
        self._queries = queries
        self._mode_ctrl = mode_ctrl
        self._our_address = our_address
        self._start_time = start_time or time.monotonic()

    # ── Snapshots ──────────────────────────────────────────

    async def get_dashboard(self) -> DashboardSnapshot:
        """Build the full dashboard snapshot."""
        uptime = int(time.monotonic() - self._start_time)

        # Fetch data in parallel-ish (all from local SQLite, fast)
        wallet = await self.get_wallet()
        earnings = await self.get_earnings()
        all_offers = await self._store.get_all_offers()
        pins = await self._store.get_all_pins()
        activity = await self._store.get_recent_activity(20)
        queue = await self._store.get_approval_queue()
        earnings_data = await self._store.get_earnings()

        return DashboardSnapshot(
            mode=self._mode_ctrl.get_mode().value,
            pinner_address=self._our_address,
            node_id="",  # Filled by Kubo check if available
            uptime_seconds=uptime,
            stellar_connected=True,  # Simplified; real check in daemon loop
            stellar_latest_ledger=0,
            kubo_connected=True,
            kubo_peer_count=0,
            wallet=wallet,
            offers_seen=len(all_offers),
            offers_accepted=len([o for o in all_offers if o.status == "accepted"]),
            offers_rejected=len([o for o in all_offers if o.status == "rejected"]),
            offers_awaiting_approval=len(queue),
            pins_active=len(pins),
            claims_completed=earnings_data.claims_count,
            earnings=earnings,
            approval_queue=[_offer_to_snapshot(o) for o in queue],
            recent_activity=[
                ActivityEntry(
                    timestamp=a.created_at,
                    event_type=a.event_type,
                    slot_id=a.slot_id,
                    cid=a.cid,
                    amount=a.amount,
                    message=a.message,
                )
                for a in activity
            ],
        )

    async def get_offers(self, status: str | None = None) -> list[OfferSnapshot]:
        if status:
            offers = await self._store.get_offers_by_status(status)
        else:
            offers = await self._store.get_all_offers()
        return [_offer_to_snapshot(o) for o in offers]

    async def get_approval_queue(self) -> list[OfferSnapshot]:
        offers = await self._store.get_approval_queue()
        return [_offer_to_snapshot(o) for o in offers]

    async def get_earnings(self, period: str = "all") -> EarningsSnapshot:
        e = await self._store.get_earnings()
        avg = e.total_earned // e.claims_count if e.claims_count > 0 else 0
        return EarningsSnapshot(
            total_earned_stroops=e.total_earned,
            total_earned_xlm=_xlm_str(e.total_earned),
            earned_24h_stroops=e.earned_24h,
            earned_24h_xlm=_xlm_str(e.earned_24h),
            earned_7d_stroops=e.earned_7d,
            earned_7d_xlm=_xlm_str(e.earned_7d),
            earned_30d_stroops=e.earned_30d,
            earned_30d_xlm=_xlm_str(e.earned_30d),
            claims_count=e.claims_count,
            average_per_claim_stroops=avg,
        )

    async def get_pins(self) -> list[PinSnapshot]:
        pins = await self._store.get_all_pins()
        return [
            PinSnapshot(
                cid=p.cid,
                slot_id=p.slot_id,
                bytes_pinned=p.bytes_pinned,
                pinned_at=p.pinned_at,
            )
            for p in pins
        ]

    async def get_wallet(self) -> WalletSnapshot:
        balance = await self._queries.get_wallet_balance(self._our_address)
        return WalletSnapshot(
            address=self._our_address,
            balance_stroops=balance,
            balance_xlm=_xlm_str(balance),
            can_cover_tx=balance >= ESTIMATED_TX_FEE * 2,
            estimated_tx_fee=ESTIMATED_TX_FEE,
        )

    # ── Actions ────────────────────────────────────────────

    async def approve_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        results = []
        for sid in slot_ids:
            offer = await self._store.get_offer(sid)
            if offer is None:
                results.append(ActionResult(success=False, message=f"Slot {sid} not found"))
                continue
            if offer.status != "awaiting_approval":
                results.append(ActionResult(
                    success=False,
                    message=f"Slot {sid} status is '{offer.status}', not awaiting_approval",
                ))
                continue
            await self._store.update_offer_status(sid, "approved")
            await self._store.log_activity(
                "offer_approved", f"Approved slot {sid}", slot_id=sid, cid=offer.cid,
            )
            results.append(ActionResult(success=True, message=f"Slot {sid} approved"))
        return results

    async def reject_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        results = []
        for sid in slot_ids:
            offer = await self._store.get_offer(sid)
            if offer is None:
                results.append(ActionResult(success=False, message=f"Slot {sid} not found"))
                continue
            await self._store.update_offer_status(sid, "rejected", reject_reason="operator_rejected")
            await self._store.log_activity(
                "offer_rejected", f"Rejected slot {sid}", slot_id=sid, cid=offer.cid,
            )
            results.append(ActionResult(success=True, message=f"Slot {sid} rejected"))
        return results

    async def set_mode(self, mode: str) -> ActionResult:
        try:
            new_mode = DaemonMode(mode)
        except ValueError:
            return ActionResult(success=False, message=f"Invalid mode: {mode}")

        self._mode_ctrl.set_mode(new_mode)
        await self._store.set_daemon_config(mode=mode)
        await self._store.log_activity("mode_changed", f"Mode set to {mode}")
        return ActionResult(success=True, message=f"Mode set to {mode}")

    async def update_policy(
        self, min_price: int | None = None, max_content_size: int | None = None
    ) -> ActionResult:
        await self._store.set_daemon_config(
            min_price=min_price, max_content_size=max_content_size,
        )
        parts = []
        if min_price is not None:
            parts.append(f"min_price={min_price}")
        if max_content_size is not None:
            parts.append(f"max_content_size={max_content_size}")
        msg = f"Policy updated: {', '.join(parts)}"
        await self._store.log_activity("policy_updated", msg)
        return ActionResult(success=True, message=msg)
