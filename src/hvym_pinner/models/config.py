"""Configuration models for the daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DaemonMode(str, Enum):
    """Operating mode for the pinner daemon."""

    AUTO = "auto"  # Pin + claim immediately
    APPROVE = "approve"  # Queue for frontend approval


# ── Network defaults ──────────────────────────────────────────────
# All network-dependent settings keyed by network name.
# Sources:
#   Mainnet: github.com/inviti8/pintheon_contracts/releases/tag/mainnet-release-v1.0.0
#   Testnet: github.com/inviti8/pintheon_contracts/releases/tag/deploy-alpha-v0.09-testnet
NETWORK_DEFAULTS: dict[str, dict[str, str]] = {
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


@dataclass
class ScheduleConfig:
    """CID Hunter verification scheduler configuration."""

    cycle_interval: int = 3600  # seconds between full verification cycles
    check_timeout: int = 30  # seconds per individual check
    max_concurrent_checks: int = 5
    failure_threshold: int = 3  # consecutive failures before flagging
    cooldown_after_flag: int = 86400  # seconds after flagging before re-checking


@dataclass
class HunterConfig:
    """CID Hunter module configuration."""

    enabled: bool = False
    cycle_interval: int = 3600
    check_timeout: int = 30
    max_concurrent_checks: int = 5
    failure_threshold: int = 3
    cooldown_after_flag: int = 86400
    pinner_cache_ttl: int = 3600
    verification_methods: list[str] = field(
        default_factory=lambda: ["dht_provider", "bitswap"]
    )

    def to_schedule_config(self) -> ScheduleConfig:
        return ScheduleConfig(
            cycle_interval=self.cycle_interval,
            check_timeout=self.check_timeout,
            max_concurrent_checks=self.max_concurrent_checks,
            failure_threshold=self.failure_threshold,
            cooldown_after_flag=self.cooldown_after_flag,
        )


@dataclass
class DaemonConfig:
    """Complete daemon configuration.

    Network-dependent fields (rpc_url, network_passphrase, contract_id,
    factory_contract_id) are automatically resolved from NETWORK_DEFAULTS
    when ``set_network()`` is called or at instantiation via ``__post_init__``.
    """

    # Daemon
    mode: DaemonMode = DaemonMode.AUTO
    poll_interval: int = 5  # seconds
    error_backoff: int = 30  # seconds
    log_level: str = "info"

    # Stellar — resolved by set_network()
    network: str = "testnet"
    rpc_url: str = ""
    network_passphrase: str = ""
    contract_id: str = ""
    factory_contract_id: str = ""
    keypair_secret: str = ""  # loaded from env var HVYM_PINNER_SECRET

    # IPFS
    kubo_rpc_url: str = "http://127.0.0.1:5001"
    pin_timeout: int = 60  # seconds
    max_content_size: int = 1_073_741_824  # 1 GB
    fetch_retries: int = 3

    # Policy
    min_price: int = 100  # minimum stroops per pin to accept

    # Storage
    db_path: str = "~/.hvym_pinner/state.db"

    # CID Hunter
    hunter: HunterConfig = field(default_factory=HunterConfig)

    def __post_init__(self) -> None:
        """Resolve network defaults on instantiation."""
        if not self.rpc_url:
            self.set_network(self.network)

    def set_network(self, network: str) -> None:
        """Set the network and update all network-dependent fields.

        Replaces rpc_url, network_passphrase, contract_id, and
        factory_contract_id with the defaults for *network*.
        Unknown network names clear the dependent fields.
        """
        self.network = network
        defaults = NETWORK_DEFAULTS.get(network, {})
        self.rpc_url = defaults.get("rpc_url", "")
        self.network_passphrase = defaults.get("network_passphrase", "")
        self.contract_id = defaults.get("contract_id", "")
        self.factory_contract_id = defaults.get("factory_contract_id", "")
