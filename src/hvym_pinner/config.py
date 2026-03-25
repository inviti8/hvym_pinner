"""Configuration loading: TOML file + environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from hvym_pinner.models.config import DaemonConfig, DaemonMode, HunterConfig


def load_config(
    config_path: str | Path | None = None,
    env_prefix: str = "HVYM_PINNER_",
) -> DaemonConfig:
    """Load daemon configuration from TOML file and environment variables.

    Priority (highest wins):
        1. Environment variables (HVYM_PINNER_SECRET, etc.)
        2. TOML config file
        3. Network defaults (via DaemonConfig.set_network)
    """
    raw: dict = {}
    if config_path is not None:
        p = Path(config_path).expanduser()
        if p.exists():
            with open(p, "rb") as f:
                raw = tomllib.load(f)

    cfg = DaemonConfig()

    # ── Daemon section ─────────────────────────────────────
    daemon = raw.get("daemon", {})
    if mode_str := daemon.get("mode"):
        cfg.mode = DaemonMode(mode_str)
    if v := daemon.get("poll_interval"):
        cfg.poll_interval = int(v)
    if v := daemon.get("error_backoff"):
        cfg.error_backoff = int(v)
    if v := daemon.get("log_level"):
        cfg.log_level = str(v)

    # ── Stellar section ────────────────────────────────────
    # Apply network first so all dependent fields get their defaults,
    # then overlay any explicit per-field overrides from the TOML.
    stellar = raw.get("stellar", {})
    if v := stellar.get("network"):
        cfg.set_network(str(v))
    if v := stellar.get("rpc_url"):
        cfg.rpc_url = str(v)
    if v := stellar.get("contract_id"):
        cfg.contract_id = str(v)
    if v := stellar.get("keypair_secret"):
        cfg.keypair_secret = str(v)
    if v := stellar.get("network_passphrase"):
        cfg.network_passphrase = str(v)

    # ── IPFS section ───────────────────────────────────────
    ipfs = raw.get("ipfs", {})
    if v := ipfs.get("kubo_rpc_url"):
        cfg.kubo_rpc_url = str(v)
    if v := ipfs.get("pin_timeout"):
        cfg.pin_timeout = int(v)
    if v := ipfs.get("max_content_size"):
        cfg.max_content_size = int(v)
    if v := ipfs.get("fetch_retries"):
        cfg.fetch_retries = int(v)

    # ── Policy section ─────────────────────────────────────
    policy = raw.get("policy", {})
    if v := policy.get("min_price"):
        cfg.min_price = int(v)

    # ── Storage section ────────────────────────────────────
    storage = raw.get("storage", {})
    if v := storage.get("db_path"):
        cfg.db_path = str(v)

    # ── Hunter section ─────────────────────────────────────
    hunter_raw = raw.get("hunter", {})
    cfg.hunter = HunterConfig(
        enabled=hunter_raw.get("enabled", False),
        cycle_interval=hunter_raw.get("cycle_interval", 3600),
        check_timeout=hunter_raw.get("check_timeout", 30),
        max_concurrent_checks=hunter_raw.get("max_concurrent_checks", 5),
        failure_threshold=hunter_raw.get("failure_threshold", 3),
        cooldown_after_flag=hunter_raw.get("cooldown_after_flag", 86400),
        pinner_cache_ttl=hunter_raw.get("pinner_cache_ttl", 3600),
        verification_methods=hunter_raw.get(
            "verification_methods", ["dht_provider", "bitswap"]
        ),
    )

    # ── Environment variable overrides (highest priority) ──
    # Network env var triggers set_network() so all dependent fields update,
    # then individual env vars can still override specific fields.
    if net := os.environ.get(f"{env_prefix}NETWORK"):
        cfg.set_network(net)
    if secret := os.environ.get(f"{env_prefix}SECRET"):
        cfg.keypair_secret = secret
    if rpc := os.environ.get(f"{env_prefix}RPC_URL"):
        cfg.rpc_url = rpc
    if cid := os.environ.get(f"{env_prefix}CONTRACT_ID"):
        cfg.contract_id = cid
    if mode_env := os.environ.get(f"{env_prefix}MODE"):
        cfg.mode = DaemonMode(mode_env)

    # Expand ~ in paths
    cfg.db_path = str(Path(cfg.db_path).expanduser())

    return cfg
