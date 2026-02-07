"""JSON-serializable snapshot models for the Data API / frontend bridge."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from hvym_pinner.models.hunter import HunterSummary


def _to_dict(obj: Any) -> dict:
    """Recursively convert a dataclass to a plain dict."""
    return asdict(obj)


# ---------------------------------------------------------------------------
# Core dashboard
# ---------------------------------------------------------------------------


@dataclass
class WalletSnapshot:
    address: str
    balance_stroops: int
    balance_xlm: str  # "123.456 XLM"
    can_cover_tx: bool  # enough for at least one collect_pin() tx
    estimated_tx_fee: int  # stroops


@dataclass
class EarningsSnapshot:
    total_earned_stroops: int = 0
    total_earned_xlm: str = "0 XLM"
    earned_24h_stroops: int = 0
    earned_24h_xlm: str = "0 XLM"
    earned_7d_stroops: int = 0
    earned_7d_xlm: str = "0 XLM"
    earned_30d_stroops: int = 0
    earned_30d_xlm: str = "0 XLM"
    claims_count: int = 0
    average_per_claim_stroops: int = 0


@dataclass
class PinnerSnapshot:
    address: str
    node_id: str
    multiaddr: str
    min_price: int
    pins_completed: int
    flags: int
    staked: int
    active: bool


@dataclass
class SlotSnapshot:
    slot_id: int
    active: bool
    publisher: str | None = None
    offer_price: int | None = None
    pin_qty: int | None = None
    pins_remaining: int | None = None
    expired: bool = False
    claimed_by_us: bool = False


@dataclass
class ContractSnapshot:
    contract_id: str
    pin_fee: int = 0
    min_offer_price: int = 0
    min_pin_qty: int = 0
    max_cycles: int = 0
    pinner_stake: int = 0
    our_pinner: PinnerSnapshot | None = None
    slots: list[SlotSnapshot] = field(default_factory=list)
    available_slots: int = 0


@dataclass
class OfferSnapshot:
    """A PIN offer as seen by the frontend."""

    slot_id: int
    cid: str
    gateway: str
    offer_price: int  # stroops
    offer_price_xlm: str  # "0.01 XLM"
    pin_qty: int
    pins_remaining: int
    publisher: str
    status: str
    net_profit: int = 0
    expires_in_seconds: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PinSnapshot:
    """A CID pinned on our local node."""

    cid: str
    slot_id: int | None = None
    bytes_pinned: int | None = None
    pinned_at: str = ""


@dataclass
class OperationSnapshot:
    """An in-flight pin or claim operation."""

    slot_id: int
    cid: str
    stage: str  # "fetching" | "pinning" | "verifying" | "claiming"
    progress_pct: int | None = None  # 0-100 if known
    started_at: str = ""


@dataclass
class ActivityEntry:
    """A single line in the activity feed."""

    timestamp: str  # ISO 8601
    event_type: str
    slot_id: int | None = None
    cid: str | None = None
    amount: int | None = None  # stroops
    message: str = ""


@dataclass
class DashboardSnapshot:
    """Complete daemon state in one serializable object."""

    # Identity
    mode: str  # "auto" | "approve"
    pinner_address: str
    node_id: str
    uptime_seconds: int

    # Connectivity
    stellar_connected: bool
    stellar_latest_ledger: int
    kubo_connected: bool
    kubo_peer_count: int

    # Wallet
    wallet: WalletSnapshot

    # Activity summary
    offers_seen: int
    offers_accepted: int
    offers_rejected: int
    offers_awaiting_approval: int  # 0 in auto mode
    pins_active: int
    claims_completed: int

    # Earnings
    earnings: EarningsSnapshot

    # Live queues
    approval_queue: list[OfferSnapshot] = field(default_factory=list)
    active_operations: list[OperationSnapshot] = field(default_factory=list)

    # Recent activity
    recent_activity: list[ActivityEntry] = field(default_factory=list)

    # Contract state
    contract: ContractSnapshot | None = None

    # CID Hunter (None if disabled)
    hunter: HunterSummary | None = None

    def to_dict(self) -> dict:
        return _to_dict(self)
