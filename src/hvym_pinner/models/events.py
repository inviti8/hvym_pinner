"""Contract event models deserialized from Soroban event stream."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinEvent:
    """Emitted when a publisher creates a pin request (PIN topic)."""

    slot_id: int
    cid: str
    filename: str
    gateway: str
    offer_price: int  # stroops
    pin_qty: int
    publisher: str  # Stellar address
    ledger_sequence: int


@dataclass(frozen=True)
class PinnedEvent:
    """Emitted when a pinner collects payment (PINNED topic).

    Critical for CID Hunter: tells us which pinner claimed which CID.
    """

    slot_id: int
    cid_hash: str  # SHA256 hex of CID
    pinner: str  # Stellar address of claiming pinner
    amount: int  # stroops paid
    pins_remaining: int
    ledger_sequence: int


@dataclass(frozen=True)
class UnpinEvent:
    """Emitted when a slot is freed: cancelled, expired, or filled (UNPIN topic)."""

    slot_id: int
    cid_hash: str  # SHA256 hex of CID
    ledger_sequence: int
