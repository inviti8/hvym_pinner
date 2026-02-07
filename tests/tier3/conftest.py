"""Tier 3 fixtures: real Stellar testnet stress tests.

Provides timing infrastructure, account management (Friendbot funding,
pinner registration), and reusable contract client helpers.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from stellar_sdk import Keypair

from hvym_pinner.bindings.hvym_pin_service import ClientAsync
from tests.conftest import (
    CONTRACT_ID,
    EXPLORER_BASE,
    TEST_PUBLIC,
    TEST_SECRET,
    tx_link,
)

# ── Account secrets (from .env) ──────────────────────────────────

PINNER_A_SECRET = os.environ.get(
    "PINNER_A_SECRET", "SATQCJDE7ATYC3GDIRDH2NIAZRPFUONXHTKTMAUEYDUCDPAUPTDUF253"
)
PINNER_A_PUBLIC = os.environ.get(
    "PINNER_A_PUBLIC", "GB3MQVDOI6JGPQQF5IFXZPOTREQEDXDRJG2AXEMUTCOOYM7TO3R3UBGS"
)
PINNER_B_SECRET = os.environ.get(
    "PINNER_B_SECRET", "SB6AJGM6SDXHFY3HGN7OH3DCJOH5NR2YMUIEF2D4KDMREZHLFZSNXTQD"
)
PINNER_B_PUBLIC = os.environ.get(
    "PINNER_B_PUBLIC", "GDCK7OQBNBJISP3ZLFDAT5D7KFDRQJCXCFCJ2AV2EMX4GUOQ7JAW6AZM"
)

RPC_URL = "https://soroban-testnet.stellar.org"
NETWORK_PASSPHRASE = "Test SDF Network ; September 2015"
FRIENDBOT_URL = "https://friendbot.stellar.org"

STROOPS_PER_XLM = 10_000_000


# ── Timing infrastructure ────────────────────────────────────────


@dataclass
class TimingRecord:
    """Single timed operation."""

    operation: str
    duration_s: float
    tx_hash: str | None = None
    result: str = ""


@dataclass
class TimingCollector:
    """Accumulates timing records for a single test."""

    records: list[TimingRecord] = field(default_factory=list)

    def add(
        self,
        operation: str,
        duration_s: float,
        tx_hash: str | None = None,
        result: str = "",
    ) -> None:
        self.records.append(TimingRecord(operation, duration_s, tx_hash, result))

    def to_html(self) -> str:
        """Render as an HTML table for pytest-html."""
        if not self.records:
            return ""
        rows = []
        for r in self.records:
            dur = f"{r.duration_s:.3f}s" if r.duration_s >= 1 else f"{r.duration_s * 1000:.0f}ms"
            hash_cell = tx_link(r.tx_hash) if r.tx_hash else "-"
            rows.append(
                f"<tr><td>{r.operation}</td><td>{dur}</td>"
                f"<td>{hash_cell}</td><td>{r.result}</td></tr>"
            )
        return (
            '<table border="1" cellpadding="4" cellspacing="0" '
            'style="border-collapse:collapse;font-family:monospace;font-size:12px;margin:8px 0;">'
            "<tr><th>Operation</th><th>Duration</th><th>TX Hash</th><th>Result</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    def summary(self) -> str:
        """Plain-text summary for console output."""
        lines = []
        for r in self.records:
            dur = f"{r.duration_s:.3f}s" if r.duration_s >= 1 else f"{r.duration_s * 1000:.0f}ms"
            tx = r.tx_hash[:12] + "..." if r.tx_hash else "-"
            lines.append(f"  {r.operation:<40} {dur:>8}  tx={tx}  {r.result}")
        return "\n".join(lines)


@asynccontextmanager
async def timed_op(timing: TimingCollector, label: str):
    """Async context manager that records duration of the wrapped block.

    Usage:
        async with timed_op(timing, "create_pin") as rec:
            result = await tx.sign_and_submit()
            rec["tx_hash"] = tx.send_transaction_response.hash
            rec["result"] = f"slot_id={result}"
    """
    rec: dict = {"tx_hash": None, "result": ""}
    start = time.perf_counter()
    try:
        yield rec
    finally:
        elapsed = time.perf_counter() - start
        timing.add(label, elapsed, rec.get("tx_hash"), rec.get("result", ""))


# ── pytest-html hook: inject timing tables into report ───────────


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        timing: TimingCollector | None = getattr(item, "_timing", None)
        if timing and timing.records:
            from pytest_html.extras import html as html_extra
            extra = getattr(report, "extras", [])
            extra.append(html_extra(timing.to_html()))
            report.extras = extra


# ── Session-scoped fixtures ──────────────────────────────────────


@pytest.fixture(scope="session")
def testnet_reachable():
    """Gate: skip all tier3 tests if Stellar testnet RPC is unreachable."""
    try:
        r = httpx.post(
            f"{RPC_URL}",
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            timeout=10,
        )
        data = r.json()
        if data.get("result", {}).get("status") == "healthy":
            return True
        pytest.skip(f"Stellar testnet RPC not healthy: {data}")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        pytest.skip(f"Stellar testnet RPC unreachable: {exc}")


@pytest.fixture(scope="session")
def publisher_keypair():
    return Keypair.from_secret(TEST_SECRET)


@pytest.fixture(scope="session")
def pinner_a_keypair():
    return Keypair.from_secret(PINNER_A_SECRET)


@pytest.fixture(scope="session")
def pinner_b_keypair():
    return Keypair.from_secret(PINNER_B_SECRET)


@pytest.fixture(scope="session")
def keypairs(publisher_keypair, pinner_a_keypair, pinner_b_keypair):
    return {
        "publisher": publisher_keypair,
        "pinner_a": pinner_a_keypair,
        "pinner_b": pinner_b_keypair,
    }


@pytest.fixture(scope="session")
def funded_accounts(testnet_reachable, pinner_a_keypair, pinner_b_keypair):
    """Fund pinner A and B via Friendbot (idempotent)."""
    for kp in [pinner_a_keypair, pinner_b_keypair]:
        try:
            r = httpx.get(f"{FRIENDBOT_URL}?addr={kp.public_key}", timeout=30)
            if r.status_code == 200:
                pass  # funded
            elif r.status_code == 400:
                pass  # already funded
            else:
                pytest.skip(f"Friendbot returned {r.status_code} for {kp.public_key}")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            pytest.skip(f"Friendbot unreachable: {exc}")
    return True


def make_client(keypair: Keypair | None = None) -> ClientAsync:
    """Create a ClientAsync for the pin service contract.

    Each account needs its own client instance because sequence numbers
    are per source account.
    """
    return ClientAsync(
        contract_id=CONTRACT_ID,
        rpc_url=RPC_URL,
        network_passphrase=NETWORK_PASSPHRASE,
    )


@pytest.fixture(scope="session")
async def cleared_slots(testnet_reachable, publisher_keypair):
    """Cancel all occupied slots to ensure a clean slate.

    The contract has only NUM_SLOTS=10 slots. Previous test runs may have
    left slots occupied. This fixture cancels any remaining slots so the
    test suite starts fresh.
    """
    client = make_client()
    try:
        cancelled = 0
        for i in range(10):
            try:
                # Check if slot exists
                tx = await client.get_slot(i)
                await tx.simulate()
                slot = tx.result()
                if slot is None:
                    continue
                # Cancel it
                tx = await client.cancel_pin(
                    caller=publisher_keypair.public_key,
                    slot_id=i,
                    source=publisher_keypair.public_key,
                    signer=publisher_keypair,
                )
                await tx.sign_and_submit()
                cancelled += 1
            except Exception:
                pass  # Slot empty or already cancelled
        if cancelled:
            print(f"\n[tier3 setup] Cancelled {cancelled} stale slots")
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
    return True


@pytest.fixture(scope="session")
async def registered_pinners(cleared_slots, funded_accounts, pinner_a_keypair, pinner_b_keypair):
    """Ensure both pinners are registered. Idempotent across re-runs."""
    for kp, label in [(pinner_a_keypair, "pinner_a"), (pinner_b_keypair, "pinner_b")]:
        client = make_client()
        try:
            # Check if already registered
            tx = await client.is_pinner(kp.public_key)
            await tx.simulate()
            already = tx.result()
            if already:
                continue

            # Register
            tx = await client.join_as_pinner(
                caller=kp.public_key,
                node_id=f"QmFake{label}NodeId12345".encode("utf-8"),
                multiaddr=f"/ip4/127.0.0.1/tcp/4001/p2p/QmFake{label}".encode("utf-8"),
                min_price=1_000_000,  # 0.1 XLM
                source=kp.public_key,
                signer=kp,
            )
            await tx.sign_and_submit()
        finally:
            try:
                await client.server.close()
            except Exception:
                pass
    return True


# ── Module / function scoped fixtures ────────────────────────────


@pytest.fixture(scope="module")
def contract_client(testnet_reachable):
    """Module-scoped ClientAsync for the pin service."""
    client = make_client()
    yield client
    # Cleanup is best-effort
    try:
        import asyncio
        asyncio.get_event_loop().run_until_complete(client.server.close())
    except Exception:
        pass


@pytest.fixture
def timing(request):
    """Per-test TimingCollector. Attaches to the test item for report hook."""
    tc = TimingCollector()
    request.node._timing = tc
    yield tc
    # Print summary to console in verbose mode
    if tc.records:
        print(f"\n--- Timing: {request.node.name} ---")
        print(tc.summary())


@pytest.fixture(scope="session")
def tier3_state():
    """Session-wide state tracker for cleanup.

    Tests append slot_ids they create so cleanup can cancel them.
    """
    return {"slot_ids": [], "pinners_registered": []}


# ── Helpers ──────────────────────────────────────────────────────


async def create_test_slot(
    client: ClientAsync,
    publisher_kp: Keypair,
    timing: TimingCollector,
    *,
    cid: str = "QmTestCid000000000000000000000000000000000000000",
    filename: str = "test.bin",
    gateway: str = "https://pintheon.xyz",
    offer_price: int = 10_000_000,
    pin_qty: int = 3,
    label: str = "create_pin",
) -> int:
    """Create a pin slot and record timing. Returns slot_id."""
    async with timed_op(timing, label) as rec:
        tx = await client.create_pin(
            caller=publisher_kp.public_key,
            cid=cid.encode("utf-8"),
            filename=filename.encode("utf-8"),
            gateway=gateway.encode("utf-8"),
            offer_price=offer_price,
            pin_qty=pin_qty,
            source=publisher_kp.public_key,
            signer=publisher_kp,
        )
        slot_id = await tx.sign_and_submit()
        rec["tx_hash"] = tx.send_transaction_response.hash if tx.send_transaction_response else None
        rec["result"] = f"slot_id={slot_id}"
    return slot_id


async def simulate_query(client: ClientAsync, method_name: str, *args) -> object:
    """Call a read-only contract method via simulate(). Returns result."""
    method = getattr(client, method_name)
    tx = await method(*args)
    await tx.simulate()
    return tx.result()
