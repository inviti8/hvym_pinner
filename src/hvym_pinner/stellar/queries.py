"""Contract query helpers using the auto-generated bindings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from stellar_sdk import Address, Keypair, SorobanServer

from hvym_pinner.bindings.hvym_pin_service import (
    ClientAsync,
    Pinner as BindingPinner,
    PinSlot as BindingPinSlot,
)

log = logging.getLogger(__name__)

STROOPS_PER_XLM = 10_000_000


def _addr_str(addr: object) -> str:
    """Extract string address from Address or str."""
    if isinstance(addr, Address):
        return addr.address
    return str(addr)


@dataclass
class SlotInfo:
    """Simplified slot data for internal use."""

    slot_id: int
    cid_hash: str  # hex
    publisher: str
    offer_price: int
    pin_qty: int
    pins_remaining: int
    escrow_balance: int
    created_at: int
    claims: list[str]


@dataclass
class PinnerData:
    """Simplified pinner data for internal use."""

    address: str
    node_id: str
    multiaddr: str
    active: bool
    flags: int
    min_price: int
    pins_completed: int
    staked: int
    joined_at: int


class ContractQueries:
    """Read-only queries against the hvym-pin-service contract.

    Uses the async bindings client for simulation-only calls (no signing needed).
    """

    def __init__(
        self,
        contract_id: str,
        rpc_url: str,
        network_passphrase: str,
    ) -> None:
        self._client = ClientAsync(
            contract_id=contract_id,
            rpc_url=rpc_url,
            network_passphrase=network_passphrase,
        )
        self._rpc_url = rpc_url

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        try:
            await self._client.server.close()
        except Exception:
            pass

    async def get_slot(self, slot_id: int) -> SlotInfo | None:
        """Query a slot's current on-chain state."""
        try:
            tx = await self._client.get_slot(slot_id)
            await tx.simulate()
            raw: BindingPinSlot = tx.result()
            if raw is None:
                return None
            return SlotInfo(
                slot_id=slot_id,
                cid_hash=raw.cid_hash.hex() if isinstance(raw.cid_hash, bytes) else str(raw.cid_hash),
                publisher=_addr_str(raw.publisher),
                offer_price=raw.offer_price,
                pin_qty=raw.pin_qty,
                pins_remaining=raw.pins_remaining,
                escrow_balance=raw.escrow_balance,
                created_at=raw.created_at,
                claims=[_addr_str(c) for c in raw.claims],
            )
        except Exception as exc:
            log.warning("get_slot(%d) failed: %s", slot_id, exc)
            return None

    async def get_pinner(self, address: str) -> PinnerData | None:
        """Query a pinner's on-chain registry data."""
        try:
            tx = await self._client.get_pinner(address)
            await tx.simulate()
            raw: Optional[BindingPinner] = tx.result()
            if raw is None:
                return None
            return PinnerData(
                address=_addr_str(raw.address),
                node_id=raw.node_id.decode("utf-8") if isinstance(raw.node_id, bytes) else str(raw.node_id),
                multiaddr=raw.multiaddr.decode("utf-8") if isinstance(raw.multiaddr, bytes) else str(raw.multiaddr),
                active=raw.active,
                flags=raw.flags,
                min_price=raw.min_price,
                pins_completed=raw.pins_completed,
                staked=raw.staked,
                joined_at=raw.joined_at,
            )
        except Exception as exc:
            log.warning("get_pinner(%s) failed: %s", address[:16], exc)
            return None

    async def is_slot_expired(self, slot_id: int) -> bool | None:
        """Check if a slot has expired. Returns None on error."""
        try:
            tx = await self._client.is_slot_expired(slot_id)
            await tx.simulate()
            return tx.result()
        except Exception as exc:
            log.warning("is_slot_expired(%d) failed: %s", slot_id, exc)
            return None

    async def get_join_fee(self) -> int | None:
        """Get the join fee for pinners (in stroops)."""
        try:
            tx = await self._client.join_fee()
            await tx.simulate()
            return tx.result()
        except Exception as exc:
            log.warning("get_join_fee failed: %s", exc)
            return None

    async def get_pinner_stake(self) -> int | None:
        """Get the required pinner stake (in stroops)."""
        try:
            tx = await self._client.pinner_stake_amount()
            await tx.simulate()
            return tx.result()
        except Exception as exc:
            log.warning("get_pinner_stake failed: %s", exc)
            return None

    async def get_wallet_balance(self, address: str) -> int:
        """Get the native XLM balance for an address in stroops.

        Uses the Horizon-compatible account query via SorobanServer.
        """
        try:
            server = SorobanServer(self._rpc_url)
            # SorobanServer doesn't have account queries directly,
            # so we use the Stellar SDK's Server for balance
            from stellar_sdk import Server as HorizonServer

            # For testnet, use Horizon API
            horizon_url = self._rpc_url.replace("soroban-testnet", "horizon-testnet")
            if "soroban" not in horizon_url:
                horizon_url = "https://horizon-testnet.stellar.org"

            horizon = HorizonServer(horizon_url)
            account = horizon.accounts().account_id(address).call()

            for balance in account.get("balances", []):
                if balance.get("asset_type") == "native":
                    xlm = float(balance["balance"])
                    return int(xlm * STROOPS_PER_XLM)

            return 0
        except Exception as exc:
            log.warning("get_wallet_balance(%s) failed: %s", address[:16], exc)
            return 0
