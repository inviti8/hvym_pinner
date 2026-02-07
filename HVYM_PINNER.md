# HVYM_PINNER: Autonomous IPFS Pinning Daemon

## Overview

`hvym_pinner` is a Python-based IPFS pinning daemon that listens to `hvym-pin-service` Soroban contract events on Stellar and earns XLM by pinning content for Pintheon publishers. It operates in two modes: **fully autonomous** (auto-pins matching offers) and **semi-autonomous** (queues offers for approval via a frontend client). It acts as the off-chain counterpart to the on-chain pin marketplace.

The daemon is designed as a **headless backend** that exposes aggregated state data for consumption by varying frontend clients (CLI, web dashboard, mobile app, Electron, etc.). The package itself has no UI opinion - it produces structured data that any client can query and display.

**Architecture**: Python package with clean, well-defined interfaces designed for future transpilation to other languages (TypeScript, Rust, Go).

**Stack**:
- **Stellar interaction**: `stellar-sdk` (Python) v13+ against Soroban testnet RPC
- **Contract bindings**: Auto-generated Python bindings from `pintheon_contracts/bindings/hvym_pin_service/` (sync + async clients, typed data structures, SCVal serialization built-in)
- **IPFS backend**: Direct HTTP RPC to a local Kubo node (`:5001`)
- **Pin strategy**: Pin first, collect payment after (safe for the network)
- **Frontend bridge**: JSON-serializable data aggregation layer for any client

---

## Contract Bindings & Deployed Contracts

### Generated Bindings

The `pintheon_contracts/bindings/` directory contains **auto-generated Python bindings** for all Pintheon Soroban contracts. These eliminate the need for raw XDR encoding/decoding or manual transaction building.

**Location**: `../pintheon_contracts/bindings/hvym_pin_service/bindings.py` (2,856 lines)

Each binding provides two client classes:
- **`Client`** (synchronous) - for simple scripts and CLI commands
- **`ClientAsync`** (asynchronous) - for the daemon's async main loop

**Usage pattern** (from `bindings/examples/`):
```python
from hvym_pin_service.bindings import ClientAsync as PinServiceClient
from stellar_sdk import Keypair

# Read-only query (free, no signing)
client = PinServiceClient(contract_id=CONTRACT_ID, rpc_url=RPC_URL)
result = await client.get_slot(slot_id)
await result.simulate()
slot = result.result()  # returns typed Slot object

# Transaction (requires signing)
tx = await client.collect_pin(
    caller=public_key,
    slot_id=slot_id,
    source=public_key,
    signer=keypair,
)
await tx.simulate()
response = tx.sign_and_submit()
amount = response.result()  # typed return value
```

**Key binding methods for the pinner daemon**:

| Operation | Binding Method | Type |
|-----------|---------------|------|
| Register as pinner | `join_as_pinner(caller, node_id, multiaddr, min_price)` | Transaction |
| Update profile | `update_pinner(caller, node_id, multiaddr, min_price, active)` | Transaction |
| Collect pin payment | `collect_pin(caller, slot_id)` | Transaction |
| Flag a pinner | `flag_pinner(caller, pinner_addr)` | Transaction |
| Leave and reclaim stake | `leave_as_pinner(caller)` | Transaction |
| Query slot | `get_slot(slot_id)` | Query |
| All slots | `get_all_slots()` | Query |
| Available slots? | `has_available_slots()` | Query |
| Slot expired? | `is_slot_expired(slot_id)` | Query |
| Current epoch | `current_epoch()` | Query |
| Pinner details | `get_pinner(address)` | Query |
| Service config | `pin_fee()`, `join_fee()`, `min_offer_price()`, etc. | Query |
| Contract balance | `balance()` | Query |

**Relevant examples** (in `../pintheon_contracts/bindings/examples/`):
- `06_pin_service_queries.py` - Read-only queries (fees, slots, pinners, epochs)
- `07_pin_service_pinner.py` - Full pinner lifecycle (join, update, collect, flag, leave)
- `08_pin_service_publisher.py` - Publisher workflow (create pin, monitor, cancel)
- `03_async_example.py` - Async client usage pattern with concurrent queries

### Deployed Contracts (Testnet)

**Source of truth**: `../pintheon_contracts/deployments.json` (alpha v0.09)

| Contract | Contract ID | Status |
|----------|------------|--------|
| `hvym_pin_service` | `CCEDYFIHUCJFITWEOT7BWUO2HBQQ72L244ZXQ4YNOC6FYRDN3MKDQFK7` | Deployed |
| `hvym_pin_service_factory` | `CACBN6G2EPPLAQORDB3LXN3SULGVYBAETFZTNYTNDQ77B7JFRIBT66V2` | Deployed |
| `hvym_collective` | `CAYD2PS5KR4VSEQPQZEUDF3KHT2NDWTGVXAHPPMLLS4HHM5ARUNALFUU` | Deployed |
| `hvym_roster` | `CC4AWAEY5UMWYGI5WZIFG4EQZVVQMPZFFBVX4JOLISLDWZ5G4H4EDTAJ` | Deployed |
| `opus_token` | `CB3MM62JMDTNVJVOXORUOOPBFAWVTREJLA5VN4YME4MBNCHGBHQPQH7G` | Deployed |

**Network config** (from `bindings/examples/config.py`):
```
RPC_URL  = https://soroban-testnet.stellar.org
Network  = Test SDF Network ; September 2015
XLM_TOKEN = CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC
```

**Note**: The `config.py` in bindings/examples has slightly older contract IDs (v0.08). Always reference `deployments.json` for the latest deployed IDs.

### Impact on Implementation

The bindings dramatically simplify the Stellar integration layer:

1. **No XDR deserialization needed** - The bindings handle all SCVal encoding/decoding. We get typed Python objects (`Slot`, `Pinner`, etc.) directly.
2. **No manual transaction building** - `client.collect_pin()` builds, simulates, signs, and submits in a few lines.
3. **Event polling still needed** - The bindings are for contract calls, not event subscriptions. The Event Poller still uses `SorobanServer.get_events()` directly from `stellar-sdk`.
4. **Async-native** - `ClientAsync` fits directly into our `asyncio` daemon loop.

---

## Core Design Principles

1. **Dual-mode operation**: Fully autonomous (fire-and-forget) or semi-autonomous (human-in-the-loop approval queue)
2. **Frontend-agnostic data layer**: All daemon state is aggregated into structured, JSON-serializable snapshots that any frontend can consume - CLI, web, mobile, desktop
3. **Pin-first economics**: Always pin content before calling `collect_pin()` - absorb the risk of unpaid work rather than gaming the network
4. **Selective acceptance**: Filter offers by `min_price` threshold and wallet balance - only pin what's profitable and affordable
5. **Idempotent operations**: Safe to restart at any point without double-claiming or duplicate pins
6. **Clean interfaces**: All subsystems communicate through well-defined protocols/interfaces for future language portability
7. **Minimal dependencies**: Kubo HTTP RPC directly, no wrapper libraries

---

## Operating Modes

The daemon runs in one of two modes, set at startup via config. The mode determines what happens after an offer passes the filter.

### Fully Autonomous Mode (`mode = "auto"`)

The daemon pins and collects payment without any human interaction. As long as:
- The daemon wallet has sufficient XLM to cover `collect_pin()` transaction fees
- The offer's `offer_price >= min_price` (operator's configured threshold)
- The slot is active and not already claimed

...the daemon pins and claims immediately. This is the "set it and forget it" mode for operators who trust their price threshold and want maximum throughput.

```
PIN event ──▶ Filter ──▶ [passes?] ──▶ Fetch from gateway ──▶ Add+Pin to Kubo ──▶ collect_pin() ──▶ Earned
                            │
                            └──▶ [fails?] ──▶ Rejected (logged)
```

### Semi-Autonomous Mode (`mode = "approve"`)

The daemon collects and filters offers but does **not** pin or claim. Instead, qualifying offers are queued with status `awaiting_approval`. A frontend client queries the approval queue, displays it to the operator, and submits approve/reject decisions back.

```
PIN event ──▶ Filter ──▶ [passes?] ──▶ Queue as "awaiting_approval"
                            │                      │
                            │               Frontend reads queue
                            │               Operator approves/rejects
                            │                      │
                            │              ┌───────▼────────┐
                            │              │   approved?    │
                            │              │ Pin ──▶ Claim  │
                            │              │   rejected?    │
                            │              │ Mark rejected  │
                            │              └────────────────┘
                            │
                            └──▶ [fails?] ──▶ Rejected (logged)
```

**Approval queue behavior**:
- Offers in the queue have a TTL based on slot expiration. If the operator doesn't act before the slot expires, the offer auto-transitions to `expired`.
- The daemon continuously updates queued offers with live slot state (pins_remaining, expiry countdown) so the frontend always shows fresh data.
- Batch approve/reject is supported - operators can select multiple offers.

### Mode Switching

