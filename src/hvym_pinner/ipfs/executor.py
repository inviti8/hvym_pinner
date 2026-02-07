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
        """Pin a CID to the local Kubo node via gateway-fetch pipeline.

        Pintheon nodes run private IPFS swarms, so DHT resolution won't work
        for fresh content. Instead we:
        1. Fetch content bytes from the publisher's HTTPS gateway
        2. Add to local Kubo via /api/v0/add (with matching chunker params)
        3. Verify the returned CID matches the expected CID
        4. Pin locally (instant since blocks are already present)
        """
        log.info("Pinning CID %s via gateway %s", cid, gateway)
        start = time.monotonic()

        # Step 1: Fetch content from the publisher's gateway
        gateway_url = f"{gateway.rstrip('/')}/ipfs/{cid}"
        content_bytes: bytes | None = None

        for attempt in range(1, self._fetch_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self._pin_timeout, connect=10),
                    follow_redirects=True,
                ) as client:
                    async with client.stream("GET", gateway_url) as resp:
                        resp.raise_for_status()

                        # Check Content-Length before downloading body
                        content_length = resp.headers.get("content-length")
                        if content_length and int(content_length) > self._max_content_size:
                            duration = int((time.monotonic() - start) * 1000)
                            return PinResult(
                                success=False,
                                cid=cid,
                                error=f"content too large: {content_length} bytes "
                                      f"(max {self._max_content_size})",
                                duration_ms=duration,
                            )

                        # Read body bytes
                        chunks = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > self._max_content_size:
                                duration = int((time.monotonic() - start) * 1000)
                                return PinResult(
                                    success=False,
                                    cid=cid,
                                    error=f"content exceeded max size during download "
                                          f"(>{self._max_content_size} bytes)",
                                    duration_ms=duration,
                                )
                            chunks.append(chunk)
                        content_bytes = b"".join(chunks)

                log.info(
                    "Fetched %d bytes from gateway (attempt %d)", len(content_bytes), attempt,
                )
                break  # Success

            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TimeoutException) or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
                )
                if retryable and attempt < self._fetch_retries:
                    log.warning(
                        "Gateway fetch failed for %s (attempt %d/%d): %s",
                        cid, attempt, self._fetch_retries, exc,
                    )
                    continue
                duration = int((time.monotonic() - start) * 1000)
                error_msg = str(exc)
                if isinstance(exc, httpx.HTTPStatusError):
                    error_msg = f"gateway HTTP {exc.response.status_code}"
                else:
                    error_msg = f"gateway timeout after {self._fetch_retries} attempts"
                log.error("Gateway fetch failed for %s: %s", cid, error_msg)
                return PinResult(
                    success=False, cid=cid, error=error_msg, duration_ms=duration,
                )

            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                log.error("Gateway fetch error for %s: %s", cid, exc)
                return PinResult(
                    success=False, cid=cid, error=str(exc), duration_ms=duration,
                )

        if content_bytes is None:
            duration = int((time.monotonic() - start) * 1000)
            return PinResult(
                success=False, cid=cid, error="gateway fetch failed", duration_ms=duration,
            )

        # Step 2: Add content to local Kubo via /api/v0/add
        try:
            async with httpx.AsyncClient(timeout=self._pin_timeout) as client:
                resp = await client.post(
                    self._url("add"),
                    params={
                        "wrap-with-directory": "false",
                        "chunker": "size-262144",
                        "raw-leaves": "false",
                        "cid-version": "0",
                        "hash": "sha2-256",
                        "pin": "false",  # We pin explicitly in step 3
                    },
                    files={"file": ("data", content_bytes)},
                )
                resp.raise_for_status()
                add_data = resp.json()
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            log.error("Kubo add failed for %s: %s", cid, exc)
            return PinResult(
                success=False, cid=cid, error=f"kubo_add: {exc}", duration_ms=duration,
            )

        # CID verification
        returned_cid = add_data.get("Hash", "")
        if returned_cid != cid:
            duration = int((time.monotonic() - start) * 1000)
            log.error(
                "CID mismatch for %s: Kubo returned %s", cid, returned_cid,
            )
            return PinResult(
                success=False,
                cid=cid,
                error=f"cid_mismatch: expected {cid}, got {returned_cid}",
                duration_ms=duration,
            )

        bytes_pinned = int(add_data.get("Size", 0)) or None

        # Step 3: Pin locally (instant since blocks are now in the blockstore)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self._url("pin/add"),
                    params={"arg": cid},
                )
                resp.raise_for_status()
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            log.error("Local pin failed for %s: %s", cid, exc)
            return PinResult(
                success=False, cid=cid, error=f"local_pin: {exc}", duration_ms=duration,
            )

        duration = int((time.monotonic() - start) * 1000)
        log.info("Pinned %s (%s bytes) in %dms", cid, bytes_pinned or "?", duration)
        return PinResult(
            success=True,
            cid=cid,
            bytes_pinned=bytes_pinned,
            duration_ms=duration,
        )

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
