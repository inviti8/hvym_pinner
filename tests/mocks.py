"""Mock implementations of all external-facing components."""

from __future__ import annotations

from hvym_pinner.models.records import PinResult, ClaimResult, FilterResult
from hvym_pinner.models.hunter import (
    FlagResult,
    PinnerInfo,
    VerificationResult,
    MethodResult,
)
from hvym_pinner.models.events import PinEvent, PinnedEvent, UnpinEvent
from hvym_pinner.interfaces.poller import ContractEvent
from hvym_pinner.stellar.queries import SlotInfo, PinnerData


class MockPoller:
    """Implements EventPoller protocol. Returns pre-loaded event lists."""

    def __init__(self) -> None:
        self.events: list[ContractEvent] = []
        self._cursor: str | None = None

    async def poll(self) -> list[ContractEvent]:
        result = list(self.events)
        self.events.clear()
        return result

    async def get_cursor(self) -> int | None:
        if self._cursor is None:
            return None
        try:
            return int(self._cursor.split("-")[0])
        except (ValueError, IndexError):
            return None

    def set_cursor(self, cursor: str) -> None:
        self._cursor = cursor

    def enqueue(self, *events: ContractEvent) -> None:
        """Test helper: stage events for next poll."""
        self.events.extend(events)


class MockExecutor:
    """Implements PinExecutor protocol."""

    def __init__(
        self,
        succeed: bool = True,
        bytes_pinned: int = 1024,
        error: str | None = None,
    ) -> None:
        self.succeed = succeed
        self.bytes_pinned = bytes_pinned
        self._error = error
        self.pinned_cids: set[str] = set()
        self.pin_calls: list[tuple[str, str]] = []

    async def pin(self, cid: str, gateway: str) -> PinResult:
        self.pin_calls.append((cid, gateway))
        if self.succeed:
            self.pinned_cids.add(cid)
            return PinResult(
                success=True,
                cid=cid,
                bytes_pinned=self.bytes_pinned,
                duration_ms=10,
            )
        return PinResult(
            success=False,
            cid=cid,
            error=self._error or "mock pin failure",
            duration_ms=10,
        )

    async def verify_pinned(self, cid: str) -> bool:
        return cid in self.pinned_cids

    async def unpin(self, cid: str) -> bool:
        self.pinned_cids.discard(cid)
        return True


class MockSubmitter:
    """Implements ClaimSubmitter protocol."""

    def __init__(
        self,
        succeed: bool = True,
        tx_hash: str = "mock_tx_abc123",
        error: str | None = None,
    ) -> None:
        self.succeed = succeed
        self.tx_hash = tx_hash
        self._error = error
        self.claim_calls: list[int] = []

    async def submit_claim(self, slot_id: int) -> ClaimResult:
        self.claim_calls.append(slot_id)
        if self.succeed:
            return ClaimResult(
                success=True,
                slot_id=slot_id,
                tx_hash=self.tx_hash,
            )
        return ClaimResult(
            success=False,
            slot_id=slot_id,
            error=self._error or "mock claim failure",
        )


class MockQueries:
    """Implements ContractQueries-like interface for filter tests."""

    def __init__(
        self,
        wallet_balance: int = 10_000_000,
        slot_expired: bool = False,
        slot_info: SlotInfo | None = None,
        pinner_data: PinnerData | None = None,
    ) -> None:
        self.wallet_balance = wallet_balance
        self.slot_expired = slot_expired
        self._slot_info = slot_info or SlotInfo(
            slot_id=1,
            cid_hash="abc123",
            publisher="GABCDEF...",
            offer_price=1_000_000,
            pin_qty=3,
            pins_remaining=3,
            escrow_balance=3_000_000,
            created_at=0,
            claims=[],
        )
        self._pinner_data = pinner_data

    async def get_wallet_balance(self, address: str) -> int:
        return self.wallet_balance

    async def is_slot_expired(self, slot_id: int) -> bool | None:
        return self.slot_expired

    async def get_slot(self, slot_id: int) -> SlotInfo | None:
        return self._slot_info

    async def get_pinner(self, address: str) -> PinnerData | None:
        return self._pinner_data


class MockFlagSubmitter:
    """Implements FlagSubmitter protocol."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.flag_calls: list[str] = []
        self._already_flagged: set[str] = set()

    async def submit_flag(self, pinner_address: str) -> FlagResult:
        self.flag_calls.append(pinner_address)
        if self.succeed:
            return FlagResult(
                success=True,
                pinner_address=pinner_address,
                flag_count=1,
                tx_hash="mock_flag_tx_123",
            )
        return FlagResult(
            success=False,
            pinner_address=pinner_address,
            error="mock flag failure",
        )

    async def has_already_flagged(self, pinner_address: str) -> bool:
        return pinner_address in self._already_flagged


class MockVerifier:
    """Implements PinVerifier protocol."""

    def __init__(self, passed: bool = True) -> None:
        self.passed = passed
        self.verify_calls: list[tuple[str, str, str]] = []

    async def verify(
        self, cid: str, pinner_node_id: str, pinner_multiaddr: str
    ) -> VerificationResult:
        self.verify_calls.append((cid, pinner_node_id, pinner_multiaddr))
        method = "bitswap"
        return VerificationResult(
            cid=cid,
            pinner_node_id=pinner_node_id,
            passed=self.passed,
            method_used=method,
            methods_attempted=[
                MethodResult(method=method, passed=self.passed, detail="mock"),
            ],
            duration_ms=5,
            checked_at="2025-01-01T00:00:00Z",
        )