The mode can be changed at runtime via the data API (no restart required). The daemon re-evaluates any `awaiting_approval` offers: if switching to `auto`, queued offers that still pass the filter are immediately pinned.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          hvym_pinner daemon                          │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────────┐   │
│  │ Event Poller │──▶│ Offer Filter │──▶│    Mode Controller     │   │
│  │              │   │              │   │                        │   │
│  │ Polls Soroban│   │ min_price    │   │  auto ──▶ Pin + Claim  │   │
│  │ RPC for PIN  │   │ wallet bal.  │   │  approve ──▶ Queue     │   │
│  │ events       │   │ slot expiry  │   │                        │   │
│  └──────────────┘   └──────────────┘   └───────────┬────────────┘   │
│                                                     │                │
│                           ┌─────────────────────────┤                │
│                           │                         │                │
│                  ┌────────▼────────┐       ┌────────▼────────┐       │
│                  │  Pin Executor   │       │ Approval Queue  │       │
│                  │                 │       │                 │       │
│                  │ 1. Fetch from   │       │ awaiting_       │       │
│                  │    gateway URL  │       │ approval offers │       │
│                  │ 2. Add to Kubo  │◀──────│ (frontend       │       │
│                  │    (verify CID) │approve│  approves)      │       │
│                  │ 3. Pin locally  │       │                 │       │
│                  └────────┬────────┘       └─────────────────┘       │
│                           │                         ▲                │
│                  ┌────────▼────────┐                │                │
│                  │ Claim Submitter │       ┌────────┴────────┐       │
│                  │                 │       │   Data API      │       │
│  ┌────────────┐  │ collect_pin()   │       │                 │       │
│  │ State Store│◀─│ via stellar-sdk │       │ Snapshots for   │◀── Frontend
│  │ (SQLite)   │  └─────────────────┘       │ frontend clients│  clients
│  └────────────┘                            └─────────────────┘       │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐                                 │
│  │  CID Hunter  │   │   Health /   │                                 │
│  │  (optional)  │   │   Metrics    │                                 │
│  └──────────────┘   └──────────────┘                                 │
└──────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   Soroban Testnet       Kubo Node            Local SQLite
   (stellar-sdk)        (:5001 RPC)            (state.db)
                             │
                    ┌────────┴────────┐
                    │  Publisher       │
                    │  Gateway (HTTPS) │
                    │  {gateway}/ipfs/ │
                    └─────────────────┘
```

---

## Component Specifications

### 1. Event Poller

**Responsibility**: Poll Soroban RPC for `PIN` contract events and deliver them to the Offer Filter.

**Interface**:
```python
class EventPoller(Protocol):
    """Polls for new PIN events from the hvym-pin-service contract."""

    async def poll(self) -> list[PinEvent]:
        """Fetch new PIN events since last cursor. Returns deserialized events."""
        ...

    async def get_cursor(self) -> str | None:
        """Get the last processed event cursor for resumption."""
        ...
```

**Implementation details**:
- Uses `stellar-sdk` `SorobanServer.get_events()` with topic filter for `("PIN", "request")`
- Maintains a cursor (last seen ledger sequence) in SQLite for crash recovery
- Polling interval: configurable, default 5 seconds (roughly 1 ledger)
- Deserializes `PinEvent` XDR into Python dataclass
- Also listens for `UNPIN` events to remove offers from the local queue
- Listens for `PINNED` events to track when slots are filling up

**Event models**:
```python
@dataclass(frozen=True)
class PinEvent:
    """Emitted when a publisher creates a pin request."""
    slot_id: int
    cid: str
    filename: str         # original filename (display metadata, does NOT affect CID)
    gateway: str
    offer_price: int      # stroops
    pin_qty: int
    publisher: str        # Stellar address
    ledger_sequence: int  # when the event was emitted

@dataclass(frozen=True)
class PinnedEvent:
    """Emitted when a pinner collects payment for a pin.
    Critical for CID Hunter: tells us which pinner claimed which CID."""
    slot_id: int
    cid_hash: str         # SHA256 hash of CID (hex)
    pinner: str           # Stellar address of the claiming pinner
    amount: int           # stroops paid
    pins_remaining: int
    ledger_sequence: int

@dataclass(frozen=True)
class UnpinEvent:
    """Emitted when a slot is freed (cancelled, expired, or filled)."""
    slot_id: int
    cid_hash: str         # SHA256 hash of CID (hex)
    ledger_sequence: int
```

### 2. Offer Filter

**Responsibility**: Decide whether to accept or reject a PIN offer based on local policy and wallet health.

**Interface**:
```python
class OfferFilter(Protocol):
    """Filters PIN events based on local policy."""

    async def evaluate(self, event: PinEvent) -> FilterResult:
        """Evaluate an offer. Returns accept/reject with reason."""
        ...
```

```python
@dataclass
class FilterResult:
    accepted: bool
    reason: str               # human-readable: "price_too_low", "already_claimed", "insufficient_xlm", etc.
    offer: PinEvent
    wallet_balance: int       # current XLM balance at time of evaluation (stroops)
    estimated_tx_fee: int     # estimated collect_pin() tx cost (stroops)
    net_profit: int           # offer_price - estimated_tx_fee
```

**Filter criteria** (all configurable):
- `offer_price >= pinner.min_price` (our minimum acceptable price)
- Wallet has enough XLM to cover `collect_pin()` transaction fee (daemon checks balance before committing)
- Slot is not already claimed by us (check local state DB)
- CID is not already pinned locally (avoid redundant work)
- Slot has remaining pins (`pins_remaining > 0`)
- Slot is not expired (compute from `created_at` + `max_cycles`)
- Optional: gateway URL reachability pre-check
- Optional: content size pre-check (HEAD request) against `max_content_size`

**On-chain verification before pinning**:
Before committing to pin work, query the contract to verify the slot is still active:
```python
async def verify_slot_active(self, slot_id: int) -> bool:
    """Call get_slot() and is_slot_expired() to confirm slot is still claimable."""
    ...
```

### 2a. Mode Controller

**Responsibility**: Routes filtered offers to the correct destination based on operating mode.

**Interface**:
```python
class ModeController(Protocol):
    """Routes offers based on daemon operating mode."""

    async def handle_accepted_offer(self, result: FilterResult) -> None:
        """In auto mode: immediately pin + claim. In approve mode: queue for approval."""
        ...

    def get_mode(self) -> DaemonMode: ...
    def set_mode(self, mode: DaemonMode) -> None: ...
```

```python
class DaemonMode(str, Enum):
    AUTO = "auto"           # Pin + claim immediately
    APPROVE = "approve"     # Queue for frontend approval
```

**Behavior by mode**:
- `AUTO`: Calls Pin Executor -> Claim Submitter directly. No human in the loop.
- `APPROVE`: Writes offer to approval queue with status `awaiting_approval`. Waits for external approval signal via Data API.

### 3. Pin Executor

**Responsibility**: Download content from the publisher's gateway and pin it to the local Kubo node.

**Critical context — Pintheon nodes are private**: Pintheon IPFS nodes run in fully isolated private swarms (`LIBP2P_FORCE_PNET=1`, private swarm key, all bootstrap peers removed). They have zero peering exposure to the public IPFS network. Content published on a Pintheon node is **not discoverable via DHT, Bitswap, or any standard IPFS resolution mechanism**. The publisher's public HTTPS gateway (served by nginx proxying to the local Kubo gateway) is the **only** way for pinners to obtain the content.

This means `pin/add?arg={cid}` alone will **always timeout** for fresh Pintheon content — Kubo cannot find providers on the public DHT because the publisher never advertises there. The executor must fetch content from the gateway URL first, then inject it into the local blockstore.

**Interface**:
```python
class PinExecutor(Protocol):
    """Handles the actual IPFS pinning operation."""

    async def pin(self, cid: str, gateway: str) -> PinResult:
        """Fetch content from gateway, add to local Kubo, and pin."""
        ...

    async def verify_pinned(self, cid: str) -> bool:
        """Check if CID is pinned on our local node."""
        ...

    async def unpin(self, cid: str) -> bool:
        """Remove a pin (e.g., after UNPIN event)."""
        ...
```

**Implementation — three-step gateway-fetch pipeline**:

```
Publisher's Pintheon Node                    Pinner's Kubo Node
  (private IPFS swarm)                      (public IPFS node)
         │                                          │
         │  nginx gateway (HTTPS)                   │
         │  GET {gateway}/ipfs/{cid}                │
         │◀─────────────────────────────────────────│ Step 1: Fetch bytes
         │──────────── raw content bytes ──────────▶│
         │                                          │
         │                                POST /api/v0/add
         │                                (multipart upload) ──▶│ Step 2: Add to
         │                                returned_cid == cid?  │ local blockstore
         │                                          │           │ + verify CID match
         │                                          │
         │                                POST /api/v0/pin/add?arg={cid}
         │                                (instant — blocks are local) ──▶│ Step 3: Pin
         │                                          │
```

- **Step 1 — Fetch from gateway**: `GET {gateway}/ipfs/{cid}`
  - The gateway URL comes from the `PinEvent`. It points to the publisher's nginx proxy which serves content from their private Kubo gateway.
  - Timeout: configurable, default 60 seconds
  - Max size: configurable, default 1 GB (check `Content-Length` header before downloading body)
  - Retry: up to 3 attempts with exponential backoff on timeout/5xx
  - Stream the response to avoid holding large files in memory

- **Step 2 — Add to local Kubo**: `POST http://localhost:5001/api/v0/add`
  - Upload the fetched bytes as a multipart form file
  - **Must use matching Kubo add parameters** to reproduce the original CID:
    - `wrap-with-directory=false` (Pintheon default)
    - `chunker=size-262144` (Pintheon default)
    - `raw-leaves=false` (Pintheon default)
    - `cid-version=0` (Pintheon default)
    - `hash=sha2-256` (Pintheon default)
  - **CID verification**: Compare the CID returned by `/add` against the expected CID from the event. If they don't match, abort — the gateway served wrong content.
  - **Note on filenames**: The `filename` field in `PinEvent` is display metadata only. With `wrap-with-directory=false`, the filename in the multipart form does not affect the resulting CID. CIDs are computed purely from content bytes + UnixFS encoding parameters.

