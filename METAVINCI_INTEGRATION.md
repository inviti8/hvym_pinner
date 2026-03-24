# Pinwheel — Metavinci Migration Guide

## What Changed in `hvym-pinwheel`

`DaemonConfig` now owns all network-dependent configuration via a `set_network()` method. Setting the network automatically resolves `rpc_url`, `network_passphrase`, `contract_id`, and `factory_contract_id` from a built-in registry.

### Before (broken for a package)

```python
# config.py loaded from a local sibling directory
deployments_path = "../pintheon_contracts/deployments.json"
_load_deployments(cfg, deployments_path)

# daemon.py had its own passphrase map
NETWORK_PASSPHRASES = {
    "testnet": "Test SDF Network ; September 2015",
    "mainnet": "Public Global Stellar Network ; September 2015",
}
passphrase = cfg.network_passphrase or NETWORK_PASSPHRASES.get(cfg.network, "")
```

### After

```python
# models/config.py — NETWORK_DEFAULTS built into the package
NETWORK_DEFAULTS = {
    "testnet": {
        "rpc_url": "https://soroban-testnet.stellar.org",
        "network_passphrase": "Test SDF Network ; September 2015",
        "contract_id": "CCEDYFIHUCJFITWEOT7BWUO2HBQQ72L244ZXQ4YNOC6FYRDN3MKDQFK7",
        "factory_contract_id": "CACBN6G2EPPLAQORDB3LXN3SULGVYBAETFZTNYTNDQ77B7JFRIBT66V2",
    },
    "mainnet": {
        "rpc_url": "https://soroban.stellar.org",
        "network_passphrase": "Public Global Stellar Network ; September 2015",
        "contract_id": "CAWZQ2AWO4H5YCWUHCMGADLZJ4P45PF7XNMFK3AM5W3XTQ2DPZQCK36G",
        "factory_contract_id": "CAPTUV4EPELHHALQRMMF3RQ5XDE5KV6AAFIGYOKOZ6O7Y7SLPFHAAGA7",
    },
}

# DaemonConfig resolves on instantiation and can switch at runtime
cfg = DaemonConfig()                    # testnet by default
cfg.set_network("mainnet")              # all 4 fields update atomically
cfg.rpc_url = "http://custom:1234"      # individual overrides still work
```

### Removed exports

| Symbol | Was in | Status |
|--------|--------|--------|
| `NETWORK_PASSPHRASES` | `daemon.py` | **Deleted** — use `cfg.network_passphrase` or `NETWORK_DEFAULTS` |
| `CONTRACT_REGISTRY` | `config.py` | **Deleted** — replaced by `NETWORK_DEFAULTS` in `models/config.py` |
| `_load_deployments()` | `config.py` | **Deleted** — no more `deployments.json` dependency |

### New public API

| Symbol | Location | Purpose |
|--------|----------|---------|
| `NETWORK_DEFAULTS` | `hvym_pinner.models.config` | Read-only dict of per-network defaults |
| `DaemonConfig.set_network(name)` | `hvym_pinner.models.config` | Set network + resolve all dependent fields |

---

## What Metavinci Needs to Change

### 1. `build_pinner_config()` — simplify

The config builder no longer needs its own RPC URL/passphrase dicts or contract ID lookup. `set_network()` handles all of that.

**Before:**

```python
def build_pinner_config(
    keypair_secret: str,
    network: str = "testnet",
    contract_id: str = "",
    factory_contract_id: str = "",
    mode: str = "auto",
    min_price: int = 10_000_000,
    db_path: str = "",
    hunter_enabled: bool = False,
) -> 'DaemonConfig':
    from hvym_pinner.models.config import DaemonConfig, DaemonMode, HunterConfig

    rpc_urls = {
        "testnet": "https://soroban-testnet.stellar.org",
        "mainnet": "https://soroban.stellar.org",
    }
    passphrases = {
        "testnet": "Test SDF Network ; September 2015",
        "mainnet": "Public Global Stellar Network ; September 2015",
    }

    if not db_path:
        from pathlib import Path
        db_path = str(Path.home() / "pintheon_data" / "pinner.db")

    return DaemonConfig(
        mode=DaemonMode(mode),
        rpc_url=rpc_urls.get(network, rpc_urls["testnet"]),
        network_passphrase=passphrases.get(network, passphrases["testnet"]),
        network=network,
        contract_id=contract_id,
        factory_contract_id=factory_contract_id,
        keypair_secret=keypair_secret,
        kubo_rpc_url="http://127.0.0.1:5001",
        db_path=db_path,
        min_price=min_price,
        hunter=HunterConfig(enabled=hunter_enabled),
    )
```

