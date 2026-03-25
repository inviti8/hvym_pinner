"""Resolve contract IDs from the on-chain hvym-registry contract."""

from __future__ import annotations

import logging

from stellar_sdk import Address

from hvym_pinner.bindings.hvym_registry import ClientAsync, Network, NetworkKind
from hvym_pinner.models.config import (
    DaemonConfig,
    REGISTRY_CONTRACT_ID,
    REGISTRY_PASSPHRASE,
    REGISTRY_RPC_URL,
)

log = logging.getLogger(__name__)

# Contract name as registered on-chain
_PIN_SERVICE = b"hvym_pin_service"


def _network_kind(name: str) -> Network:
    """Map config network name to registry Network enum."""
    if name == "mainnet":
        return Network(NetworkKind.Mainnet)
    return Network(NetworkKind.Testnet)


def _addr_str(addr: object) -> str:
    """Extract string from Address or str."""
    if isinstance(addr, Address):
        return addr.address
    return str(addr)


async def _query_contract(
    client: ClientAsync,
    name: bytes,
    network: Network,
) -> str | None:
    """Query a single contract ID from the registry. Returns None on failure."""
    try:
        tx = await client.get_contract_id(name, network)
        await tx.simulate()
        return _addr_str(tx.result())
    except Exception as exc:
        label = name.decode("utf-8", errors="replace")
        log.warning("Registry lookup failed for %s, using fallback: %s", label, exc)
        return None


async def resolve_contracts(cfg: DaemonConfig) -> None:
    """Query the on-chain registry and update cfg's contract IDs.

    The registry contract lives on mainnet and stores contract IDs for
    both testnet and mainnet.  Each lookup is independent — a failure
    for one contract does not affect the other.  On failure the existing
    config value (hardcoded fallback from NETWORK_DEFAULTS) is kept.
    """
    network = _network_kind(cfg.network)

    client = ClientAsync(
        contract_id=REGISTRY_CONTRACT_ID,
        rpc_url=REGISTRY_RPC_URL,
        network_passphrase=REGISTRY_PASSPHRASE,
    )

    try:
        addr = await _query_contract(client, _PIN_SERVICE, network)
        if addr:
            cfg.contract_id = addr
            log.info("Registry: hvym_pin_service → %s", addr)
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