- **Step 3 — Pin**: `POST http://localhost:5001/api/v0/pin/add?arg={cid}`
  - Now succeeds instantly because the blocks are already in the local blockstore from Step 2.
  - Verify pin: `POST http://localhost:5001/api/v0/pin/ls?arg={cid}` — confirm the CID appears in the pinned set.

**Why not just `pin/add` directly?**

A bare `pin/add?arg={cid}` tells Kubo to find and fetch the content via its own resolution (DHT → Bitswap → configured gateways). This **only works** if:
- Another pinner has already pinned the content and is advertising it on the public DHT, OR
- The content happens to exist on a public IPFS gateway that Kubo knows about

For the **first pinner** to claim a fresh pin request, neither condition is true. The gateway fetch is mandatory.

**Result model**:
```python
@dataclass
class PinResult:
    success: bool
    cid: str
    bytes_pinned: int | None
    error: str | None
    duration_ms: int
```

### 4. Claim Submitter

**Responsibility**: Call `collect_pin()` on the Soroban contract after successful pinning.

**Interface**:
```python
class ClaimSubmitter(Protocol):
    """Submits collect_pin() transactions to the Soroban contract."""

    async def submit_claim(self, slot_id: int) -> ClaimResult:
        """Build, sign, and submit a collect_pin() transaction."""
        ...
```

**Implementation details** (uses generated bindings):
```python
from hvym_pin_service.bindings import ClientAsync as PinServiceClient

client = PinServiceClient(contract_id=contract_id, rpc_url=rpc_url)
tx = await client.collect_pin(
    caller=public_key,
    slot_id=slot_id,
    source=public_key,
    signer=keypair,
)
await tx.simulate()
response = tx.sign_and_submit()
```
- The binding handles transaction building, SCVal encoding, simulation, and submission
- Sign with pinner's Stellar keypair (loaded from env var `HVYM_PINNER_SECRET`)
- Handle errors gracefully:
  - `AlreadyClaimed` -> log warning, mark as claimed in local DB
  - `SlotExpired` -> log, remove from queue
  - `SlotNotActive` -> log, remove from queue
  - `NotPinner` -> fatal error, daemon should alert and pause
  - Network errors -> retry with backoff

**Result model**:
```python
@dataclass
class ClaimResult:
    success: bool
    slot_id: int
    amount_earned: int | None   # stroops
    tx_hash: str | None
    error: str | None
```

### 5. State Store (SQLite)

**Responsibility**: Persist daemon state for crash recovery and idempotency.

**Schema**:
```sql
-- Event cursor for resumption
CREATE TABLE cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_ledger INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Daemon runtime config (persisted across restarts)
CREATE TABLE daemon_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'approve'
    min_price INTEGER NOT NULL,
    max_content_size INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tracked offers (PIN events we've seen)
-- Status flow:
--   auto mode:    pending -> pinning -> pinned -> claiming -> claimed
--   approve mode: pending -> awaiting_approval -> approved -> pinning -> pinned -> claiming -> claimed
--   terminal:     rejected | expired | failed
CREATE TABLE offers (
    slot_id INTEGER PRIMARY KEY,
    cid TEXT NOT NULL,
    filename TEXT NOT NULL DEFAULT '',  -- original filename (display metadata)
    gateway TEXT NOT NULL,
    offer_price INTEGER NOT NULL,
    pin_qty INTEGER NOT NULL,
    pins_remaining INTEGER NOT NULL,
    publisher TEXT NOT NULL,
    ledger_sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT,                    -- why it was filtered out or failed
    net_profit INTEGER,                    -- offer_price minus estimated tx fee
    estimated_expiry TEXT,                 -- ISO 8601 estimated slot expiry time
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_offers_status ON offers(status);

-- Our completed claims (for earnings tracking)
CREATE TABLE claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    cid TEXT NOT NULL,
    amount_earned INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_claims_claimed_at ON claims(claimed_at);

-- Pinned CIDs on our local node
CREATE TABLE pins (
    cid TEXT PRIMARY KEY,
    slot_id INTEGER,
    bytes_pinned INTEGER,
    pinned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Activity log (feeds the frontend activity feed)
CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,               -- offer_seen | offer_accepted | pin_started | claim_confirmed | etc.
    slot_id INTEGER,
    cid TEXT,
    amount INTEGER,                         -- stroops, for earnings events
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activity_created ON activity_log(created_at);
```

**Interface**:
```python
class StateStore(Protocol):
    """Persists daemon state for crash recovery and frontend data."""

    # Cursor
    def get_cursor(self) -> int | None: ...
    def set_cursor(self, ledger: int) -> None: ...

    # Daemon config (mode, policy)
    def get_daemon_config(self) -> DaemonConfigRecord: ...
    def set_daemon_config(self, mode: str | None = None, min_price: int | None = None, max_content_size: int | None = None) -> None: ...

    # Offers
    def save_offer(self, event: PinEvent, status: str = 'pending') -> None: ...
    def get_offer(self, slot_id: int) -> OfferRecord | None: ...
    def update_offer_status(self, slot_id: int, status: str, reject_reason: str | None = None) -> None: ...
    def get_offers_by_status(self, status: str) -> list[OfferRecord]: ...
    def get_approval_queue(self) -> list[OfferRecord]: ...
    def get_all_offers(self) -> list[OfferRecord]: ...

    # Claims & earnings
    def save_claim(self, claim: ClaimResult) -> None: ...
    def get_earnings(self, since: str | None = None) -> EarningsSummary: ...

    # Pins
    def save_pin(self, cid: str, slot_id: int, bytes_pinned: int) -> None: ...
    def is_cid_pinned(self, cid: str) -> bool: ...
    def get_all_pins(self) -> list[PinRecord]: ...

    # Activity log
    def log_activity(self, event_type: str, message: str, slot_id: int | None = None, cid: str | None = None, amount: int | None = None) -> None: ...
    def get_recent_activity(self, limit: int = 50) -> list[ActivityRecord]: ...
```

### 6. CID Hunter Module

**Responsibility**: Verify that pinners who claimed our published CIDs are actually serving them on the IPFS network. When verification fails, submit `flag_pinner()` to the contract. This module is active when the operator is also a Pintheon publisher (i.e., they have CIDs they paid to have pinned).

**Key insight from contract spec**: Pintheon nodes are isolated (no peering, no exposed API). But Pintheon users typically also run pinning daemons. The CID Hunter runs on the **pinning daemon**, which is already a full IPFS node capable of Bitswap queries.

#### 6a. Tracked Pin Registry

The hunter needs to know: **which CIDs are ours, and which pinners claimed them**. This is built from two event streams:

1. **PIN events where `publisher == our_address`**: These are CIDs we published and paid for.
2. **PINNED events for those slots**: These tell us which pinners claimed our pins.

The Event Poller already captures both. The hunter maintains a registry of `(cid, pinner)` pairs to verify.

**Data model**:
```python
@dataclass
class TrackedPin:
    """A (CID, pinner) pair we are monitoring."""
    cid: str
    cid_hash: str                      # SHA256 hash (matches on-chain cid_hash)
    pinner_address: str                # Stellar address of the claiming pinner
    pinner_node_id: str                # IPFS peer ID (from pinner registry)
    pinner_multiaddr: str              # IPFS multiaddress (from pinner registry)
    slot_id: int                       # Original slot (for context, not for flagging)
    claimed_at: str                    # ISO 8601 when PINNED event was seen
    last_verified_at: str | None       # ISO 8601 last successful verification
    last_checked_at: str | None        # ISO 8601 last check attempt (success or fail)
    consecutive_failures: int          # Consecutive failed verifications
    total_checks: int                  # Lifetime verification attempts
    total_failures: int                # Lifetime failed verifications
    status: str                        # tracking | verified | suspect | flagged | flag_submitted
    flagged_at: str | None             # ISO 8601 when we submitted flag_pinner()
    flag_tx_hash: str | None           # Transaction hash of the flag submission
```

**Status lifecycle**:
```
PINNED event seen
       │
       ▼
   tracking ──── verify ──── pass ──▶ verified ──── next cycle ──▶ tracking
       │                       │
       │                    fail (consecutive_failures < threshold)
       │                       │
       │                       ▼
       │                   suspect ──── verify ──── pass ──▶ verified (reset failures)
       │                       │                     │
       │                       │                  fail (>= threshold)
       │                       │                     │
       │                       ▼                     ▼
       │                  flag_submitted ◀───── flag_pinner() called
       │
       └──── pinner already flagged by us ──▶ flag_submitted (skip, contract prevents dupes)
```

#### 6b. Verification Engine

Three-tier verification, from cheapest to most definitive:

