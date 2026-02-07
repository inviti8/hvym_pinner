"""CID Hunter data models for verification tracking and dispute submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class VerificationMethod(str, Enum):
    DHT_PROVIDER = "dht_provider"  # Quick: is pinner listed as provider?
    BITSWAP_WANT_HAVE = "bitswap"  # Definitive: does pinner respond HAVE?
    PARTIAL_RETRIEVAL = "retrieval"  # High-value: can we fetch a block?


# ---------------------------------------------------------------------------
# Verification results
# ---------------------------------------------------------------------------


@dataclass
class MethodResult:
    """Result from a single verification method."""

    method: str  # "dht_provider" | "bitswap" | "retrieval"
    passed: bool | None = None  # None if skipped or timed out
    detail: str = ""
    duration_ms: int = 0


@dataclass
class VerificationResult:
    """Composite result from the full verification pipeline."""

    cid: str
    pinner_node_id: str
    passed: bool
    method_used: str  # which method produced the final result
    methods_attempted: list[MethodResult] = field(default_factory=list)
    duration_ms: int = 0
    checked_at: str = ""  # ISO 8601


# ---------------------------------------------------------------------------
# Tracked pin state
# ---------------------------------------------------------------------------


@dataclass
class TrackedPin:
    """A (CID, pinner) pair we are monitoring for verification."""

    cid: str
    cid_hash: str  # SHA256 hex (matches on-chain cid_hash)
    pinner_address: str  # Stellar address
    pinner_node_id: str  # IPFS peer ID
    pinner_multiaddr: str  # IPFS multiaddress
    slot_id: int
    claimed_at: str = ""  # ISO 8601
    last_verified_at: str | None = None
    last_checked_at: str | None = None
    consecutive_failures: int = 0
    total_checks: int = 0
    total_failures: int = 0
    status: str = "tracking"  # tracking | verified | suspect | flag_submitted
    flagged_at: str | None = None
    flag_tx_hash: str | None = None


@dataclass
class PinnerInfo:
    """Cached on-chain pinner registry data needed for verification."""

    address: str
    node_id: str
    multiaddr: str
    active: bool
    cached_at: str = ""  # ISO 8601


# ---------------------------------------------------------------------------
# Flag submission
# ---------------------------------------------------------------------------


@dataclass
class FlagResult:
    """Result of a flag_pinner() transaction submission."""

    success: bool
    pinner_address: str
    flag_count: int | None = None  # pinner's flag count after our flag
    tx_hash: str | None = None
    error: str | None = None
    bounty_earned: int | None = None  # stroops, if threshold was hit


@dataclass
class FlagRecord:
    """Historical record of a flag we submitted."""

    pinner_address: str
    tx_hash: str = ""
    flag_count_after: int | None = None
    bounty_earned: int | None = None  # stroops
    submitted_at: str = ""  # ISO 8601


# ---------------------------------------------------------------------------
# Verification cycle
# ---------------------------------------------------------------------------


@dataclass
class CycleReport:
    """Results from a single verification cycle."""

    cycle_id: int = 0
    started_at: str = ""
    completed_at: str = ""
    total_checked: int = 0
    passed: int = 0
    failed: int = 0
    flagged: int = 0  # flags submitted this cycle
    skipped: int = 0  # already flagged, cooldown, etc.
    errors: int = 0  # network errors, timeouts
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Frontend snapshots
# ---------------------------------------------------------------------------


@dataclass
class HunterSummary:
    """CID Hunter status for the dashboard."""

    enabled: bool = False
    total_tracked_pins: int = 0
    verified_count: int = 0
    suspect_count: int = 0
    flagged_count: int = 0
    total_checks_lifetime: int = 0
    total_flags_lifetime: int = 0
    bounties_earned_stroops: int = 0
    bounties_earned_xlm: str = "0 XLM"
    last_cycle_at: str | None = None
    next_cycle_at: str | None = None
    cycle_interval_seconds: int = 3600


@dataclass
class TrackedPinSnapshot:
    """A tracked (CID, pinner) pair for the frontend."""

    cid: str
    cid_short: str  # first 16 chars
    pinner_address: str
    pinner_address_short: str  # first 8 chars
    pinner_node_id: str
    status: str
    consecutive_failures: int
    failure_threshold: int  # from config, so frontend can show "2/5"
    total_checks: int
    total_failures: int
    last_verified_at: str | None = None
    last_checked_at: str | None = None
    flagged_at: str | None = None


@dataclass
class VerificationLogEntry:
    """Single verification check result for the activity feed."""

    cid: str
    pinner_address: str
    passed: bool
    method_used: str
    duration_ms: int = 0
    checked_at: str = ""
