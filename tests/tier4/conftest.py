"""Tier 4 fixtures: end-to-end tests with real Kubo + real Stellar testnet.

Combines Tier 2 (Kubo/gateway) and Tier 3 (Stellar testnet) infrastructure
to test the full pipeline: gateway fetch -> IPFS pin -> on-chain claim.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from aiohttp import web
from stellar_sdk import Keypair

from hvym_pinner.bindings.hvym_pin_service import ClientAsync
from hvym_pinner.ipfs.executor import KuboPinExecutor
from hvym_pinner.stellar.queries import ContractQueries
from hvym_pinner.stellar.submitter import SorobanClaimSubmitter
from tests.conftest import (
    CONTRACT_ID,
    EXPLORER_BASE,
    TEST_PUBLIC,
    TEST_SECRET,
    make_test_config,
    tx_link,
)

# ── Account secrets (from .env) ──────────────────────────────────

PINNER_A_SECRET = os.environ.get(
    "PINNER_A_SECRET", "SATQCJDE7ATYC3GDIRDH2NIAZRPFUONXHTKTMAUEYDUCDPAUPTDUF253"
)
PINNER_A_PUBLIC = os.environ.get(
    "PINNER_A_PUBLIC", "GB3MQVDOI6JGPQQF5IFXZPOTREQEDXDRJG2AXEMUTCOOYM7TO3R3UBGS"
)

RPC_URL = "https://soroban-testnet.stellar.org"
NETWORK_PASSPHRASE = "Test SDF Network ; September 2015"
FRIENDBOT_URL = "https://friendbot.stellar.org"

FAKE_GATEWAY_PORT = 9299


# ── Timing infrastructure (reused from Tier 3 pattern) ───────────


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
    """Async context manager that records duration of the wrapped block."""
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


# ── Gate fixtures (session-scoped) ───────────────────────────────


@pytest.fixture(scope="session")
def kubo_available():
    """Check if local Kubo daemon is running."""
    try:
        r = httpx.post("http://127.0.0.1:5001/api/v0/id", timeout=3)
        if r.status_code == 200:
            return True
        pytest.skip("Kubo daemon not available at localhost:5001")
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("Kubo daemon not available at localhost:5001")


@pytest.fixture(scope="session")
def testnet_reachable():
    """Gate: skip all e2e tests if Stellar testnet RPC is unreachable."""
    try:
        r = httpx.post(
            RPC_URL,
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
def e2e_available(kubo_available, testnet_reachable):
    """Gate: skip all Tier 4 tests if either Kubo or testnet is down."""
    return True


# ── Account fixtures (session-scoped) ────────────────────────────


@pytest.fixture(scope="session")
def publisher_keypair():
    return Keypair.from_secret(TEST_SECRET)


@pytest.fixture(scope="session")
def pinner_a_keypair():
    return Keypair.from_secret(PINNER_A_SECRET)


@pytest.fixture(scope="session")
def funded_accounts(e2e_available, pinner_a_keypair):
    """Fund pinner A via Friendbot (idempotent)."""
    try:
        r = httpx.get(f"{FRIENDBOT_URL}?addr={pinner_a_keypair.public_key}", timeout=30)
        if r.status_code not in (200, 400):
            pytest.skip(f"Friendbot returned {r.status_code} for {pinner_a_keypair.public_key}")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        pytest.skip(f"Friendbot unreachable: {exc}")
    return True


@pytest.fixture(scope="session")
async def cleared_slots(e2e_available, publisher_keypair):
    """Cancel all occupied slots to ensure a clean slate."""
    client = make_client()
    try:
        cancelled = 0
        for i in range(10):
            try:
                tx = await client.get_slot(i)
                await tx.simulate()
                slot = tx.result()
                if slot is None:
                    continue
                tx = await client.cancel_pin(
                    caller=publisher_keypair.public_key,
                    slot_id=i,
                    source=publisher_keypair.public_key,
                    signer=publisher_keypair,
                )
                await tx.sign_and_submit()
                cancelled += 1
            except Exception:
                pass
        if cancelled:
            print(f"\n[tier4 setup] Cancelled {cancelled} stale slots")
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
    return True


@pytest.fixture(scope="session")
async def registered_pinners(cleared_slots, funded_accounts, pinner_a_keypair):
    """Ensure Pinner A is registered. Idempotent across re-runs."""
    client = make_client()
    try:
        tx = await client.is_pinner(pinner_a_keypair.public_key)
        await tx.simulate()
        already = tx.result()
        if already:
            return True

        tx = await client.join_as_pinner(
            caller=pinner_a_keypair.public_key,
            node_id=b"QmFakePinnerANodeId12345",
            multiaddr=b"/ip4/127.0.0.1/tcp/4001/p2p/QmFakePinnerA",
            min_price=1_000_000,
            source=pinner_a_keypair.public_key,
            signer=pinner_a_keypair,
        )
        await tx.sign_and_submit()
        print("\n[tier4 setup] Registered Pinner A")
    finally:
        try:
            await client.server.close()
        except Exception:
            pass
    return True


# ── Content fixtures (function-scoped) ───────────────────────────


@pytest.fixture
async def test_content(e2e_available):
    """Add synthetic bytes to Kubo, discover CID, then unpin.

    Yields (cid, content_bytes). Content is NOT pinned - the executor
    must go through the full gateway-fetch -> add -> pin pipeline.
    Each test gets unique content to avoid CID collisions.
    """
    content = f"hvym-pinner-e2e-{uuid.uuid4().hex}".encode("utf-8")
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
async def fake_gateway(test_content):
    """Local HTTP server that serves test content at /ipfs/{cid}.

    Simulates the Pintheon gateway. Returns (base_url, cid, content_bytes).
    """
    cid, content = test_content
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
    site = web.TCPSite(runner, "127.0.0.1", FAKE_GATEWAY_PORT)
    await site.start()
    yield f"http://127.0.0.1:{FAKE_GATEWAY_PORT}", cid, content
    await runner.cleanup()


# ── Component fixtures (function-scoped) ─────────────────────────


@pytest.fixture
def real_executor(e2e_available):
    """Real KuboPinExecutor for E2E tests."""
    cfg = make_test_config()
    return KuboPinExecutor(
        kubo_rpc_url=cfg.kubo_rpc_url,
        pin_timeout=cfg.pin_timeout,
        max_content_size=cfg.max_content_size,
        fetch_retries=cfg.fetch_retries,
    )


@pytest.fixture
def real_submitter(e2e_available, pinner_a_keypair):
    """Real SorobanClaimSubmitter for E2E tests (Pinner A's keypair)."""
    return SorobanClaimSubmitter(
        contract_id=CONTRACT_ID,
        rpc_url=RPC_URL,
        network_passphrase=NETWORK_PASSPHRASE,
        keypair=pinner_a_keypair,
    )


@pytest.fixture
def real_queries(e2e_available):
    """Real ContractQueries for E2E tests."""
    return ContractQueries(
        contract_id=CONTRACT_ID,
        rpc_url=RPC_URL,
        network_passphrase=NETWORK_PASSPHRASE,
    )


@pytest.fixture
def timing(request):
    """Per-test TimingCollector. Attaches to the test item for report hook."""
    tc = TimingCollector()
    request.node._timing = tc
    yield tc
    if tc.records:
        print(f"\n--- Timing: {request.node.name} ---")
        print(tc.summary())


@pytest.fixture(scope="session")
def tier4_state():
    """Session-wide state tracker for cleanup.

    Tests append slot_ids and cids they create so cleanup can handle them.
    """
    return {"slot_ids": [], "cids_pinned": []}


# ── Helpers ──────────────────────────────────────────────────────


def make_client(keypair: Keypair | None = None) -> ClientAsync:
    """Create a ClientAsync for the pin service contract."""
    return ClientAsync(
        contract_id=CONTRACT_ID,
        rpc_url=RPC_URL,
        network_passphrase=NETWORK_PASSPHRASE,
    )


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