**Interface**:
```python
class PinVerifier(Protocol):
    """Verifies a pinner is actually serving a CID on the IPFS network."""

    async def verify(self, cid: str, pinner_node_id: str, pinner_multiaddr: str) -> VerificationResult:
        """Run full verification pipeline against a single (CID, pinner) pair."""
        ...

class VerificationMethod(str, Enum):
    DHT_PROVIDER = "dht_provider"       # Quick: is pinner listed as provider?
    BITSWAP_WANT_HAVE = "bitswap"       # Definitive: does pinner respond HAVE?
    PARTIAL_RETRIEVAL = "retrieval"      # High-value: can we fetch a block?

@dataclass
class VerificationResult:
    cid: str
    pinner_node_id: str
    passed: bool
    method_used: str                    # which method produced the final result
    methods_attempted: list[MethodResult]
    duration_ms: int
    checked_at: str                     # ISO 8601

@dataclass
class MethodResult:
    method: str                         # "dht_provider" | "bitswap" | "retrieval"
    passed: bool | None                 # None if skipped or timed out
    detail: str                         # human-readable detail
    duration_ms: int
```

**Verification pipeline**:
```
Step 1: DHT Provider Lookup (fast, ~2-5s)
  Kubo RPC: POST /api/v0/routing/findprovs?arg={cid}&num-providers=20
  Check if pinner_node_id appears in the provider list.
  ├── Found ──▶ likely serving, proceed to Step 2 for confirmation
  └── Not found ──▶ suspicious, but not conclusive (DHT propagation delay)
                     Still proceed to Step 2.

Step 2: Bitswap Want-Have (definitive, ~5-15s)
  Kubo RPC: POST /api/v0/bitswap/wantlist  (to check our own state)
  Then trigger a want-have via: fetching a single block from the pinner.

  Implementation approach:
    1. Connect to pinner: POST /api/v0/swarm/connect?arg={pinner_multiaddr}
    2. Request block: POST /api/v0/block/get?arg={cid} with timeout
       - If block received: pinner HAS the content ──▶ PASS
       - If timeout/error: pinner does NOT have it ──▶ FAIL

  Alternative (if Kubo supports direct Bitswap queries):
    POST /api/v0/bitswap/stat (check if pinner is a wantlist partner)

  ├── HAVE ──▶ PASS (verification complete)
  └── DONT_HAVE / timeout ──▶ FAIL

Step 3: Partial Retrieval (optional, for high-value CIDs)
  Only if configured and CID value exceeds threshold.
  POST /api/v0/cat?arg={cid}&length=1024 routed through pinner
  Confirms actual data possession, not just index.
  ├── Data received ──▶ PASS
  └── Error ──▶ FAIL
```

**Kubo RPC calls used**:
| Operation | Kubo Endpoint | Purpose |
|-----------|--------------|---------|
| Find providers | `POST /api/v0/routing/findprovs?arg={cid}` | DHT provider lookup |
| Connect to peer | `POST /api/v0/swarm/connect?arg={multiaddr}` | Establish libp2p connection |
| Get block | `POST /api/v0/block/get?arg={cid}` | Bitswap retrieval (proves possession) |
| Check peer info | `POST /api/v0/id?arg={peer_id}` | Verify peer is reachable |
| Cat (partial) | `POST /api/v0/cat?arg={cid}&length=1024` | Partial content retrieval |

#### 6c. Verification Scheduler

Runs verification cycles on a configurable schedule. Spreads checks across the interval to avoid burst traffic.

**Interface**:
```python
class VerificationScheduler(Protocol):
    """Schedules periodic verification of tracked pins."""

    async def run_cycle(self) -> CycleReport:
        """Run one full verification cycle across all tracked pins."""
        ...

    def next_cycle_at(self) -> str | None:
        """ISO 8601 timestamp of next scheduled cycle. None if not running."""
        ...

    def get_schedule_config(self) -> ScheduleConfig:
        """Current scheduling parameters."""
        ...

@dataclass
class ScheduleConfig:
    cycle_interval: int               # seconds between full verification cycles
    check_timeout: int                # seconds per individual verification check
    max_concurrent_checks: int        # parallel verifications (bounded to avoid flooding)
    failure_threshold: int            # consecutive failures before flagging
    cooldown_after_flag: int          # seconds to wait after flagging before re-checking

@dataclass
class CycleReport:
    """Results from a single verification cycle."""
    cycle_id: int
    started_at: str
    completed_at: str
    total_checked: int
    passed: int
    failed: int
    flagged: int                      # flags submitted this cycle
    skipped: int                      # already flagged, cooldown, etc.
    errors: int                       # network errors, timeouts
    duration_ms: int
```

**Scheduling logic**:
```python
async def run_cycle(self) -> CycleReport:
    tracked = self.store.get_tracked_pins(status=['tracking', 'verified', 'suspect'])

    # Prioritize: suspects first (closer to flag threshold), then longest since last check
    tracked.sort(key=lambda t: (-t.consecutive_failures, t.last_checked_at or ''))

    results = []
    semaphore = asyncio.Semaphore(self.config.max_concurrent_checks)

    async def check_one(pin: TrackedPin):
        async with semaphore:
            result = await self.verifier.verify(pin.cid, pin.pinner_node_id, pin.pinner_multiaddr)
            await self._process_result(pin, result)
            return result

    results = await asyncio.gather(*[check_one(pin) for pin in tracked], return_exceptions=True)
    return self._build_report(results)
```

**Result processing**:
```python
async def _process_result(self, pin: TrackedPin, result: VerificationResult) -> None:
    self.store.record_verification(pin.cid, pin.pinner_address, result)

    if result.passed:
        self.store.update_tracked_pin(pin.cid, pin.pinner_address,
            status='verified',
            consecutive_failures=0,
            last_verified_at=result.checked_at)
        self.store.log_activity('hunt_verified',
            f'Pinner {pin.pinner_address[:8]}... verified for {pin.cid[:16]}...',
            cid=pin.cid)
    else:
        new_failures = pin.consecutive_failures + 1
        if new_failures >= self.config.failure_threshold:
            # Flag the pinner
            flag_result = await self.flag_submitter.submit_flag(pin.pinner_address)
            self.store.update_tracked_pin(pin.cid, pin.pinner_address,
                status='flag_submitted',
                consecutive_failures=new_failures,
                flagged_at=result.checked_at,
                flag_tx_hash=flag_result.tx_hash)
            self.store.log_activity('hunt_flagged',
                f'Flagged pinner {pin.pinner_address[:8]}... for {pin.cid[:16]}... '
                f'({new_failures} consecutive failures)',
                cid=pin.cid)
        else:
            self.store.update_tracked_pin(pin.cid, pin.pinner_address,
                status='suspect',
                consecutive_failures=new_failures)
            self.store.log_activity('hunt_failed',
                f'Verification failed for {pin.pinner_address[:8]}... on {pin.cid[:16]}... '
                f'(failure {new_failures}/{self.config.failure_threshold})',
                cid=pin.cid)
```

#### 6d. Flag Submitter

**Interface**:
```python
class FlagSubmitter(Protocol):
    """Submits flag_pinner() transactions to the contract."""

    async def submit_flag(self, pinner_address: str) -> FlagResult:
        """Build, sign, and submit flag_pinner() transaction."""
        ...

    async def has_already_flagged(self, pinner_address: str) -> bool:
        """Check if we've already flagged this pinner (contract prevents dupes)."""
        ...

@dataclass
class FlagResult:
    success: bool
    pinner_address: str
    flag_count: int | None            # current flag count on pinner after our flag
    tx_hash: str | None
    error: str | None
    bounty_earned: int | None         # if our flag hit the threshold, bounty in stroops
```

**Implementation details**:
- Build Soroban transaction invoking `flag_pinner(caller, pinner_address)`
- The caller must be a registered pinner (contract enforces this)
- Handle `AlreadyFlagged` error gracefully (mark as already flagged locally)
- If our flag triggers the threshold, the contract auto-distributes the bounty. The `FlagResult.bounty_earned` field captures our share from the transaction result.

#### 6e. Pinner Registry Cache

To verify pinners, the hunter needs their `node_id` and `multiaddr`. These are stored on-chain in the Pinner registry. The hunter maintains a local cache.

**Interface**:
```python
class PinnerRegistryCache(Protocol):
    """Local cache of on-chain pinner registry data."""

    async def get_pinner_info(self, address: str) -> PinnerInfo | None:
        """Get pinner's IPFS node details. Fetches from chain if not cached."""
        ...

    async def refresh(self, address: str) -> PinnerInfo | None:
        """Force refresh from chain (e.g., if multiaddr changed)."""
        ...

@dataclass
class PinnerInfo:
    address: str
    node_id: str
    multiaddr: str
    active: bool
    cached_at: str                    # ISO 8601
```

#### 6f. CID Hunter Orchestrator

Top-level component that ties the hunter subsystems together.

**Interface**:
```python
class CIDHunter(Protocol):
    """Orchestrates CID verification and dispute submission."""

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Start the verification scheduler."""
        ...

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        ...

    # ── Event ingestion ─────────────────────────────────────

    async def on_pin_event(self, event: PinEvent) -> None:
        """Handle a PIN event. If publisher is us, start tracking this CID."""
        ...

    async def on_pinned_event(self, event: PinnedEvent) -> None:
        """Handle a PINNED event. If CID is one we're tracking, register the pinner for verification."""
        ...

    async def on_unpin_event(self, event: UnpinEvent) -> None:
        """Handle an UNPIN event. Stop tracking CIDs from freed slots."""
        ...

    # ── Manual operations ───────────────────────────────────

    async def verify_now(self, cid: str | None = None, pinner_address: str | None = None) -> list[VerificationResult]:
        """Trigger immediate verification. If no args, verify all. Optionally filter by CID or pinner."""
        ...

    async def flag_now(self, pinner_address: str) -> FlagResult:
        """Manually flag a pinner (bypass failure threshold)."""
        ...

    # ── State queries (for Data API) ────────────────────────

    def get_tracked_pins(self) -> list[TrackedPin]: ...
    def get_suspects(self) -> list[TrackedPin]: ...
    def get_flag_history(self) -> list[FlagRecord]: ...
    def get_cycle_history(self, limit: int = 10) -> list[CycleReport]: ...
    def get_hunter_summary(self) -> HunterSummary: ...
```

