"""Configuration models for the daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DaemonMode(str, Enum):
    """Operating mode for the pinner daemon."""

    AUTO = "auto"  # Pin + claim immediately
    APPROVE = "approve"  # Queue for frontend approval


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
    """Complete daemon configuration."""

    # Daemon
    mode: DaemonMode = DaemonMode.AUTO
    poll_interval: int = 5  # seconds
    error_backoff: int = 30  # seconds
    log_level: str = "info"

    # Stellar
    network: str = "testnet"
    rpc_url: str = "https://soroban-testnet.stellar.org"
    network_passphrase: str = "Test SDF Network ; September 2015"
    contract_id: str = ""  # hvym_pin_service contract ID
    factory_contract_id: str = ""  # hvym_pin_service_factory contract ID
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
