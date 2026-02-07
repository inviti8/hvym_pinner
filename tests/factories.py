"""Synthetic event factories for testing."""

from __future__ import annotations

import hashlib

from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent


def make_pin_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    filename: str = "test-asset.glb",
    gateway: str = "https://ipfs.example.com",
    offer_price: int = 1_000_000,
    pin_qty: int = 3,
    publisher: str = "GABCDEFGHIJKLMNOPQRSTUVWXYZ234567GABCDEFGHIJKLMNOPQRST",
    ledger_sequence: int = 100000,
) -> PinEvent:
    return PinEvent(
        slot_id=slot_id,
        cid=cid,
        filename=filename,
        gateway=gateway,
        offer_price=offer_price,
        pin_qty=pin_qty,
        publisher=publisher,
        ledger_sequence=ledger_sequence,
    )


def make_pinned_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    pinner: str = "GDNAG4KFFVF5HCSGRWZIXZNL2SR2KBGJSHW2A6FI6DZI62XF6IBLO4GD",
    amount: int = 1_000_000,
    pins_remaining: int = 2,
    ledger_sequence: int = 100001,
) -> PinnedEvent:
    cid_hash = hashlib.sha256(cid.encode("utf-8")).hexdigest()
    return PinnedEvent(
        slot_id=slot_id,
        cid_hash=cid_hash,
        pinner=pinner,
        amount=amount,
        pins_remaining=pins_remaining,
        ledger_sequence=ledger_sequence,
    )


def make_unpin_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    ledger_sequence: int = 100002,
) -> UnpinEvent:
    cid_hash = hashlib.sha256(cid.encode("utf-8")).hexdigest()
    return UnpinEvent(
        slot_id=slot_id,
        cid_hash=cid_hash,
        ledger_sequence=ledger_sequence,
    )
