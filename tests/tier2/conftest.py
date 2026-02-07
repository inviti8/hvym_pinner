"""Tier 2 fixtures: real Kubo + local gateway server."""

from __future__ import annotations

import pytest
import httpx
from aiohttp import web

from hvym_pinner.ipfs.executor import KuboPinExecutor
from tests.conftest import make_test_config


@pytest.fixture(scope="session")
def kubo_available():
    """Check if local Kubo daemon is running. Skip tier2 tests if not."""
    try:
        r = httpx.post("http://127.0.0.1:5001/api/v0/id", timeout=3)
        if r.status_code == 200:
            return True
        pytest.skip("Kubo daemon not available at localhost:5001")
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("Kubo daemon not available at localhost:5001")


@pytest.fixture
async def surrogate_content(kubo_available):
    """Add surrogate content to Kubo, discover CID, then unpin.

    Returns (cid, content_bytes). Content is NOT pinned - the executor
    must go through the full gateway-fetch -> add -> pin pipeline.
    """
    content = b"hvym-pinner-test-alpha"
    async with httpx.AsyncClient() as client:
        # Add to discover CID
        resp = await client.post(
            "http://127.0.0.1:5001/api/v0/add",
            files={"file": ("test.txt", content)},
        )
        cid = resp.json()["Hash"]
        # Unpin so it's not already pinned
        await client.post(
            "http://127.0.0.1:5001/api/v0/pin/rm",
            params={"arg": cid},
        )
        yield cid, content
        # Teardown: clean up pin if test pinned it
        await client.post(
            "http://127.0.0.1:5001/api/v0/pin/rm",
            params={"arg": cid},
        )


@pytest.fixture
async def gateway_server(surrogate_content):
    """Local HTTP server that serves surrogate content at /ipfs/{cid}.

    Returns (base_url, content_map). The executor fetches {base_url}/ipfs/{cid}.
    """
    cid, content = surrogate_content
    content_map = {cid: content}

    async def handle_ipfs(request):
        req_cid = request.match_info["cid"]
        if req_cid in content_map:
            return web.Response(body=content_map[req_cid])
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/ipfs/{cid}", handle_ipfs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9199)
    await site.start()
    yield "http://127.0.0.1:9199", content_map
    await runner.cleanup()


@pytest.fixture
def real_executor(kubo_available):
    """Real KuboPinExecutor for Tier 2 tests."""
    cfg = make_test_config()
    return KuboPinExecutor(
        kubo_rpc_url=cfg.kubo_rpc_url,
        pin_timeout=cfg.pin_timeout,
        max_content_size=cfg.max_content_size,
        fetch_retries=cfg.fetch_retries,
    )