#### 6g. CID Hunter Data Models (Frontend Snapshots)

```python
@dataclass
class HunterSummary:
    """CID Hunter status for the dashboard."""
    enabled: bool
    total_tracked_pins: int            # (CID, pinner) pairs we're monitoring
    verified_count: int                # currently verified
    suspect_count: int                 # failed verification, not yet flagged
    flagged_count: int                 # flags we've submitted
    total_checks_lifetime: int
    total_flags_lifetime: int
    bounties_earned_stroops: int       # total bounty earnings from flags
    bounties_earned_xlm: str
    last_cycle_at: str | None          # ISO 8601
    next_cycle_at: str | None          # ISO 8601
    cycle_interval_seconds: int

@dataclass
class TrackedPinSnapshot:
    """A tracked (CID, pinner) pair for the frontend."""
    cid: str
    cid_short: str                     # first 16 chars for display
    pinner_address: str
    pinner_address_short: str          # first 8 chars
    pinner_node_id: str
    status: str                        # tracking | verified | suspect | flagged | flag_submitted
    consecutive_failures: int
    failure_threshold: int             # from config, so frontend can show "2/5 failures"
    total_checks: int
    total_failures: int
    last_verified_at: str | None
    last_checked_at: str | None
    flagged_at: str | None

@dataclass
class FlagRecord:
    """A flag we submitted, for history."""
    pinner_address: str
    tx_hash: str
    flag_count_after: int | None       # pinner's flag count after our flag
    bounty_earned: int | None          # stroops, if threshold was hit
    submitted_at: str                  # ISO 8601

@dataclass
class VerificationLogEntry:
    """Single verification check result for the activity feed."""
    cid: str
    pinner_address: str
    passed: bool
    method_used: str
    duration_ms: int
    checked_at: str
```

#### 6h. SQLite Schema (Hunter Tables)

```sql
-- CIDs we published and are tracking for verification
CREATE TABLE tracked_cids (
    cid TEXT NOT NULL,
    cid_hash TEXT NOT NULL,
    slot_id INTEGER NOT NULL,              -- original slot (for context)
    publisher TEXT NOT NULL,                -- should be our address
    gateway TEXT,
    pin_qty INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_tracked_cids_cid ON tracked_cids(cid);

-- (CID, pinner) pairs: each pinner that claimed one of our CIDs
CREATE TABLE tracked_pins (
    cid TEXT NOT NULL,
    pinner_address TEXT NOT NULL,
    pinner_node_id TEXT NOT NULL,
    pinner_multiaddr TEXT NOT NULL,
    slot_id INTEGER NOT NULL,
    claimed_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_checked_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_checks INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'tracking',  -- tracking | verified | suspect | flag_submitted
    flagged_at TEXT,
    flag_tx_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cid, pinner_address)
);
CREATE INDEX idx_tracked_pins_status ON tracked_pins(status);
CREATE INDEX idx_tracked_pins_next_check ON tracked_pins(last_checked_at);

-- Verification check log (individual check results)
CREATE TABLE verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    pinner_address TEXT NOT NULL,
    passed INTEGER NOT NULL,               -- 0 or 1
    method_used TEXT NOT NULL,
    methods_attempted TEXT NOT NULL,        -- JSON array of MethodResult
    duration_ms INTEGER NOT NULL,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_vlog_checked ON verification_log(checked_at);
CREATE INDEX idx_vlog_cid_pinner ON verification_log(cid, pinner_address);

-- Verification cycle history
CREATE TABLE verification_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    total_checked INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    flagged INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL
);

-- Flags we've submitted
CREATE TABLE flag_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pinner_address TEXT NOT NULL,
    tx_hash TEXT,
    flag_count_after INTEGER,
    bounty_earned INTEGER,                 -- stroops
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_flags_pinner ON flag_history(pinner_address);

-- Pinner registry cache (IPFS node details for verification)
CREATE TABLE pinner_cache (
    address TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    multiaddr TEXT NOT NULL,
    active INTEGER NOT NULL,               -- 0 or 1
    cached_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

#### 6i. CID Hunter Configuration

```toml
[hunter]
enabled = false                        # false by default; enable if operator is also a publisher
cycle_interval = 3600                  # seconds between verification cycles (default: 1 hour)
check_timeout = 30                     # seconds per individual pinner check
max_concurrent_checks = 5             # parallel verifications per cycle
failure_threshold = 3                  # consecutive failures before flagging
cooldown_after_flag = 86400           # seconds to wait after flagging before re-checking (24h)
pinner_cache_ttl = 3600               # seconds before refreshing pinner registry cache
verification_methods = ["dht_provider", "bitswap"]  # which methods to use
# verification_methods = ["dht_provider", "bitswap", "retrieval"]  # add partial retrieval for high-value
```

#### 6j. Integration with Main Daemon Loop

The CID Hunter runs as a background task alongside the main polling loop:

```python
async def run(config: DaemonConfig) -> None:
    """Main daemon loop with CID Hunter integration."""

    # ... existing setup ...

    hunter = None
    if config.hunter.enabled:
        verifier = KuboPinVerifier(config.kubo_rpc_url, config.hunter)
        flag_sub = SorobanFlagSubmitter(config.soroban_rpc, config.contract_id, config.keypair)
        registry = PinnerRegistryCache(config.soroban_rpc, config.contract_id, store)
        scheduler = PeriodicVerificationScheduler(verifier, flag_sub, registry, store, config.hunter)
        hunter = CIDHunterOrchestrator(scheduler, store, config.pinner_address)
        asyncio.create_task(hunter.start())  # runs verification cycles in background

    await resume_interrupted(store, executor, submitter)

    while True:
        try:
            events = await poller.poll()

            for event in events:
                if isinstance(event, PinEvent):
                    # ... existing offer handling ...

                    # Feed to CID Hunter (tracks if publisher is us)
                    if hunter:
                        await hunter.on_pin_event(event)

                elif isinstance(event, PinnedEvent):
                    # Feed to CID Hunter (registers pinner for verification if CID is ours)
                    if hunter:
                        await hunter.on_pinned_event(event)

                elif isinstance(event, UnpinEvent):
                    # ... existing unpin handling ...

                    if hunter:
                        await hunter.on_unpin_event(event)

            # ... rest of loop ...
```

#### 6k. Data API Extensions for CID Hunter

The Data API exposes hunter state to frontends:

```python
class DataAPI(Protocol):
    # ... existing methods ...

    # ── CID Hunter (read) ──────────────────────────────────

    def get_hunter_summary(self) -> HunterSummary:
        """CID Hunter overview for the dashboard."""
        ...

    def get_tracked_pins(self, status: str | None = None) -> list[TrackedPinSnapshot]:
        """All tracked (CID, pinner) pairs. Optionally filter by status."""
        ...

    def get_suspects(self) -> list[TrackedPinSnapshot]:
        """Pinners that have failed verification (shorthand for status='suspect')."""
        ...

    def get_flag_history(self) -> list[FlagRecord]:
        """History of flags we've submitted."""
        ...

    def get_verification_log(self, cid: str | None = None, pinner: str | None = None, limit: int = 50) -> list[VerificationLogEntry]:
        """Detailed verification check history."""
        ...

    def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        """Past verification cycle summaries."""
        ...

    # ── CID Hunter (actions) ───────────────────────────────

    async def verify_now(self, cid: str | None = None, pinner: str | None = None) -> list[VerificationResult]:
        """Trigger immediate verification from frontend."""
        ...

    async def flag_pinner(self, pinner_address: str) -> FlagResult:
        """Manually flag a pinner from frontend (bypass threshold)."""
        ...

    def update_hunter_config(self, cycle_interval: int | None = None, failure_threshold: int | None = None) -> ActionResult:
        """Update hunter settings at runtime."""
        ...
```

The `DashboardSnapshot` is extended to include the hunter:

```python
@dataclass
class DashboardSnapshot:
    # ... existing fields ...

    # CID Hunter
    hunter: HunterSummary | None       # None if hunter is disabled
```

### 7. Health / Metrics

**Responsibility**: Expose daemon health and earnings metrics.

**Interface**:
```python
class HealthCheck(Protocol):
    """Reports daemon health status."""

    def status(self) -> DaemonStatus: ...
    def earnings(self) -> EarningsReport: ...
    def kubo_connected(self) -> bool: ...
    def stellar_connected(self) -> bool: ...