**After:**

```python
def build_pinner_config(
    keypair_secret: str,
    network: str = "testnet",
    mode: str = "auto",
    min_price: int = 10_000_000,
    db_path: str = "",
    hunter_enabled: bool = False,
) -> 'DaemonConfig':
    """Build a DaemonConfig from Metavinci's current state.

    Network-dependent fields (rpc_url, passphrase, contract IDs) are
    resolved automatically by DaemonConfig.set_network().
    """
    from hvym_pinner.models.config import DaemonConfig, DaemonMode, HunterConfig

    if not db_path:
        from pathlib import Path
        db_path = str(Path.home() / "pintheon_data" / "pinner.db")

    cfg = DaemonConfig(
        mode=DaemonMode(mode),
        keypair_secret=keypair_secret,
        kubo_rpc_url="http://127.0.0.1:5001",
        db_path=db_path,
        min_price=min_price,
        hunter=HunterConfig(enabled=hunter_enabled),
    )
    cfg.set_network(network)
    return cfg
```

**What changed:**
- Removed `rpc_urls` and `passphrases` dicts (now built into the package)
- Removed `contract_id` and `factory_contract_id` parameters (auto-resolved by `set_network()`)
- Call `cfg.set_network(network)` after construction to populate all network fields

### 2. `_start_pinwheel()` — remove contract ID lookup

The `_get_pinwheel_contract_id()` and `_get_pinwheel_factory_id()` helpers are no longer needed. `build_pinner_config()` gets them from `set_network()`.

**Before:**

```python
def _start_pinwheel(self):
    # ...
    contract_id = self._get_pinwheel_contract_id()
    factory_id = self._get_pinwheel_factory_id()

    if not contract_id:
        self.open_msg_dialog('No pin service contract ID configured.')
        return

    config = build_pinner_config(
        keypair_secret=secret,
        network=self.PINTHEON_NETWORK,
        contract_id=contract_id,
        factory_contract_id=factory_id,
        mode=self.PINWHEEL_MODE,
        min_price=self._get_pinwheel_min_price(),
    )
    # ...
```

**After:**

```python
def _start_pinwheel(self):
    # ...
    config = build_pinner_config(
        keypair_secret=secret,
        network=self.PINTHEON_NETWORK,
        mode=self.PINWHEEL_MODE,
        min_price=self._get_pinwheel_min_price(),
    )
    # ...
```

**What changed:**
- Removed `_get_pinwheel_contract_id()` call and guard
- Removed `_get_pinwheel_factory_id()` call
- Removed `contract_id` / `factory_contract_id` keyword args

### 3. Delete `_get_pinwheel_contract_id()` and `_get_pinwheel_factory_id()`

These methods are no longer needed. Contract IDs are resolved by the package via `set_network()`. Delete both methods entirely.

### 4. `_ensure_pinwheel_registered()` — use DaemonConfig

**Before:**

```python
def _ensure_pinwheel_registered(self, secret: str, contract_id: str) -> bool:
    from hvym_pinner.stellar.queries import ContractQueries
    from stellar_sdk import Keypair

    kp = Keypair.from_secret(secret)
    passphrase = ("Test SDF Network ; September 2015" if self.PINTHEON_NETWORK == "testnet"
                  else "Public Global Stellar Network ; September 2015")
    rpc_url = ("https://soroban-testnet.stellar.org" if self.PINTHEON_NETWORK == "testnet"
               else "https://soroban.stellar.org")

    queries = ContractQueries(contract_id, rpc_url, passphrase)
    # ...
```

**After:**

```python
def _ensure_pinwheel_registered(self, secret: str) -> bool:
    from hvym_pinner.models.config import DaemonConfig, NETWORK_DEFAULTS
    from hvym_pinner.stellar.queries import ContractQueries
    from stellar_sdk import Keypair

    kp = Keypair.from_secret(secret)
    defaults = NETWORK_DEFAULTS[self.PINTHEON_NETWORK]

    queries = ContractQueries(
        defaults["contract_id"],
        defaults["rpc_url"],
        defaults["network_passphrase"],
    )
    # ...
```

**What changed:**
- Removed `contract_id` parameter (looked up from `NETWORK_DEFAULTS`)
- Removed manual passphrase/rpc_url ternaries
- Import `NETWORK_DEFAULTS` from the package

