"""Tests 24-27: Gateway HTTP errors, CID mismatch, size limit, timeouts."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from hvym_pinner.ipfs.executor import KuboPinExecutor
from tests.conftest import make_test_config


# ── Test 24: Gateway HTTP 404 ─────────────────────────────────────


@pytest.mark.kubo
async def test_error_gateway_http_error(real_executor, kubo_available):
    """Gateway returns 404 → PinResult(success=False, error contains '404')."""
    # Use a server that returns 404 for everything
    async def handle_404(request):
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/ipfs/{cid}", handle_404)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9200)
    await site.start()

    try:
        result = await real_executor.pin("QmNonExistent", "http://127.0.0.1:9200")
        assert not result.success
        assert "404" in (result.error or "")
    finally:
        await runner.cleanup()


# ── Test 25: CID mismatch ────────────────────────────────────────


@pytest.mark.kubo
async def test_error_cid_mismatch(real_executor, kubo_available):
    """Gateway serves wrong content → cid_mismatch error."""
    wrong_content = b"this is totally wrong content"

    async def handle_wrong(request):
        return web.Response(body=wrong_content)

    app = web.Application()
    app.router.add_get("/ipfs/{cid}", handle_wrong)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9201)
    await site.start()

    try:
        # Use a known CID that won't match the wrong content
        result = await real_executor.pin("QmExpectedCIDThatWontMatch", "http://127.0.0.1:9201")
        assert not result.success
        assert "cid_mismatch" in (result.error or "")
    finally:
        await runner.cleanup()


# ── Test 26: Content too large ────────────────────────────────────


@pytest.mark.kubo
async def test_error_content_too_large(kubo_available):
    """Content-Length exceeds max_content_size → error before full download."""
    large_content = b"x" * 2048

    async def handle_large(request):
        return web.Response(
            body=large_content,
            headers={"Content-Length": str(len(large_content))},
        )

    app = web.Application()
    app.router.add_get("/ipfs/{cid}", handle_large)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9202)
    await site.start()

    try:
        # Create an executor with very small max_content_size
        executor = KuboPinExecutor(
            kubo_rpc_url="http://127.0.0.1:5001",
            pin_timeout=5,
            max_content_size=1024,  # smaller than content
            fetch_retries=1,
        )
        result = await executor.pin("QmSomeCID", "http://127.0.0.1:9202")
        assert not result.success
        assert "content" in (result.error or "").lower()
        assert "large" in (result.error or "").lower() or "size" in (result.error or "").lower()
    finally:
        await runner.cleanup()


# ── Test 27: Gateway timeout with retries ─────────────────────────


@pytest.mark.kubo
async def test_error_gateway_timeout_retries(kubo_available):
    """Gateway times out → retries up to fetch_retries, then fails."""
    call_count = 0

    async def handle_slow(request):
        nonlocal call_count
        call_count += 1
        # Sleep longer than the executor's timeout
        await asyncio.sleep(10)
        return web.Response(body=b"too late")

    app = web.Application()
    app.router.add_get("/ipfs/{cid}", handle_slow)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9203)
    await site.start()

    try:
        executor = KuboPinExecutor(
            kubo_rpc_url="http://127.0.0.1:5001",
            pin_timeout=1,  # very short timeout
            max_content_size=10_000_000,
            fetch_retries=2,
        )
        result = await executor.pin("QmTimeoutCID", "http://127.0.0.1:9203")
        assert not result.success
        assert "timeout" in (result.error or "").lower()
        # Should have retried
        assert call_count >= 2
    finally:
        await runner.cleanup()