```

**Metrics tracked**:
- Total XLM earned (all time, last 24h)
- Pins completed (count)
- Offers seen / accepted / rejected / expired
- Current Kubo node status (connected, peer count)
- Current Stellar RPC status (connected, latest ledger)
- Uptime

### 8. Data Aggregation API (Frontend Bridge)

**Responsibility**: Aggregate all daemon state into structured, JSON-serializable snapshots for consumption by any frontend client. This is the **sole interface** between the daemon and any UI.

The daemon itself has no UI. Frontends (CLI, web dashboard, Electron app, mobile) all consume the same data shapes. The API can be exposed via:
- **In-process** (direct Python import for CLI tools)
- **Unix socket / named pipe** (for local IPC with desktop apps)
- **HTTP** (optional, for web dashboards - thin wrapper over the same data)

**Interface**:
```python
class DataAPI(Protocol):
    """Aggregated daemon state for frontend clients."""

    # ── Snapshots (read) ──────────────────────────────────

    def get_dashboard(self) -> DashboardSnapshot:
        """Full daemon state in a single call. The primary frontend entry point."""
        ...

    def get_offers(self, status: str | None = None) -> list[OfferSnapshot]:
        """List offers, optionally filtered by status."""
        ...

    def get_approval_queue(self) -> list[OfferSnapshot]:
        """Offers awaiting operator approval (semi-autonomous mode)."""
        ...

    def get_earnings(self, period: str = "all") -> EarningsSnapshot:
        """Earnings breakdown. period: 'all', '24h', '7d', '30d'."""
        ...

    def get_pins(self) -> list[PinSnapshot]:
        """All CIDs currently pinned on our node."""
        ...

    def get_wallet(self) -> WalletSnapshot:
        """Wallet balance and transaction history."""
        ...

    def get_contract_state(self) -> ContractSnapshot:
        """Current on-chain state: slots, config, our pinner record."""
        ...

    # ── Actions (write) ────────────────────────────────────

    async def approve_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        """Approve queued offers for pinning (semi-autonomous mode)."""
        ...

    async def reject_offers(self, slot_ids: list[int]) -> list[ActionResult]:
        """Reject queued offers (semi-autonomous mode)."""
        ...

    def set_mode(self, mode: str) -> ActionResult:
        """Switch operating mode ('auto' or 'approve'). Takes effect immediately."""
        ...

    def update_policy(self, min_price: int | None = None, max_content_size: int | None = None) -> ActionResult:
        """Update filter policy at runtime without restart."""
        ...
```

**Dashboard Snapshot** (the main payload frontends render):
```python
@dataclass
class DashboardSnapshot:
    """Complete daemon state in one serializable object."""

    # Identity
    mode: str                          # "auto" | "approve"
    pinner_address: str                # our Stellar address
    node_id: str                       # our IPFS peer ID
    uptime_seconds: int

    # Connectivity
    stellar_connected: bool
    stellar_latest_ledger: int
    kubo_connected: bool
    kubo_peer_count: int

    # Wallet
    wallet: WalletSnapshot

    # Activity summary
    offers_seen: int
    offers_accepted: int
    offers_rejected: int
    offers_awaiting_approval: int      # 0 in auto mode
    pins_active: int                   # CIDs currently pinned
    claims_completed: int

    # Earnings
    earnings: EarningsSnapshot

    # Live queues
    approval_queue: list[OfferSnapshot]   # empty in auto mode
    active_operations: list[OperationSnapshot]  # currently pinning/claiming

    # Recent activity (last N events for a feed/log view)
    recent_activity: list[ActivityEntry]

    # Contract state
    contract: ContractSnapshot
```

```python
@dataclass
class OfferSnapshot:
    """A PIN offer as seen by the frontend."""
    slot_id: int
    cid: str
    filename: str             # original filename (display only)
    gateway: str
    offer_price: int              # stroops
    offer_price_xlm: str          # human-readable "0.01 XLM"
    pin_qty: int
    pins_remaining: int
    publisher: str
    status: str                   # pending | awaiting_approval | pinning | pinned | claimed | rejected | expired | failed
    net_profit: int               # offer_price minus estimated tx fee
    expires_in_seconds: int | None  # countdown until slot expiry, None if unknown
    created_at: str               # ISO 8601
    updated_at: str               # ISO 8601

@dataclass
class WalletSnapshot:
    address: str
    balance_stroops: int
    balance_xlm: str              # "123.456 XLM"
    can_cover_tx: bool            # enough for at least one collect_pin() tx
    estimated_tx_fee: int         # current estimated tx fee in stroops

@dataclass
class EarningsSnapshot:
    total_earned_stroops: int
    total_earned_xlm: str
    earned_24h_stroops: int
    earned_24h_xlm: str
    earned_7d_stroops: int
    earned_7d_xlm: str
    earned_30d_stroops: int
    earned_30d_xlm: str
    claims_count: int
    average_per_claim_stroops: int

@dataclass
class ContractSnapshot:
    contract_id: str
    pin_fee: int
    min_offer_price: int
    min_pin_qty: int
    max_cycles: int
    pinner_stake: int
    our_pinner: PinnerSnapshot | None  # None if not registered
    slots: list[SlotSnapshot]
    available_slots: int

@dataclass
class PinnerSnapshot:
    address: str
    node_id: str
    multiaddr: str
    min_price: int
    pins_completed: int
    flags: int
    staked: int
    active: bool

@dataclass
class SlotSnapshot:
    slot_id: int
    active: bool
    publisher: str | None
    offer_price: int | None
    pin_qty: int | None
    pins_remaining: int | None
    expired: bool
    claimed_by_us: bool

@dataclass
class OperationSnapshot:
    """An in-flight pin or claim operation."""
    slot_id: int
    cid: str
    stage: str                    # "fetching_gateway" | "adding_to_kubo" | "pinning" | "verifying" | "claiming"
    progress_pct: int | None      # 0-100 if known
    started_at: str

@dataclass
class ActivityEntry:
    """A single line in the activity feed."""
    timestamp: str                # ISO 8601
    event_type: str               # "offer_seen" | "offer_accepted" | "pin_started" | "pin_completed" | "claim_submitted" | "claim_confirmed" | "offer_rejected" | "offer_expired" | "error"
    slot_id: int | None
    cid: str | None
    amount: int | None            # stroops, for earnings events
    message: str                  # human-readable summary

@dataclass
class ActionResult:
    success: bool
    message: str
```

**Serialization**: All snapshot dataclasses serialize to JSON via a single `to_dict()` method (or `dataclasses.asdict()`). Frontends receive plain JSON - no Python-specific types leak through.

**Polling vs Push**: The Data API is pull-based (frontend calls `get_dashboard()` on an interval). A future enhancement could add WebSocket push for real-time updates, but polling is sufficient for v1 and keeps the daemon simple.

---

## Daemon Main Loop

The main loop handles both modes. The difference is a single branch after filtering.

```python
async def run(config: DaemonConfig) -> None:
    """Main daemon loop. Handles both auto and approve modes."""

    store = SQLiteStateStore(config.db_path)
    poller = SorobanEventPoller(config.soroban_rpc, config.contract_id, store)
    offer_filter = PriceOfferFilter(store)
    executor = KuboPinExecutor(config.kubo_rpc_url)
    submitter = SorobanClaimSubmitter(config.soroban_rpc, config.contract_id, config.keypair)
    mode_ctrl = ModeController(store)
    data_api = DataAggregator(store, executor, submitter, mode_ctrl)

    # Resume any interrupted pin operations from last run
    await resume_interrupted(store, executor, submitter)

    while True:
        try:
            # 1. Poll for new events
            events = await poller.poll()

            for event in events:
                if isinstance(event, PinEvent):
                    store.save_offer(event)
                    store.log_activity('offer_seen', f'PIN offer: slot {event.slot_id}, {event.offer_price} stroops', slot_id=event.slot_id, cid=event.cid)

                    # 2. Filter
                    result = await offer_filter.evaluate(event)
                    if not result.accepted:
                        store.update_offer_status(event.slot_id, 'rejected', reject_reason=result.reason)
                        store.log_activity('offer_rejected', f'Rejected: {result.reason}', slot_id=event.slot_id)
                        continue

                    # 3. Mode branch
                    mode = mode_ctrl.get_mode()

                    if mode == DaemonMode.APPROVE:
                        # Queue for frontend approval
                        store.update_offer_status(event.slot_id, 'awaiting_approval')
                        store.log_activity('offer_queued', f'Queued for approval: slot {event.slot_id}', slot_id=event.slot_id, cid=event.cid)
                        continue

                    # AUTO mode: pin and claim immediately
                    await _execute_pin_and_claim(event, store, executor, submitter)

                elif isinstance(event, UnpinEvent):
                    store.update_offer_status(event.slot_id, 'expired')
                    store.log_activity('offer_expired', f'Slot {event.slot_id} freed', slot_id=event.slot_id)

            # 4. Process any newly approved offers (from frontend)
            approved = store.get_offers_by_status('approved')
            for offer in approved:
                event = offer.to_pin_event()
                await _execute_pin_and_claim(event, store, executor, submitter)

            # 5. Expire stale approval queue entries
            await _expire_stale_approvals(store)

            # 6. Wait before next poll
            await asyncio.sleep(config.poll_interval)

        except Exception as e:
            log.error(f"Main loop error: {e}")
            store.log_activity('error', str(e))
            await asyncio.sleep(config.error_backoff)


