"""Test 2: Real Kubo pinning via gateway-fetch pipeline."""

from __future__ import annotations

import pytest


@pytest.mark.kubo
async def test_auto_mode_with_real_kubo(real_executor, surrogate_content, gateway_server):
    """Full gateway-fetch -> add -> CID verify -> pin with real Kubo."""
    cid, content = surrogate_content
    gateway_url, _ = gateway_server

    result = await real_executor.pin(cid, gateway_url)

    assert result.success
    assert result.cid == cid
    assert result.bytes_pinned is not None
    assert await real_executor.verify_pinned(cid)
