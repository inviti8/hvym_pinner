"""Pinner registry cache - cached on-chain pinner info for verification."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from hvym_pinner.models.hunter import PinnerInfo
from hvym_pinner.stellar.queries import ContractQueries
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


class PinnerRegistryCacheImpl:
    """Local cache of on-chain pinner registry data.

    Caches pinner IPFS node details (node_id, multiaddr) in SQLite to avoid
    hitting the chain for every verification check. Entries expire after
    a configurable TTL.
    """

    def __init__(
        self,
        store: SQLiteStateStore,
        queries: ContractQueries,
        ttl_seconds: int = 3600,
    ) -> None:
        self._store = store
        self._queries = queries
        self._ttl = ttl_seconds

    async def get_pinner_info(self, address: str) -> PinnerInfo | None:
        """Get pinner IPFS node details. Fetches from chain if not cached or expired."""
        cached = await self._store.get_cached_pinner(address)
        if cached and not self._is_expired(cached):
            return cached
        return await self.refresh(address)

    async def refresh(self, address: str) -> PinnerInfo | None:
        """Force refresh pinner info from chain."""
        pinner = await self._queries.get_pinner(address)
        if pinner is None:
            return None

        info = PinnerInfo(
            address=pinner.address,
            node_id=pinner.node_id,
            multiaddr=pinner.multiaddr,
            active=pinner.active,
        )
        await self._store.cache_pinner(info)
        log.debug("Cached pinner info for %s (node=%s)", address[:16], pinner.node_id[:16])
        return info

    def _is_expired(self, info: PinnerInfo) -> bool:
        """Check if a cached entry has expired."""
        if not info.cached_at:
            return True
        try:
            cached_time = datetime.fromisoformat(info.cached_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - cached_time).total_seconds()
            return age > self._ttl
        except (ValueError, TypeError):
            return True