async def _execute_pin_and_claim(event: PinEvent, store, executor, submitter) -> None:
    """Fetch content from gateway, pin locally, then collect payment.
    Used by both auto mode and post-approval."""

    store.update_offer_status(event.slot_id, 'pinning')
    store.log_activity('pin_started', f'Fetching {event.cid} from {event.gateway}',
                       slot_id=event.slot_id, cid=event.cid)

    # Fetch from gateway → add to Kubo → pin (3-step pipeline)
    pin_result = await executor.pin(event.cid, event.gateway)
    if not pin_result.success:
        store.update_offer_status(event.slot_id, 'failed', reject_reason=pin_result.error)
        store.log_activity('pin_failed', f'Pin failed: {pin_result.error}', slot_id=event.slot_id, cid=event.cid)
        return

    store.save_pin(event.cid, event.slot_id, pin_result.bytes_pinned)
    store.update_offer_status(event.slot_id, 'pinned')
    store.log_activity('pin_completed', f'Pinned {event.cid} ({pin_result.bytes_pinned} bytes)', slot_id=event.slot_id, cid=event.cid)

    # Collect payment
    store.update_offer_status(event.slot_id, 'claiming')
    claim_result = await submitter.submit_claim(event.slot_id)
    if claim_result.success:
        store.save_claim(claim_result)
        store.update_offer_status(event.slot_id, 'claimed')
        store.log_activity('claim_confirmed', f'Earned {claim_result.amount_earned} stroops', slot_id=event.slot_id, cid=event.cid, amount=claim_result.amount_earned)
    else:
        store.update_offer_status(event.slot_id, 'claim_failed', reject_reason=claim_result.error)
        store.log_activity('claim_failed', f'Claim failed: {claim_result.error}', slot_id=event.slot_id, cid=event.cid)
```

### Approval Flow (Semi-Autonomous)

When a frontend approves offers via the Data API:

```python
# Inside DataAggregator.approve_offers():
async def approve_offers(self, slot_ids: list[int]) -> list[ActionResult]:
    results = []
    for slot_id in slot_ids:
        offer = self.store.get_offer(slot_id)
        if offer and offer.status == 'awaiting_approval':
            # Verify slot is still active on-chain before committing
            if await self.filter.verify_slot_active(slot_id):
                self.store.update_offer_status(slot_id, 'approved')
                results.append(ActionResult(success=True, message=f'Slot {slot_id} approved'))
            else:
                self.store.update_offer_status(slot_id, 'expired')
                results.append(ActionResult(success=False, message=f'Slot {slot_id} expired'))
        else:
            results.append(ActionResult(success=False, message=f'Slot {slot_id} not in approval queue'))
    return results
```

The main loop picks up `approved` offers on its next iteration and executes pin + claim.

### Crash Recovery

On startup, the daemon checks the state DB for interrupted operations:
- **Status `pinning`**: Content download was interrupted. Re-attempt the pin.
- **Status `pinned` or `claiming`**: Pin succeeded but `collect_pin()` was never sent or didn't confirm. Re-submit.
- **Status `claim_failed`**: Previous collect attempt failed. Retry (may succeed if it was a transient error, or slot may have expired).
- **Status `awaiting_approval`**: Left as-is. Frontend will see them in the queue.
- **Status `approved`**: Pin + claim will execute on next loop iteration.

---

## Configuration

```toml
[daemon]
mode = "auto"              # "auto" (fully autonomous) or "approve" (semi-autonomous, queues for frontend)
poll_interval = 5          # seconds between event polls
error_backoff = 30         # seconds to wait after an error
log_level = "info"
api_socket = "~/.hvym_pinner/api.sock"  # Unix socket / named pipe for frontend IPC

[stellar]
network = "testnet"
rpc_url = "https://soroban-testnet.stellar.org"
# Contract IDs loaded from ../pintheon_contracts/deployments.json by default
# Override here only if needed:
# contract_id = "CCEDYFIHUCJFITWEOT7BWUO2HBQQ72L244ZXQ4YNOC6FYRDN3MKDQFK7"
deployments_path = "../pintheon_contracts/deployments.json"
keypair_secret = ""        # or use HVYM_PINNER_SECRET env var
# keypair_file = "~/.hvym_pinner/secret.key"  # alternative

[ipfs]
kubo_rpc_url = "http://127.0.0.1:5001"
pin_timeout = 60           # seconds
max_content_size = 1073741824  # 1 GB
fetch_retries = 3

[policy]
min_price = 100            # minimum stroops per pin to accept
# max_concurrent_pins = 3  # future: limit parallel pin operations

[storage]
db_path = "~/.hvym_pinner/state.db"

[hunter]
enabled = false                        # enable if operator is also a Pintheon publisher
cycle_interval = 3600                  # seconds between verification cycles (1 hour)
check_timeout = 30                     # seconds per individual pinner check
max_concurrent_checks = 5             # parallel verifications per cycle
failure_threshold = 3                  # consecutive failures before auto-flagging
cooldown_after_flag = 86400           # seconds wait after flagging before re-checking (24h)
pinner_cache_ttl = 3600               # seconds before refreshing on-chain pinner info
verification_methods = ["dht_provider", "bitswap"]
```

---

## Package Structure

```
hvym_pinner/
├── pyproject.toml
├── README.md
├── HVYM_PINNER.md              # This document
├── src/
│   └── hvym_pinner/
│       ├── __init__.py
│       ├── __main__.py          # CLI entry point: `python -m hvym_pinner`
│       ├── cli.py               # CLI argument parsing (click or argparse)
│       ├── config.py            # Configuration loading (TOML + env vars)
│       ├── daemon.py            # Main loop orchestration (both modes)
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   ├── events.py        # PinEvent, UnpinEvent, PinnedEvent dataclasses
│       │   ├── records.py       # OfferRecord, ClaimResult, PinResult, etc.
│       │   ├── snapshots.py     # DashboardSnapshot, OfferSnapshot, WalletSnapshot, etc.
│       │   ├── hunter.py        # TrackedPin, VerificationResult, HunterSummary, FlagResult, etc.
│       │   └── config.py        # DaemonConfig, DaemonMode, ScheduleConfig dataclass/enum
│       │
│       ├── interfaces/
│       │   ├── __init__.py
│       │   ├── poller.py        # EventPoller protocol
│       │   ├── filter.py        # OfferFilter protocol
│       │   ├── executor.py      # PinExecutor protocol
│       │   ├── submitter.py     # ClaimSubmitter protocol
│       │   ├── store.py         # StateStore protocol
│       │   ├── data_api.py      # DataAPI protocol (frontend bridge)
│       │   ├── mode.py          # ModeController protocol
│       │   ├── hunter.py        # CIDHunter orchestrator protocol
│       │   ├── verifier.py      # PinVerifier protocol
│       │   ├── scheduler.py     # VerificationScheduler protocol
│       │   └── flag.py          # FlagSubmitter protocol
│       │
│       ├── stellar/
│       │   ├── __init__.py
│       │   ├── poller.py        # SorobanEventPoller (get_events() + event XDR decoding)
│       │   ├── submitter.py     # SorobanClaimSubmitter (uses hvym_pin_service bindings)
│       │   ├── contract.py      # Contract query wrappers (uses hvym_pin_service bindings)
│       │   └── wallet.py        # Wallet balance queries (stellar-sdk account API)
│       │
│       ├── ipfs/
│       │   ├── __init__.py
│       │   └── kubo.py          # KuboPinExecutor implementation (HTTP RPC)
│       │
│       ├── policy/
│       │   ├── __init__.py
│       │   ├── filter.py        # PriceOfferFilter implementation
│       │   └── mode.py          # ModeController implementation
│       │
│       ├── storage/
│       │   ├── __init__.py
│       │   └── sqlite.py        # SQLiteStateStore implementation
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── aggregator.py    # DataAggregator: builds snapshots from store + live state
│       │   └── ipc.py           # IPC server (Unix socket / named pipe for frontend clients)
│       │
│       └── hunter/
│           ├── __init__.py
│           ├── orchestrator.py  # CIDHunterOrchestrator: ties subsystems together
│           ├── verifier.py      # KuboPinVerifier: DHT + Bitswap + retrieval checks
│           ├── scheduler.py     # PeriodicVerificationScheduler: cycle management
│           ├── flag.py          # SorobanFlagSubmitter: flag_pinner() transactions
│           └── registry.py      # PinnerRegistryCache: cached on-chain pinner info
│
├── tests/
│   ├── conftest.py              # Shared fixtures
│   ├── test_poller.py
│   ├── test_filter.py
│   ├── test_executor.py
│   ├── test_submitter.py
│   ├── test_store.py
│   ├── test_data_api.py         # Data aggregation + snapshot tests
│   ├── test_mode_controller.py  # Mode switching + approval flow
│   ├── test_daemon.py           # Integration: full loop with mocks (both modes)
│   ├── test_event_decode.py     # Event XDR deserialization
│   ├── test_hunter_verifier.py  # Verification engine (DHT, Bitswap mocks)
│   ├── test_hunter_scheduler.py # Verification cycle scheduling + failure threshold
│   ├── test_hunter_flag.py      # Flag submission + bounty handling
│   └── test_hunter_integration.py # Full hunter flow: track -> verify -> flag
│
└── config/
    └── config.example.toml      # Example configuration
```

---

## Dependencies

```toml
[project]
name = "hvym-pinner"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "stellar-sdk>=13.0.0",       # Stellar/Soroban interaction (must match bindings version)
    "httpx>=0.27.0",             # Async HTTP client (Kubo RPC + gateway fetches)
    "aiosqlite>=0.20.0",         # Async SQLite
    "tomli>=2.0.0",              # TOML config parsing (stdlib in 3.11+ but explicit)
    "click>=8.1.0",              # CLI framework
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "ruff>=0.4.0",
]