### 5. `_register_pinwheel()` — same simplification

**Before:**

```python
def _register_pinwheel(self, secret: str, contract_id: str) -> bool:
    kp = Keypair.from_secret(secret)
    passphrase = ("Test SDF Network ; September 2015" if self.PINTHEON_NETWORK == "testnet"
                  else "Public Global Stellar Network ; September 2015")
    rpc_url = ("https://soroban-testnet.stellar.org" if self.PINTHEON_NETWORK == "testnet"
               else "https://soroban.stellar.org")

    client = ClientAsync(contract_id, rpc_url, passphrase)
    # ...
```

**After:**

```python
def _register_pinwheel(self, secret: str) -> bool:
    from hvym_pinner.models.config import NETWORK_DEFAULTS

    kp = Keypair.from_secret(secret)
    defaults = NETWORK_DEFAULTS[self.PINTHEON_NETWORK]

    client = ClientAsync(
        defaults["contract_id"],
        defaults["rpc_url"],
        defaults["network_passphrase"],
    )
    # ...
```

**What changed:**
- Removed `contract_id` parameter
- Replaced ternaries with `NETWORK_DEFAULTS` lookup

### 6. Update `_start_pinwheel()` registration call

Since `_ensure_pinwheel_registered` and `_register_pinwheel` no longer take `contract_id`:

**Before:**

```python
if not self._ensure_pinwheel_registered(secret, contract_id):
    return
```

**After:**

```python
if not self._ensure_pinwheel_registered(secret):
    return
```

### 7. Remove `pinwheel_contract_id` from TinyDB schema

The `pinwheel_contract_id` field in the `app_data` document is no longer needed. Contract IDs are resolved by the package.

**Before:**

```json
{
    "type": "app_data",
    "pinwheel_wallet": "GABCDEF...",
    "pinwheel_mode": "auto",
    "pinwheel_min_price": 10000000,
    "pinwheel_contract_id": "",
    "pinwheel_auto_start": false,
    "pinwheel_hunter_enabled": false
}
```

**After:**

```json
{
    "type": "app_data",
    "pinwheel_wallet": "GABCDEF...",
    "pinwheel_mode": "auto",
    "pinwheel_min_price": 10000000,
    "pinwheel_auto_start": false,
    "pinwheel_hunter_enabled": false
}
```

### 8. Import change in `cli.py`-dependent code

If metavinci imports `NETWORK_PASSPHRASES` from `hvym_pinner.daemon`, that import is gone. Use `NETWORK_DEFAULTS` from `hvym_pinner.models.config` instead:

```python
# Before
from hvym_pinner.daemon import NETWORK_PASSPHRASES

# After
from hvym_pinner.models.config import NETWORK_DEFAULTS
# Access: NETWORK_DEFAULTS["testnet"]["network_passphrase"]
```

### 9. Runtime network switching

`DaemonConfig.set_network()` can now be called at runtime to switch between testnet and mainnet. If metavinci needs to support network switching while Pinwheel is running, it can update the config — though the daemon's components (poller, submitter, queries) are constructed at init time, so a restart is still required for the switch to take full effect.

```python
# Switch network (requires daemon restart to rebuild components)
config.set_network("mainnet")
self._stop_pinwheel()
self.pinwheel_worker = PinwheelWorker(config, parent=self)
self.pinwheel_worker.start()
```

---

## Summary of Deletions in Metavinci

| Code to delete | Reason |
|----------------|--------|
| `_get_pinwheel_contract_id()` | Contract IDs built into package |
| `_get_pinwheel_factory_id()` | Same |
| `rpc_urls` dict in `build_pinner_config` | Handled by `set_network()` |
| `passphrases` dict in `build_pinner_config` | Handled by `set_network()` |
| Passphrase/RPC ternaries in registration methods | Use `NETWORK_DEFAULTS` |
| `pinwheel_contract_id` TinyDB field | No longer needed |
| `from hvym_pinner.daemon import NETWORK_PASSPHRASES` | Deleted export |

## Summary of Additions in Metavinci

| Code to add | Where |
|-------------|-------|
| `from hvym_pinner.models.config import NETWORK_DEFAULTS` | Registration/query methods that need network-specific values outside of a `DaemonConfig` |
| `cfg.set_network(network)` | In `build_pinner_config()` after constructing `DaemonConfig` |
