"""EventPoller protocol - polls Soroban RPC for contract events."""

from __future__ import annotations

from typing import Protocol, Union

from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent

ContractEvent = Union[PinEvent, PinnedEvent, UnpinEvent]


class EventPoller(Protocol):
    """Polls for new contract events from hvym-pin-service."""

    async def poll(self) -> list[ContractEvent]:
        """Fetch new events since last cursor. Returns deserialized events."""
        ...

    async def get_cursor(self) -> int | None:
        """Get the last processed ledger sequence for resumption."""
        ...
