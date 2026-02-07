# FLOW_TEST.md — End-to-End Flow Test Plan

## 1. Overview

### Purpose

Define a comprehensive test plan for the `hvym_pinner` daemon covering the full PIN→pin→claim lifecycle, approval workflows, error recovery, CID Hunter verification cycles, and Data API snapshot correctness.

### What's Tested

- **Core flow**: PIN event → policy filter → Kubo pin → Soroban claim → state persistence
- **Rejection paths**: Price, balance, slot state, profitability filters
- **Error recovery**: Pin timeouts, claim failures (AlreadyClaimed, SlotExpired, NotPinner)
- **Event lifecycle**: PIN → PINNED → UNPIN state transitions
- **Approve mode**: Queue management, approve/reject, mode switching
- **CID Hunter**: Track → verify → suspect → flag lifecycle
- **Data API**: Dashboard snapshots, earnings, approval queue, hunter summaries
- **Cursor persistence**: Resume from last-seen ledger after restart

### Out of Scope

- Live testnet integration (real Stellar/IPFS networks)
- CLI command parsing (click has its own testing patterns)
- Contract binding correctness (tested in pintheon_contracts)
- Performance/load testing
- Frontend client behavior

---

## 2. Test Architecture (Two Tiers)

### Tier 1: Mock-Only Fast Tests (CI-Safe)

**Marker**: `@pytest.mark.tier1` (default — runs without markers too)

No external infrastructure required. All network-facing components are replaced with mock objects or in-memory substitutes.

| Component | Mock Strategy |
|-----------|--------------|
| `SorobanEventPoller` | Returns synthetic `PinEvent`/`PinnedEvent`/`UnpinEvent` lists |
| `PolicyOfferFilter` | Returns pre-built `FilterResult` (or real impl with mocked `ContractQueries`) |
| `KuboPinExecutor` | Returns `PinResult(success=True/False, ...)` |
| `SorobanClaimSubmitter` | Returns `ClaimResult(success=True/False, ...)` |
| `ContractQueries` | Returns synthetic `SlotInfo`/`PinnerData`/balances |
| `SQLiteStateStore` | **Real implementation** with `:memory:` SQLite (fast, no mocking needed) |
| `DaemonModeController` | Real implementation (pure in-memory, no I/O) |
| `DataAggregator` | Real implementation wired to mocked components |
| `KuboPinVerifier` | Returns synthetic `VerificationResult` |
| `SorobanFlagSubmitter` | Returns synthetic `FlagResult` |
| `PinnerRegistryCacheImpl` | Real implementation with mocked `ContractQueries` |

**Why real SQLite**: `aiosqlite` supports `:memory:` databases. The store is the source of truth for all state — using the real implementation catches serialization bugs, query errors, and schema mismatches that mocks would hide.

**Why real ModeController**: It's a trivial in-memory wrapper. Mocking it adds complexity with no benefit.

### Tier 2: Kubo Integration Tests (Requires Local Kubo)

**Marker**: `@pytest.mark.kubo`

Requires a running Kubo daemon at `localhost:5001`. Tests real IPFS pinning with surrogate content. Stellar components remain mocked.

| Component | Strategy |
|-----------|---------|
| `KuboPinExecutor` | **Real implementation** hitting local Kubo |
| `KuboPinVerifier` | **Real implementation** hitting local Kubo (DHT/Bitswap against local node) |
| Everything else | Same as Tier 1 |

**Skip logic**: Tests auto-skip if Kubo is unreachable (checked via fixture).

---

## 3. Surrogate Content Strategy

### Why We Need Surrogate CIDs

The daemon pins content by CID. Tests need real CIDs that correspond to actual content — but we don't need real publisher data or gateways.

### How It Works

1. **Add test files to local Kubo** via `POST /api/v0/add`:
   ```
   POST http://127.0.0.1:5001/api/v0/add
   Content-Type: multipart/form-data
   file=<test content bytes>
   ```
   Returns: `{"Hash": "QmXyz...", "Size": "42"}`

