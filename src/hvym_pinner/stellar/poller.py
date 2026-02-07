"""Soroban event poller - polls RPC for PIN/PINNED/UNPIN contract events."""

from __future__ import annotations

import logging
from typing import Sequence

from stellar_sdk import Address, SorobanServer, scval, xdr
from stellar_sdk.soroban_rpc import EventFilter, EventFilterType, EventInfo

from hvym_pinner.bindings.hvym_pin_service import (
    PinEvent as BindingPinEvent,
    PinnedEvent as BindingPinnedEvent,
    UnpinEvent as BindingUnpinEvent,
)
from hvym_pinner.interfaces.poller import ContractEvent
from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent

log = logging.getLogger(__name__)

# Pre-computed XDR base64 for topic symbols used in filters
_TOPIC_PIN = scval.to_symbol("PIN").to_xdr()
_TOPIC_PINNED = scval.to_symbol("PINNED").to_xdr()
_TOPIC_UNPIN = scval.to_symbol("UNPIN").to_xdr()

# Topic patterns emitted by the contract:
#   PIN event:    ("PIN",    "request")
#   PINNED event: ("PINNED", "claim")
#   UNPIN event:  ("UNPIN",  "request")
_TOPIC_MAP = {
    "PIN": "pin",
    "PINNED": "pinned",
    "UNPIN": "unpin",
}


def _addr_to_str(addr: object) -> str:
    """Extract the string address from a stellar_sdk.Address or plain str."""
    if isinstance(addr, Address):
        return addr.address
    return str(addr)


def _decode_topic(topic_xdr: str) -> str:
    """Decode a base64 XDR SCVal symbol to a plain string."""
    val = xdr.SCVal.from_xdr(topic_xdr)
    return scval.from_symbol(val)


def _parse_event(info: EventInfo) -> ContractEvent | None:
    """Parse a raw EventInfo into one of our model event types.

    Returns None if the event type is unrecognized or malformed.
    """
    if len(info.topic) < 1:
        return None

    try:
        event_kind = _decode_topic(info.topic[0])
    except Exception:
        log.debug("Could not decode topic[0] for event %s", info.id)
        return None

    # Decode the event value from XDR
    try:
        value_scval = xdr.SCVal.from_xdr(info.value)
    except Exception:
        log.warning("Could not decode value XDR for event %s", info.id)
        return None

    try:
        if event_kind == "PIN":
            raw = BindingPinEvent.from_scval(value_scval)
            return PinEvent(
                slot_id=raw.slot_id,
                cid=raw.cid.decode("utf-8") if isinstance(raw.cid, bytes) else str(raw.cid),
                filename=raw.filename.decode("utf-8") if isinstance(raw.filename, bytes) else str(raw.filename),
                gateway=raw.gateway.decode("utf-8") if isinstance(raw.gateway, bytes) else str(raw.gateway),
                offer_price=raw.offer_price,
                pin_qty=raw.pin_qty,
                publisher=_addr_to_str(raw.publisher),
                ledger_sequence=info.ledger,
            )

        elif event_kind == "PINNED":
            raw = BindingPinnedEvent.from_scval(value_scval)
            return PinnedEvent(
                slot_id=raw.slot_id,
                cid_hash=raw.cid_hash.hex() if isinstance(raw.cid_hash, bytes) else str(raw.cid_hash),
                pinner=_addr_to_str(raw.pinner),
                amount=raw.amount,
                pins_remaining=raw.pins_remaining,
                ledger_sequence=info.ledger,
            )

        elif event_kind == "UNPIN":
            raw = BindingUnpinEvent.from_scval(value_scval)
            return UnpinEvent(
                slot_id=raw.slot_id,
                cid_hash=raw.cid_hash.hex() if isinstance(raw.cid_hash, bytes) else str(raw.cid_hash),
                ledger_sequence=info.ledger,
            )

        else:
            log.debug("Ignoring event kind: %s", event_kind)
            return None

    except Exception as exc:
        log.warning("Failed to parse %s event %s: %s", event_kind, info.id, exc)
        return None


class SorobanEventPoller:
    """Polls Soroban RPC for hvym-pin-service contract events.

    Uses SorobanServer.get_events() with topic filters for PIN, PINNED,
    and UNPIN events. Maintains a cursor (event ID) for resumption across
    restarts.
    """

    def __init__(
        self,
        rpc_url: str,
        contract_id: str,
        start_ledger: int | None = None,
    ) -> None:
        self._server = SorobanServer(rpc_url)
        self._contract_id = contract_id
        self._cursor: str | None = None
        self._start_ledger: int | None = start_ledger
        self._filters = [
            EventFilter(
                event_type=EventFilterType.CONTRACT,
                contract_ids=[contract_id],
                topics=[
                    [_TOPIC_PIN, _TOPIC_PINNED, _TOPIC_UNPIN],
                ],
            )
        ]

    @property
    def cursor(self) -> str | None:
        return self._cursor

    def set_cursor(self, cursor: str) -> None:
        """Restore cursor from persisted state."""
        self._cursor = cursor

    async def poll(self) -> list[ContractEvent]:
        """Fetch new events since last cursor.

        On first call (no cursor), uses start_ledger or latest_ledger from RPC.
        Subsequent calls use the cursor for pagination.
        """
        try:
            if self._cursor:
                response = self._server.get_events(
                    filters=self._filters,
                    cursor=self._cursor,
                    limit=100,
                )
            else:
                # First poll - need a start ledger
                start = self._start_ledger
                if start is None:
                    # Use the latest ledger from the server
                    health = self._server.get_latest_ledger()
                    start = health.sequence
                    log.info("No cursor, starting from latest ledger %d", start)

                response = self._server.get_events(
                    start_ledger=start,
                    filters=self._filters,
                    limit=100,
                )

        except Exception as exc:
            log.error("Event poll failed: %s", exc)
            raise

        events: list[ContractEvent] = []
        for info in response.events:
            if not info.in_successful_contract_call:
                continue
            parsed = _parse_event(info)
            if parsed is not None:
                events.append(parsed)
                log.debug(
                    "Parsed %s event at ledger %d",
                    type(parsed).__name__,
                    info.ledger,
                )

        # Update cursor to the last event we saw
        if response.events:
            self._cursor = response.events[-1].id
        elif response.cursor:
            self._cursor = response.cursor

        if events:
            log.info("Polled %d events (cursor: %s)", len(events), self._cursor)

        return events

    async def get_cursor(self) -> int | None:
        """Get the latest ledger from the last poll for persistence.

        Note: We store the string cursor internally for RPC pagination,
        but the StateStore interface uses ledger (int) for simplicity.
        The event ID encodes the ledger.
        """
        if self._cursor is None:
            return None
        # Soroban event IDs encode the ledger - extract it
        # Format: "{ledger}-{index}" or just use the latest ledger
        try:
            return int(self._cursor.split("-")[0])
        except (ValueError, IndexError):
            return None
