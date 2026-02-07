"""Tier 3 gate tests: RPC health and contract config readability."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.tier3.conftest import RPC_URL, simulate_query, make_client, timed_op

pytestmark = pytest.mark.testnet


async def test_rpc_health(testnet_reachable, timing):
    """Verify RPC endpoint responds and measure latency."""
    async with timed_op(timing, "getHealth") as rec:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                RPC_URL,
                json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
                timeout=10,
            )
        data = resp.json()
        rec["result"] = data.get("result", {}).get("status", "unknown")

    assert data["result"]["status"] == "healthy"


async def test_contract_config_readable(testnet_reachable, timing):
    """Read all config values from the contract and verify known constants."""
    client = make_client()
    try:
        queries = [
            ("pin_fee", "pin_fee"),
            ("join_fee", "join_fee"),
            ("pinner_stake_amount", "pinner_stake_amount"),
            ("min_pin_qty", "min_pin_qty"),
            ("min_offer_price", "min_offer_price"),
            ("get_pinner_count", "get_pinner_count"),
        ]
        results = {}
        for label, method in queries:
            async with timed_op(timing, label) as rec:
                val = await simulate_query(client, method)
                rec["result"] = str(val)
                results[label] = val

        # These are known contract constants (stroops)
        assert results["pin_fee"] > 0, "pin_fee should be positive"
        assert results["join_fee"] > 0, "join_fee should be positive"
        assert results["pinner_stake_amount"] > 0, "stake should be positive"
        assert results["min_pin_qty"] >= 1, "min_pin_qty should be >= 1"
        assert results["min_offer_price"] > 0, "min_offer_price should be positive"
        assert results["get_pinner_count"] >= 0, "pinner_count should be >= 0"
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