[project.scripts]
hvym-pinner = "hvym_pinner.cli:main"
```

**Contract bindings dependency**: The `hvym_pin_service` bindings from `../pintheon_contracts/bindings/` are imported directly (path-based dependency or copied into the package). These are auto-generated and depend on `stellar-sdk>=13.0.0`.

---

## Registration Flow (One-Time Setup)

Before the daemon can earn, the operator must register as a pinner on-chain:

```
1. Operator installs hvym-pinner
2. Operator configures Stellar keypair + Kubo node
3. Operator runs: `hvym-pinner register`
   - Calls join_as_pinner(caller, node_id, multiaddr, min_price)
   - Pays join_fee + pinner_stake
   - Stores registration confirmation locally
4. Operator runs: `hvym-pinner start`
   - Daemon begins autonomous operation
```

**CLI commands**:
```
hvym-pinner register                  # Register as pinner on the contract
hvym-pinner start                     # Start daemon (uses mode from config)
hvym-pinner start --mode auto         # Override: start in fully autonomous mode
hvym-pinner start --mode approve      # Override: start in semi-autonomous mode
hvym-pinner status                    # Show daemon health + earnings (queries Data API)
hvym-pinner offers                    # List current offers (all statuses)
hvym-pinner offers --pending          # List offers awaiting approval
hvym-pinner approve <slot_id> [...]   # Approve one or more queued offers
hvym-pinner reject <slot_id> [...]    # Reject one or more queued offers
hvym-pinner approve --all             # Approve all queued offers
hvym-pinner mode auto                 # Switch to autonomous mode at runtime
hvym-pinner mode approve              # Switch to semi-autonomous mode at runtime
hvym-pinner stop                      # Graceful shutdown
hvym-pinner unregister                # Call leave_as_pinner(), reclaim stake
hvym-pinner update                    # Update pinner config (node_id, multiaddr, min_price)

# CID Hunter commands (requires hunter.enabled = true)
hvym-pinner hunter status             # Show hunter summary (tracked pins, suspects, flags)
hvym-pinner hunter tracked            # List all tracked (CID, pinner) pairs
hvym-pinner hunter suspects           # List pinners that failed verification
hvym-pinner hunter verify             # Trigger immediate verification cycle
hvym-pinner hunter verify <cid>       # Verify a specific CID across all its pinners
hvym-pinner hunter flag <pinner_addr> # Manually flag a pinner (bypass threshold)
hvym-pinner hunter log                # Show recent verification results
hvym-pinner hunter flags              # Show flag submission history
```

The CLI is itself a frontend client - it queries the Data API over IPC, same as any other frontend would. This ensures the CLI exercises the exact same data path that web/desktop/mobile clients use.

---

## Security Considerations

1. **Keypair management**: Secret key loaded from env var (`HVYM_PINNER_SECRET`) or encrypted keyfile. Never stored in config TOML.
2. **Gateway trust**: Content fetched from publisher gateways is untrusted. The executor must verify that the CID returned by Kubo's `/add` matches the expected CID from the event — this is the content-addressing guarantee. If the gateway serves wrong or malicious content, the CID won't match and the pin is aborted.
3. **Kubo RPC**: Runs on localhost only. No authentication needed for local daemon.
4. **Slot race conditions**: Multiple pinners may attempt to claim the same slot. `AlreadyClaimed` and `SlotFilled` errors are expected and handled gracefully.
5. **Content size limits**: Configurable max size to prevent pinning enormous files that exceed storage capacity.
6. **Rate limiting**: Respect Soroban RPC rate limits. Back off on 429 responses.

---

## Transpilation Strategy (Deferred)

The codebase is structured with explicit `Protocol` interfaces so that each component can be independently reimplemented in another language. When the time comes:

1. **Interface contracts** (`interfaces/`) define the API surface - these translate directly to traits (Rust), interfaces (TypeScript/Go)
2. **Models** (`models/`) are pure dataclasses - trivial to port
3. **Implementations** are isolated per subsystem - can be ported module by module
4. **No Python-specific magic**: No metaclasses, decorators-as-logic, or dynamic typing tricks

Target languages (in priority order, TBD):
- **TypeScript/Node.js**: Largest community, `@stellar/stellar-sdk` is well-maintained
- **Rust**: Performance, same ecosystem as the Soroban contract
- **Go**: Good for long-running daemons, strong concurrency primitives

---

## Implementation Phases

### Phase 1: Foundation
- [ ] Project scaffolding (pyproject.toml, package structure)
- [ ] Configuration loading (TOML + env vars + mode)
- [ ] All models and interfaces defined (including snapshots)
- [ ] DaemonMode enum and ModeController interface
- [ ] SQLite state store (full schema with approval queue + activity log)

### Phase 2: Stellar Integration (using generated bindings)
- [ ] Import `hvym_pin_service` bindings from `../pintheon_contracts/bindings/`
- [ ] Load contract IDs from `../pintheon_contracts/deployments.json`
- [ ] Event poller (subscribe to PIN/UNPIN/PINNED events via `SorobanServer.get_events()`)
- [ ] Event XDR deserialization (events are not covered by bindings)
- [ ] Wallet balance queries (via `stellar-sdk` account queries)
- [ ] Claim submitter using `ClientAsync.collect_pin()` (bindings handle tx building)
- [ ] Registration CLI using `Client.join_as_pinner()` (bindings handle tx building)
- [ ] Contract query helpers using bindings (`get_slot`, `is_slot_expired`, `get_pinner`, etc.)

### Phase 3: IPFS Integration
- [ ] Kubo HTTP RPC client (pin/unpin/verify)
- [ ] Gateway content fetching with size pre-check
- [ ] Pin executor (fetch + pin + verify pipeline)

### Phase 4: Daemon Loop (Both Modes)
- [ ] Main loop orchestration with mode branching
- [ ] ModeController implementation (auto vs approve routing)
- [ ] Offer filter (price, wallet balance, expiry, dedup)
- [ ] Approval queue management (queue, expire stale, process approved)
- [ ] Crash recovery (resume interrupted pins/claims, preserve approval queue)
- [ ] Runtime mode switching
- [ ] Graceful shutdown

### Phase 5: Data Aggregation API
- [ ] DataAggregator (builds snapshots from store + live Stellar/Kubo state)
- [ ] All snapshot dataclasses with JSON serialization
- [ ] IPC server (Unix socket / named pipe)
- [ ] Approve/reject actions via Data API
- [ ] Runtime policy updates via Data API
- [ ] Activity log feed

### Phase 6: CLI Frontend
- [ ] CLI commands that query Data API over IPC (status, offers, approve, reject, mode)
- [ ] Registration and unregistration commands
- [ ] Logging and output formatting
- [ ] Tests (unit + integration with mocked Soroban/Kubo, both modes)
- [ ] Example config + documentation

### Phase 7: CID Hunter
- [ ] Tracked pin registry (SQLite schema + store methods)
- [ ] Pinner registry cache (on-chain pinner info lookups)
- [ ] Pin verifier: DHT provider lookup via Kubo RPC
- [ ] Pin verifier: Bitswap want-have via Kubo RPC (connect + block get)
- [ ] Pin verifier: optional partial retrieval
- [ ] Verification scheduler (periodic cycles, concurrent checks, priority ordering)
- [ ] Failure threshold tracking + automatic flag_pinner() submission
- [ ] Flag submitter (Soroban transaction for flag_pinner())
- [ ] CID Hunter orchestrator (event ingestion, lifecycle, manual verify/flag)
- [ ] Hunter Data API extensions (summary, tracked pins, suspects, flag history, verification log)
- [ ] DashboardSnapshot integration (hunter field)
- [ ] Hunter configuration (TOML section, runtime updates)
- [ ] CLI commands for hunter (verify-now, flag, hunter-status)
- [ ] Tests (verifier mocks, scheduler cycles, flag flow, end-to-end)

---

## Open Questions

1. **Unpin policy**: When an `UNPIN` event fires (slot expired/cancelled), should we unpin the content from our Kubo node? Or keep it to support the network?
   - Recommendation: Keep by default, configurable. Storage is cheap; being a good IPFS citizen has value.

2. **Concurrent pinning**: Should the daemon process multiple PIN events in parallel?
   - Recommendation: Start sequential (simpler), add configurable concurrency in a later phase.

3. **Content size discovery**: Since we now fetch from the gateway, check `Content-Length` header from the gateway response before downloading the body to avoid a 10 GB download for a 100-stroop offer.
   - Recommendation: Yes, check `Content-Length` in the streaming response. Abort if it exceeds `max_content_size`.

4. **Earnings withdrawal**: Should the daemon auto-manage XLM balances, or leave that to the operator?
   - Recommendation: Operator manages. Daemon just earns and reports.

5. **Approval queue TTL**: In semi-autonomous mode, how aggressively should stale approvals expire? Should the daemon poll on-chain slot state for every queued offer, or estimate expiry from the ledger math?
   - Recommendation: Estimate from ledger math (cheap), with periodic on-chain verification for offers that are about to be approved.

6. **IPC transport**: Unix sockets work on Linux/macOS. For Windows, named pipes or localhost TCP? Or should we default to a localhost HTTP server for maximum cross-platform compatibility?
   - Recommendation: Start with localhost HTTP (simple, cross-platform, works with every frontend). Unix socket as optional optimization.

7. **Frontend notification**: Should the daemon support push notifications (e.g., WebSocket) for real-time updates to frontends, or is polling `get_dashboard()` sufficient?
   - Recommendation: Polling for v1. WebSocket push as a future enhancement.