2. **Use the returned CID** in synthetic `PinEvent` objects.

3. **Test files**: Small deterministic payloads:
   - `b"hvym-pinner-test-alpha"` → consistent CID across runs
   - `b"hvym-pinner-test-beta"` → second CID for multi-pin scenarios
   - `b"\x00" * 1024` → 1KB binary blob for size checks

### Why the Gateway Field Doesn't Matter

`KuboPinExecutor.pin()` calls:
```
POST /api/v0/pin/add?arg={cid}&progress=false
```

Kubo resolves the CID via DHT, **not** via the gateway URL. The `gateway` field from `PinEvent` is only logged — it never affects pin resolution. For Tier 2 tests the content is already local (we just added it), so `pin/add` finds it instantly.

For Tier 1 tests, the executor is mocked entirely — the gateway field is irrelevant.

### Cleanup

Tier 2 test fixtures unpin all surrogate CIDs in teardown via `POST /api/v0/pin/rm?arg={cid}`.

---

## 4. External Dependency Map

| # | Service | Protocol | Used By | APIs Called | Mock Approach |
|---|---------|----------|---------|-------------|---------------|
| 1 | **Soroban RPC** | JSON-RPC over HTTPS | `SorobanEventPoller` | `getEvents`, `getLatestLedger` | Mock at `SorobanServer` level — replace `poller` attribute on daemon |
| 2 | **Soroban Transactions** | JSON-RPC over HTTPS | `SorobanClaimSubmitter`, `SorobanFlagSubmitter`, `ContractQueries` | `simulateTransaction`, `sendTransaction`, contract simulations | Mock at component level — replace `submitter`/`queries` attributes |
| 3 | **Horizon API** | REST over HTTPS | `ContractQueries.get_wallet_balance()` | `GET /accounts/{addr}` | Mocked inside `ContractQueries` mock (returns int balance) |
| 4 | **Kubo IPFS RPC** | HTTP POST | `KuboPinExecutor`, `KuboPinVerifier` | `pin/add`, `pin/ls`, `pin/rm`, `object/stat`, `routing/findprovs`, `swarm/connect`, `block/get`, `cat` | **Tier 1**: Mock entire executor/verifier. **Tier 2**: Real calls to local Kubo |

---

## 5. Mock Harness Design

### 5.1 Synthetic Event Factories

Located in `tests/factories.py`:

```python
def make_pin_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    gateway: str = "https://ipfs.example.com",
    offer_price: int = 1_000_000,      # 0.1 XLM
    pin_qty: int = 3,
    publisher: str = "GABCDEF...",
    ledger_sequence: int = 100000,
) -> PinEvent: ...

def make_pinned_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    pinner: str = "GDNAG4K...",        # our test address
    amount: int = 1_000_000,
    pins_remaining: int = 2,
    ledger_sequence: int = 100001,
) -> PinnedEvent: ...

def make_unpin_event(
    slot_id: int = 1,
    cid: str = "QmTestCID123",
    ledger_sequence: int = 100002,
) -> UnpinEvent: ...
```

Note: `PinnedEvent` and `UnpinEvent` use `cid_hash` (SHA-256 hex of CID), not raw CID. The factory computes this automatically from the `cid` parameter.

### 5.2 Mock Component Classes

Located in `tests/mocks.py`:

```python
class MockPoller:
    """Implements EventPoller protocol. Returns pre-loaded event lists."""
    def __init__(self):
        self.events: list[ContractEvent] = []
        self._cursor: int | None = None

    async def poll(self) -> list[ContractEvent]:
        result = list(self.events)
        self.events.clear()
        return result

    def get_cursor(self) -> int | None:
        return self._cursor

    def set_cursor(self, cursor: str) -> None:
        self._cursor = int(cursor.split("-")[0])

    def enqueue(self, *events: ContractEvent) -> None:
        """Test helper: stage events for next poll."""
        self.events.extend(events)


class MockExecutor:
    """Implements PinExecutor protocol."""
    def __init__(self, succeed: bool = True, bytes_pinned: int = 1024):
        self.succeed = succeed
        self.bytes_pinned = bytes_pinned
        self.pinned_cids: set[str] = set()
        self.pin_calls: list[tuple[str, str]] = []

    async def pin(self, cid: str, gateway: str) -> PinResult: ...
    async def verify_pinned(self, cid: str) -> bool: ...
    async def unpin(self, cid: str) -> bool: ...


class MockSubmitter:
    """Implements ClaimSubmitter protocol."""
    def __init__(self, succeed: bool = True, tx_hash: str = "mock_tx_abc123"):
        self.succeed = succeed
        self.tx_hash = tx_hash
        self.error: str | None = None       # e.g. "already_claimed"
        self.claim_calls: list[int] = []    # slot_ids

    async def submit_claim(self, slot_id: int) -> ClaimResult: ...


class MockQueries:
    """Implements ContractQueries-like interface for filter tests."""
    def __init__(self, wallet_balance: int = 10_000_000, slot_active: bool = True):
        self.wallet_balance = wallet_balance
        self.slot_active = slot_active
        # ... configurable slot info, pinner data, etc.

    async def get_wallet_balance(self, address: str) -> int: ...
    async def is_slot_expired(self, slot_id: int) -> bool | None: ...
    async def get_slot(self, slot_id: int) -> SlotInfo | None: ...
    async def get_pinner(self, address: str) -> PinnerData | None: ...


class MockFlagSubmitter:
    """Implements FlagSubmitter protocol."""
    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.flag_calls: list[str] = []

    async def submit_flag(self, pinner_address: str) -> FlagResult: ...
    async def has_already_flagged(self, pinner_address: str) -> bool: ...


class MockVerifier:
    """Implements PinVerifier protocol."""
    def __init__(self, passed: bool = True):
        self.passed = passed

    async def verify(self, cid, pinner_node_id, pinner_multiaddr) -> VerificationResult: ...
```

All mocks record their calls for assertion (e.g. `executor.pin_calls`, `submitter.claim_calls`).

### 5.3 Test Daemon Wiring

The `PinnerDaemon` constructor creates real components internally, but they only store configuration — no network calls happen during `__init__`. The approach:

1. **Construct** `PinnerDaemon(cfg)` with a valid test config (including a valid Stellar keypair — `Keypair.from_secret()` is pure computation, no network).
2. **Replace** component attributes with mocks **before** calling any async methods:

```python
async def create_test_daemon(cfg, mocks):
    daemon = PinnerDaemon(cfg)
    # Replace with mocks before any I/O
    daemon.store = mocks.store          # real SQLiteStateStore(":memory:")
    daemon.poller = mocks.poller        # MockPoller
    daemon.executor = mocks.executor    # MockExecutor
    daemon.submitter = mocks.submitter  # MockSubmitter
    daemon.queries = mocks.queries      # MockQueries
    daemon.filter = PolicyOfferFilter(  # real filter with mocked queries
        queries=mocks.queries,
        our_address=cfg_address,
        min_price=cfg.min_price,
        max_content_size=cfg.max_content_size,
    )
    daemon.data_api = DataAggregator(   # real aggregator with mocked deps
        store=mocks.store,
        queries=mocks.queries,
        mode_ctrl=daemon.mode_ctrl,
        our_address=cfg_address,
        start_time=datetime.now(timezone.utc).isoformat(),
        hunter=daemon.hunter,
    )
    # Initialize store (creates tables in :memory:)
    await mocks.store.initialize()
    return daemon
```

### 5.4 Test Keypair

Tests use a real Stellar secret key (the constructor calls `Keypair.from_secret()` which validates format). Since all Stellar network calls are mocked, no funds are needed.

```python
TEST_SECRET = "SCZANGBA5YHTNYVVV3C7CAZMCLXPILHSE6PGYAY2TDGPMHGK5B55FOHM"
TEST_PUBLIC = "GDNAG4KFFVF5HCSGRWZIXZNL2SR2KBGJSHW2A6FI6DZI62XF6IBLO4GD"
```

