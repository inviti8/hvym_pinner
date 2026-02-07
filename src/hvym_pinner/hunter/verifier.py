"""Pin verifier - DHT provider lookup + Bitswap verification via Kubo RPC."""

from __future__ import annotations

import logging
import time

import httpx

from hvym_pinner.models.hunter import MethodResult, VerificationResult

log = logging.getLogger(__name__)


class KuboPinVerifier:
    """Verifies a pinner is actually serving a CID on the IPFS network.

    Uses the local Kubo node's RPC API to:
    1. DHT provider lookup: check if pinner is advertising the CID
    2. Bitswap verification: connect to pinner and try to get a block
    3. Optional partial retrieval: fetch first 1024 bytes of content
    """

    def __init__(
        self,
        kubo_rpc_url: str = "http://127.0.0.1:5001",
        check_timeout: int = 30,
        methods: list[str] | None = None,
    ) -> None:
        self._base_url = kubo_rpc_url.rstrip("/")
        self._check_timeout = check_timeout
        self._methods = methods or ["dht_provider", "bitswap"]

    def _url(self, endpoint: str) -> str:
        return f"{self._base_url}/api/v0/{endpoint}"

    async def verify(
        self, cid: str, pinner_node_id: str, pinner_multiaddr: str
    ) -> VerificationResult:
        """Run verification pipeline against a single (CID, pinner) pair.

        Runs configured methods in order. Returns as soon as one is definitive.
        """
        start = time.monotonic()
        methods_attempted: list[MethodResult] = []
        overall_passed = False
        method_used = "none"

        for method_name in self._methods:
            if method_name == "dht_provider":
                result = await self._check_dht_provider(cid, pinner_node_id)
            elif method_name == "bitswap":
                result = await self._check_bitswap(cid, pinner_node_id, pinner_multiaddr)
            elif method_name == "retrieval":
                result = await self._check_partial_retrieval(cid, pinner_multiaddr)
            else:
                continue

            methods_attempted.append(result)

            if result.passed is True:
                overall_passed = True
                method_used = result.method
                break
            elif result.passed is False and method_name == "bitswap":
                # Bitswap is definitive - if it fails, the pinner doesn't have it
                method_used = result.method
                break

        duration = int((time.monotonic() - start) * 1000)
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        return VerificationResult(
            cid=cid,
            pinner_node_id=pinner_node_id,
            passed=overall_passed,
            method_used=method_used,
            methods_attempted=methods_attempted,
            duration_ms=duration,
            checked_at=checked_at,
        )

    async def _check_dht_provider(self, cid: str, pinner_node_id: str) -> MethodResult:
        """Step 1: DHT provider lookup - is pinner advertising this CID?"""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                # findprovs returns NDJSON stream - read with a reasonable limit
                resp = await client.post(
                    self._url("routing/findprovs"),
                    params={"arg": cid, "num-providers": "20"},
                )
                resp.raise_for_status()

                # Parse NDJSON response - each line is a JSON object
                found = False
                for line in resp.text.strip().split("\n"):
                    if not line.strip():
                        continue
                    import json
                    try:
                        entry = json.loads(line)
                        # The response contains Responses with provider IDs
                        responses = entry.get("Responses") or []
                        for r in responses:
                            peer_id = r.get("ID", "")
                            if peer_id == pinner_node_id:
                                found = True
                                break
                    except json.JSONDecodeError:
                        continue
                    if found:
                        break

                duration = int((time.monotonic() - start) * 1000)
                if found:
                    return MethodResult(
                        method="dht_provider",
                        passed=True,
                        detail=f"Pinner found in DHT providers for {cid[:16]}...",
                        duration_ms=duration,
                    )
                else:
                    return MethodResult(
                        method="dht_provider",
                        passed=None,  # Inconclusive - DHT may be slow to propagate
                        detail=f"Pinner not found in DHT providers (checked 20)",
                        duration_ms=duration,
                    )

        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            return MethodResult(
                method="dht_provider",
                passed=None,
                detail=f"DHT lookup error: {exc}",
                duration_ms=duration,
            )

    async def _check_bitswap(
        self, cid: str, pinner_node_id: str, pinner_multiaddr: str
    ) -> MethodResult:
        """Step 2: Bitswap verification - connect and try block retrieval."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                # 1. Connect to the pinner's node
                connect_resp = await client.post(
                    self._url("swarm/connect"),
                    params={"arg": pinner_multiaddr},
                )
                if connect_resp.status_code != 200:
                    duration = int((time.monotonic() - start) * 1000)
                    return MethodResult(
                        method="bitswap",
                        passed=False,
                        detail=f"Failed to connect to pinner: HTTP {connect_resp.status_code}",
                        duration_ms=duration,
                    )

                # 2. Try to get a block - if the pinner has it, we'll get data back
                block_resp = await client.post(
                    self._url("block/get"),
                    params={"arg": cid},
                )

                duration = int((time.monotonic() - start) * 1000)

                if block_resp.status_code == 200 and len(block_resp.content) > 0:
                    return MethodResult(
                        method="bitswap",
                        passed=True,
                        detail=f"Block retrieved ({len(block_resp.content)} bytes)",
                        duration_ms=duration,
                    )
                else:
                    return MethodResult(
                        method="bitswap",
                        passed=False,
                        detail=f"Block not available (HTTP {block_resp.status_code})",
                        duration_ms=duration,
                    )

        except httpx.TimeoutException:
            duration = int((time.monotonic() - start) * 1000)
            return MethodResult(
                method="bitswap",
                passed=False,
                detail="Bitswap timeout - pinner not responding",
                duration_ms=duration,
            )
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            return MethodResult(
                method="bitswap",
                passed=False,
                detail=f"Bitswap error: {exc}",
                duration_ms=duration,
            )

    async def _check_partial_retrieval(
        self, cid: str, pinner_multiaddr: str
    ) -> MethodResult:
        """Step 3: Partial content retrieval - fetch first 1024 bytes."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                resp = await client.post(
                    self._url("cat"),
                    params={"arg": cid, "length": "1024"},
                )

                duration = int((time.monotonic() - start) * 1000)

                if resp.status_code == 200 and len(resp.content) > 0:
                    return MethodResult(
                        method="retrieval",
                        passed=True,
                        detail=f"Retrieved {len(resp.content)} bytes",
                        duration_ms=duration,
                    )
                else:
                    return MethodResult(
                        method="retrieval",
                        passed=False,
                        detail=f"Retrieval failed (HTTP {resp.status_code})",
                        duration_ms=duration,
                    )

        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            return MethodResult(
                method="retrieval",
                passed=False,
                detail=f"Retrieval error: {exc}",
                duration_ms=duration,
            )
