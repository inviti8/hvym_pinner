"""Kubo IPFS pin executor - pins/unpins content via the Kubo HTTP RPC."""

from __future__ import annotations

import logging
import time

import httpx

from hvym_pinner.models.records import PinResult

log = logging.getLogger(__name__)


class KuboPinExecutor:
    """Handles IPFS pinning operations against a local Kubo node.

    Uses the Kubo HTTP RPC API at /api/v0/ for:
    - pin/add: Pin a CID (fetches content from the network)
    - pin/ls: Check if a CID is pinned
    - pin/rm: Remove a pin
    """

    def __init__(
        self,
        kubo_rpc_url: str = "http://127.0.0.1:5001",
        pin_timeout: int = 60,
        max_content_size: int = 1_073_741_824,
        fetch_retries: int = 3,
    ) -> None:
        self._base_url = kubo_rpc_url.rstrip("/")
        self._pin_timeout = pin_timeout
        self._max_content_size = max_content_size
        self._fetch_retries = fetch_retries

    def _url(self, endpoint: str) -> str:
        return f"{self._base_url}/api/v0/{endpoint}"

    async def pin(self, cid: str, gateway: str) -> PinResult:
        """Pin a CID to the local Kubo node.

        Uses pin/add which fetches the content from the IPFS network.
        The gateway hint is logged but Kubo resolves via its own DHT.
        """
        log.info("Pinning CID %s (gateway hint: %s)", cid, gateway)
        start = time.monotonic()

        for attempt in range(1, self._fetch_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._pin_timeout) as client:
                    # pin/add fetches and pins in one call
                    resp = await client.post(
                        self._url("pin/add"),
                        params={"arg": cid, "progress": "false"},
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    duration = int((time.monotonic() - start) * 1000)
                    pinned_cid = data.get("Pins", [cid])[0] if "Pins" in data else cid

                    # Get object size
                    bytes_pinned = await self._get_object_size(cid)

                    log.info(
                        "Pinned %s (%s bytes) in %dms",
                        pinned_cid,
                        bytes_pinned or "?",
                        duration,
                    )
                    return PinResult(
                        success=True,
                        cid=pinned_cid,
                        bytes_pinned=bytes_pinned,
                        duration_ms=duration,
                    )

            except httpx.TimeoutException:
                duration = int((time.monotonic() - start) * 1000)
                if attempt < self._fetch_retries:
                    log.warning(
                        "Pin timeout for %s (attempt %d/%d)",
                        cid,
                        attempt,
                        self._fetch_retries,
                    )
                    continue
                return PinResult(
                    success=False,
                    cid=cid,
                    error=f"timeout after {self._fetch_retries} attempts",
                    duration_ms=duration,
                )

            except httpx.HTTPStatusError as exc:
                duration = int((time.monotonic() - start) * 1000)
                error_msg = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                log.error("Pin failed for %s: %s", cid, error_msg)
                return PinResult(
                    success=False,
                    cid=cid,
                    error=error_msg,
                    duration_ms=duration,
                )

            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                log.error("Pin error for %s: %s", cid, exc)
                return PinResult(
                    success=False,
                    cid=cid,
                    error=str(exc),
                    duration_ms=duration,
                )

        # Should not reach here, but just in case
        return PinResult(success=False, cid=cid, error="max retries exceeded")

    async def verify_pinned(self, cid: str) -> bool:
        """Check if a CID is pinned on our local node."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._url("pin/ls"),
                    params={"arg": cid, "type": "recursive"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return cid in data.get("Keys", {})
                return False
        except Exception as exc:
            log.warning("verify_pinned(%s) failed: %s", cid, exc)
            return False

    async def unpin(self, cid: str) -> bool:
        """Remove a pin from the local Kubo node."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._url("pin/rm"),
                    params={"arg": cid},
                )
                if resp.status_code == 200:
                    log.info("Unpinned %s", cid)
                    return True
                # 500 with "not pinned" is also acceptable
                if "not pinned" in resp.text.lower():
                    log.debug("CID %s was not pinned", cid)
                    return True
                log.warning("Unpin failed for %s: %s", cid, resp.text[:200])
                return False
        except Exception as exc:
            log.error("Unpin error for %s: %s", cid, exc)
            return False

    async def _get_object_size(self, cid: str) -> int | None:
        """Get the cumulative size of a pinned object."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._url("object/stat"),
                    params={"arg": cid},
                )
                if resp.status_code == 200:
                    return resp.json().get("CumulativeSize")
        except Exception:
            pass
        return None