(This is the existing testnet keypair from `.env` — it's safe for test configs since Stellar calls are mocked.)

### 5.5 Test Config Factory

```python
def make_test_config(**overrides) -> DaemonConfig:
    """Build a DaemonConfig suitable for testing."""
    defaults = dict(
        mode=DaemonMode.AUTO,
        poll_interval=1,
        error_backoff=1,
        rpc_url="https://soroban-testnet.stellar.org",
        network_passphrase="Test SDF Network ; September 2015",
        contract_id="CCEDYFIHUCJFITWEOT7BWUO2HBQQ72L244ZXQ4YNOC6FYRDN3MKDQFK7",
        factory_contract_id="CACBN6G2EPPLAQORDB3LXN3SULGVYBAETFZTNYTNDQ77B7JFRIBT66V2",
        keypair_secret=TEST_SECRET,
        kubo_rpc_url="http://127.0.0.1:5001",
        pin_timeout=5,
        max_content_size=10_000_000,
        min_price=100,
        db_path=":memory:",
        hunter=HunterConfig(enabled=False),
    )
    defaults.update(overrides)
    return DaemonConfig(**defaults)
```

---

## 6. Test Scenarios

### 6.1 Happy Path — Auto Mode PIN→Pin→Claim

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 1 | `test_auto_mode_full_lifecycle` | PIN event arrives → filter accepts → executor pins → submitter claims → offer status transitions: `pending` → `pinning` → `claiming` → `claimed`. Verify: `store.get_offers_by_status("claimed")` returns 1 record, `store.get_earnings()` reflects correct amount, activity log contains `offer_seen`, `pin_started`, `pin_success`, `claim_success` entries. | T1 |
| 2 | `test_auto_mode_with_real_kubo` | Same flow but with real `KuboPinExecutor` pinning surrogate content. Verify the CID is actually pinned via `pin/ls`. | T2 |

**Test approach**: Call `daemon._handle_pin_event(event)` directly. This is deterministic (no polling loop, no sleep).

### 6.2 Rejection Paths

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 3 | `test_reject_price_too_low` | `offer_price=50` with `min_price=100` → filter rejects → status `rejected`, reason `price_too_low`. Executor never called. | T1 |
| 4 | `test_reject_insufficient_balance` | `MockQueries.wallet_balance = 10_000` (below `ESTIMATED_TX_FEE * 2 = 200_000`) → rejected `insufficient_xlm`. | T1 |
| 5 | `test_reject_slot_expired` | `MockQueries.is_slot_expired = True` → rejected `slot_not_active`. | T1 |
| 6 | `test_reject_slot_filled` | `MockQueries.get_slot().pins_remaining = 0` → rejected `slot_not_active`. | T1 |
| 7 | `test_reject_unprofitable` | `offer_price=50_000` with `ESTIMATED_TX_FEE=100_000` → net profit ≤ 0 → rejected `unprofitable`. (Set `min_price=1` to pass price check first.) | T1 |

**Test approach**: Use real `PolicyOfferFilter` with `MockQueries`. Call `filter.evaluate(event)` or `daemon._handle_pin_event(event)`.

### 6.3 Error Paths

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 8 | `test_error_pin_timeout` | `MockExecutor` returns `PinResult(success=False, error="timeout")`. Verify: status → `pin_failed`, activity log contains error, claim never attempted. | T1 |
| 9 | `test_error_claim_already_claimed` | Pin succeeds, `MockSubmitter` returns `ClaimResult(success=False, error="already_claimed")`. Verify: pin saved, status → `claim_failed`, no earnings recorded. | T1 |
| 10 | `test_error_claim_slot_expired` | Same pattern with `error="slot_expired"`. | T1 |
| 11 | `test_error_claim_not_pinner` | Same pattern with `error="not_pinner"`. | T1 |

**Test approach**: Call `daemon._handle_pin_event(event)` with error-producing mocks.

### 6.4 Event Lifecycle (State Transitions)

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 12 | `test_pinned_event_updates_slot` | Send PIN event (auto-claim succeeds) → then PINNED event with `pins_remaining=0` → offer status updates to `filled`. | T1 |
| 13 | `test_unpin_event_expires_offer` | Send PIN event → then UNPIN event for same slot → offer status updates to `expired`, activity log contains `offer_expired`. | T1 |
| 14 | `test_pinned_event_from_other_pinner` | PINNED event where `pinner != our_address` and `pins_remaining > 0` → status stays as-is (no `filled`). Verifies we don't overreact to other pinners claiming. | T1 |

**Test approach**: Call `daemon._handle_pin_event()` then `daemon._handle_pinned_event()` / `daemon._handle_unpin_event()` in sequence.

### 6.5 Approve Mode

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 15 | `test_approve_mode_queues_offer` | Mode=APPROVE, PIN arrives → filter accepts → status set to `awaiting_approval` (not `pinning`). Executor/submitter never called. | T1 |
| 16 | `test_approve_then_execute` | Mode=APPROVE → offer queued → `data_api.approve_offers([slot_id])` → status `approved`. Next main loop iteration picks up approved offers → `_execute_pin_and_claim()` runs → `claimed`. | T1 |
| 17 | `test_reject_queued_offer` | Mode=APPROVE → offer queued → `data_api.reject_offers([slot_id])` → status `rejected`, reason `operator_rejected`. | T1 |
| 18 | `test_mode_switch_mid_flight` | Start in AUTO, process one event (claimed). Switch to APPROVE via `data_api.set_mode("approve")`. Next PIN event queues instead of auto-executing. Switch back to AUTO — verify queued offer doesn't auto-execute (must be explicitly approved first). | T1 |

**Test approach**: Tests 15/17 call `_handle_pin_event()` directly. Test 16 requires calling `_handle_pin_event()` then simulating the approved-offer processing from `_main_loop()`. Test 18 uses multiple sequential event handling calls with mode changes between them.

### 6.6 CID Hunter

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 19 | `test_hunter_track_verify_flag_lifecycle` | Enable hunter. PIN event (our CID) → PINNED event (other pinner) → tracked pin created → `MockVerifier` returns `passed=False` for 3 consecutive cycles → status transitions: `tracking` → `suspect` → auto-flag triggered → `flag_submitted`. Verify `FlagRecord` saved, `FlagResult` returned with tx hash. | T1 |
| 20 | `test_hunter_unpin_stops_tracking` | PIN → PINNED → tracked → UNPIN event → tracked pin status set to `slot_freed`. Future verification cycles skip it. | T1 |

**Test approach**: For test 19, wire `CIDHunterOrchestrator` with mock verifier/flag submitter, call event handlers, then `scheduler.run_cycle()` three times. For test 20, process events in sequence and verify state.

### 6.7 Concurrent Events, Persistence, Data API

| # | Test Name | Description | Tier |
|---|-----------|-------------|------|
| 21 | `test_concurrent_pin_events` | Poller returns 3 PIN events in one batch. All three go through filter → pin → claim. Verify all 3 offers in store, 3 claims recorded, earnings = sum of all 3. | T1 |
| 22 | `test_cursor_persistence_and_resume` | Process events, daemon stops. Create new daemon with same `:memory:` store (pass it in). Verify cursor restored, poller starts from last-seen ledger. | T1 |
| 23 | `test_dashboard_snapshot_accuracy` | Process a mix of events (1 claimed, 1 rejected, 1 awaiting). Call `data_api.get_dashboard()`. Verify all snapshot fields: `total_earned`, `active_offers`, `pending_approvals`, `pins_count`, `recent_activity` contains correct entries. | T1 |

---

## 7. Fixture Design

### 7.1 Shared Fixtures (`tests/conftest.py`)

```python
import pytest
from hvym_pinner.models.config import DaemonConfig, DaemonMode, HunterConfig
from hvym_pinner.storage.sqlite import SQLiteStateStore

TEST_SECRET = "SCZANGBA5YHTNYVVV3C7CAZMCLXPILHSE6PGYAY2TDGPMHGK5B55FOHM"
TEST_PUBLIC = "GDNAG4KFFVF5HCSGRWZIXZNL2SR2KBGJSHW2A6FI6DZI62XF6IBLO4GD"


@pytest.fixture
def test_config():
    """Default DaemonConfig for tests."""
    return make_test_config()


@pytest.fixture
async def store():
    """Initialized in-memory SQLiteStateStore."""
    s = SQLiteStateStore(":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def mock_poller():
    return MockPoller()


@pytest.fixture
def mock_executor():
    return MockExecutor(succeed=True)


@pytest.fixture
def mock_submitter():
    return MockSubmitter(succeed=True)


@pytest.fixture
def mock_queries():
    return MockQueries(wallet_balance=10_000_000, slot_active=True)


@pytest.fixture
async def daemon(test_config, store, mock_poller, mock_executor,
                 mock_submitter, mock_queries):
    """Fully wired PinnerDaemon with mocked components."""
    d = PinnerDaemon(test_config)
    d.store = store
    d.poller = mock_poller
    d.executor = mock_executor
    d.submitter = mock_submitter
    d.queries = mock_queries
    d.filter = PolicyOfferFilter(
        queries=mock_queries,
        our_address=TEST_PUBLIC,
        min_price=test_config.min_price,
        max_content_size=test_config.max_content_size,
    )
    d.mode_ctrl = DaemonModeController(store, DaemonMode.AUTO)
    d.data_api = DataAggregator(
        store=store, queries=mock_queries, mode_ctrl=d.mode_ctrl,
        our_address=TEST_PUBLIC, start_time="2025-01-01T00:00:00Z",
    )
    return d
```

### 7.2 Kubo-Specific Fixtures (`tests/conftest.py`)

```python
import httpx


@pytest.fixture(scope="session")
def kubo_available():
    """Check if local Kubo daemon is running. Skip tier2 tests if not."""
    try:
        r = httpx.post("http://127.0.0.1:5001/api/v0/id", timeout=3)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("Kubo daemon not available at localhost:5001")


@pytest.fixture
async def surrogate_cid(kubo_available):
    """Add surrogate content to Kubo and return CID. Cleans up after test."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:5001/api/v0/add",
            files={"file": ("test.txt", b"hvym-pinner-test-alpha")},
        )
        cid = resp.json()["Hash"]
        yield cid
        # Teardown: unpin
        await client.post(
            "http://127.0.0.1:5001/api/v0/pin/rm",
            params={"arg": cid},
        )


@pytest.fixture
async def real_executor(kubo_available, test_config):
    """Real KuboPinExecutor for Tier 2 tests."""
    return KuboPinExecutor(
        kubo_rpc_url=test_config.kubo_rpc_url,
        pin_timeout=test_config.pin_timeout,
        max_content_size=test_config.max_content_size,
        fetch_retries=test_config.fetch_retries,
    )
```

### 7.3 Hunter Fixtures

```python
@pytest.fixture
def mock_verifier():
    return MockVerifier(passed=True)


@pytest.fixture
def mock_flag_submitter():
    return MockFlagSubmitter(succeed=True)


@pytest.fixture
def hunter_config():
    return HunterConfig(
        enabled=True,
        cycle_interval=10,
        check_timeout=5,
        max_concurrent_checks=3,
        failure_threshold=3,
        cooldown_after_flag=60,
        pinner_cache_ttl=300,
    )


@pytest.fixture
async def daemon_with_hunter(hunter_config, store, mock_poller,
                              mock_executor, mock_submitter, mock_queries,
                              mock_verifier, mock_flag_submitter):
    """Daemon with CID Hunter enabled, using mock verifier and flag submitter."""
    cfg = make_test_config(hunter=hunter_config)
    d = PinnerDaemon(cfg)
    d.store = store
    d.poller = mock_poller
    d.executor = mock_executor
    d.submitter = mock_submitter
    d.queries = mock_queries
    # Wire hunter with mocks
    d.hunter.verifier = mock_verifier
    d.hunter.flag_submitter = mock_flag_submitter
    d.hunter.registry = PinnerRegistryCacheImpl(
        store=store, queries=mock_queries, ttl_seconds=300,
    )
    d.hunter.scheduler = PeriodicVerificationScheduler(
        store=store, verifier=mock_verifier, registry=d.hunter.registry,
        flag_submitter=mock_flag_submitter,
        cycle_interval=10, max_concurrent=3, failure_threshold=3,
        cooldown_after_flag=60,
    )
    return d
```

---

## 8. File Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures (store, daemon, config, Kubo)
├── factories.py             # make_pin_event(), make_pinned_event(), make_unpin_event()
├── mocks.py                 # MockPoller, MockExecutor, MockSubmitter, etc.
│
├── test_happy_path.py       # Tests 1-2: auto mode full lifecycle
├── test_rejections.py       # Tests 3-7: filter rejection paths
├── test_errors.py           # Tests 8-11: pin/claim error handling
├── test_event_lifecycle.py  # Tests 12-14: PIN→PINNED→UNPIN transitions
├── test_approve_mode.py     # Tests 15-18: approval workflow + mode switch
├── test_hunter.py           # Tests 19-20: CID Hunter track/verify/flag
├── test_integration.py      # Tests 21-23: concurrent, persistence, dashboard
│
└── tier2/
    ├── __init__.py
    └── test_kubo_pin.py     # Test 2: real Kubo pinning (marked @pytest.mark.kubo)
```

---

## 9. Prerequisites

### Tier 1 (CI / Default)

- Python 3.11+
- Project dependencies: `uv sync` (installs `stellar-sdk[aiohttp]`, `httpx`, `aiosqlite`)
- Test dependencies: `pytest`, `pytest-asyncio`, `pytest-mock`
- **No external services required**

Run: `uv run pytest` (or `uv run pytest -m "not kubo"` to be explicit)

### Tier 2 (Local Development)

- Everything from Tier 1, plus:
- **Kubo daemon** running at `localhost:5001` (default config)
  - Install: `ipfs init && ipfs daemon`
  - Or Docker: `docker run -d -p 5001:5001 ipfs/kubo:latest`

Run: `uv run pytest -m kubo`

### Pytest Configuration (`pyproject.toml` additions)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "kubo: requires local Kubo daemon at localhost:5001",
    "tier1: mock-only fast tests (default)",
]
```

---

## 10. Key Design Rationale

### Direct Method Calls vs. Full Loop

Most tests call `daemon._handle_pin_event(event)` directly rather than running the full `_main_loop()`. This gives:

- **Determinism**: No `asyncio.sleep()`, no polling timing issues
- **Speed**: Sub-millisecond per test
- **Isolation**: Each test controls exactly which events are processed

The full loop is only tested for:
- Approve mode (approved offers are picked up by the loop's "process approved" phase)
- Multi-event batch processing (verifying the loop processes all events from one poll)

### PinnerDaemon Constructor Accepts Real Components

The constructor calls `Keypair.from_secret()` (pure computation) and instantiates components that only store config references. No network calls happen. We construct normally then swap attributes, avoiding the need for dependency injection refactoring.

### In-Memory SQLite Over Mock Store

The `SQLiteStateStore` has 25+ methods with complex query logic (aggregations, status filters, JSON serialization). Mocking all of these would be fragile and wouldn't catch real bugs. In-memory SQLite is fast (~1ms per operation) and tests the actual persistence layer.

### Test Keypair Safety

The test config uses a real testnet keypair. This is safe because:
1. All Stellar network calls are mocked in Tier 1
2. The keypair has no mainnet funds
3. The secret is already in the repo's `.env` (gitignored) and deployed contracts doc
