"""Internal record types for state persistence and operation results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PinResult:
    """Result of a pin operation against the local Kubo node."""

    success: bool
    cid: str
    bytes_pinned: int | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ClaimResult:
    """Result of a collect_pin() transaction submission."""

    success: bool
    slot_id: int
    amount_earned: int | None = None  # stroops
    tx_hash: str | None = None
    error: str | None = None


@dataclass
class FilterResult:
    """Result of offer evaluation by the OfferFilter."""

    accepted: bool
    reason: str  # "price_too_low", "already_claimed", "insufficient_xlm", etc.
    slot_id: int
    offer_price: int
    wallet_balance: int  # stroops at time of evaluation
    estimated_tx_fee: int  # stroops
    net_profit: int  # offer_price - estimated_tx_fee


@dataclass
class OfferRecord:
    """A PIN offer as persisted in the state store."""

    slot_id: int
    cid: str
    filename: str
    gateway: str
    offer_price: int
    pin_qty: int
    pins_remaining: int
    publisher: str
    ledger_sequence: int
    status: str = "pending"
    reject_reason: str | None = None
    net_profit: int | None = None
    estimated_expiry: str | None = None  # ISO 8601
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PinRecord:
    """A CID pinned on our local Kubo node."""

    cid: str
    slot_id: int | None = None
    bytes_pinned: int | None = None
    pinned_at: str = ""


@dataclass
class ActivityRecord:
    """A single activity log entry."""

    id: int
    event_type: str
    slot_id: int | None
    cid: str | None
    amount: int | None  # stroops
    message: str
    created_at: str


@dataclass
class ActionResult:
    """Result of a frontend-initiated action."""

    success: bool
    message: str


@dataclass
class EarningsSummary:
    """Aggregated earnings from claims."""

    total_earned: int = 0  # stroops
    earned_24h: int = 0
    earned_7d: int = 0
    earned_30d: int = 0
    claims_count: int = 0


@dataclass
class DaemonConfigRecord:
    """Runtime daemon config as persisted in SQLite."""

    mode: str = "auto"
    min_price: int = 100
    max_content_size: int = 1_073_741_824  # 1 GB
