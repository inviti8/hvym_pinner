"""SQLite implementation of the StateStore protocol."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from hvym_pinner.models.events import PinEvent
from hvym_pinner.models.records import (
    ActivityRecord,
    ClaimResult,
    DaemonConfigRecord,
    EarningsSummary,
    OfferRecord,
    PinRecord,
)
from hvym_pinner.models.hunter import (
    CycleReport,
    FlagRecord,
    PinnerInfo,
    TrackedPin,
    VerificationResult,
)

SCHEMA = """
-- Event cursor for resumption
CREATE TABLE IF NOT EXISTS cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_ledger INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Daemon runtime config
CREATE TABLE IF NOT EXISTS daemon_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL DEFAULT 'auto',
    min_price INTEGER NOT NULL DEFAULT 100,
    max_content_size INTEGER NOT NULL DEFAULT 1073741824,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tracked offers
CREATE TABLE IF NOT EXISTS offers (
    slot_id INTEGER PRIMARY KEY,
    cid TEXT NOT NULL,
    gateway TEXT NOT NULL,
    offer_price INTEGER NOT NULL,
    pin_qty INTEGER NOT NULL,
    pins_remaining INTEGER NOT NULL,
    publisher TEXT NOT NULL,
    ledger_sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT,
    net_profit INTEGER,
    estimated_expiry TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);

-- Completed claims
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    cid TEXT NOT NULL,
    amount_earned INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_claims_claimed_at ON claims(claimed_at);

-- Pinned CIDs
CREATE TABLE IF NOT EXISTS pins (
    cid TEXT PRIMARY KEY,
    slot_id INTEGER,
    bytes_pinned INTEGER,
    pinned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Activity log
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    slot_id INTEGER,
    cid TEXT,
    amount INTEGER,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);

-- Hunter: tracked CIDs we published
CREATE TABLE IF NOT EXISTS tracked_cids (
    cid TEXT NOT NULL,
    cid_hash TEXT NOT NULL,
    slot_id INTEGER NOT NULL,
    publisher TEXT NOT NULL,
    gateway TEXT,
    pin_qty INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_cids_cid ON tracked_cids(cid);

-- Hunter: (CID, pinner) verification pairs
CREATE TABLE IF NOT EXISTS tracked_pins (
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
    status TEXT NOT NULL DEFAULT 'tracking',
    flagged_at TEXT,
    flag_tx_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cid, pinner_address)
);
CREATE INDEX IF NOT EXISTS idx_tracked_pins_status ON tracked_pins(status);

-- Hunter: verification log
CREATE TABLE IF NOT EXISTS verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    pinner_address TEXT NOT NULL,
    passed INTEGER NOT NULL,
    method_used TEXT NOT NULL,
    methods_attempted TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vlog_checked ON verification_log(checked_at);

-- Hunter: cycle history
CREATE TABLE IF NOT EXISTS verification_cycles (
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

-- Hunter: flag history
CREATE TABLE IF NOT EXISTS flag_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pinner_address TEXT NOT NULL,
    tx_hash TEXT,
    flag_count_after INTEGER,
    bounty_earned INTEGER,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_flags_pinner ON flag_history(pinner_address);

-- Hunter: pinner cache
CREATE TABLE IF NOT EXISTS pinner_cache (
    address TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    multiaddr TEXT NOT NULL,
    active INTEGER NOT NULL,
    cached_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStateStore:
    """SQLite-backed implementation of the StateStore protocol."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store not initialized. Call initialize() first."
        return self._db

    # ── Cursor ─────────────────────────────────────────────

    async def get_cursor(self) -> int | None:
        async with self.db.execute("SELECT last_ledger FROM cursor WHERE id=1") as cur:
            row = await cur.fetchone()
            return row["last_ledger"] if row else None

    async def set_cursor(self, ledger: int) -> None:
        await self.db.execute(
            "INSERT INTO cursor (id, last_ledger, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET last_ledger=excluded.last_ledger,"
            " updated_at=excluded.updated_at",
            (ledger, _now()),
        )
        await self.db.commit()

    # ── Daemon config ──────────────────────────────────────

    async def get_daemon_config(self) -> DaemonConfigRecord:
        async with self.db.execute("SELECT * FROM daemon_config WHERE id=1") as cur:
            row = await cur.fetchone()
            if row:
                return DaemonConfigRecord(
                    mode=row["mode"],
                    min_price=row["min_price"],
                    max_content_size=row["max_content_size"],
                )
        return DaemonConfigRecord()

    async def set_daemon_config(
        self,
        mode: str | None = None,
        min_price: int | None = None,
        max_content_size: int | None = None,
    ) -> None:
        current = await self.get_daemon_config()
        await self.db.execute(
            "INSERT INTO daemon_config (id, mode, min_price, max_content_size, updated_at)"
            " VALUES (1, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " mode=excluded.mode, min_price=excluded.min_price,"
            " max_content_size=excluded.max_content_size, updated_at=excluded.updated_at",
            (
                mode or current.mode,
                min_price if min_price is not None else current.min_price,
                max_content_size if max_content_size is not None else current.max_content_size,
                _now(),
            ),
        )
        await self.db.commit()

    # ── Offers ─────────────────────────────────────────────

    async def save_offer(self, event: PinEvent, status: str = "pending") -> None:
        now = _now()
        await self.db.execute(
            "INSERT OR REPLACE INTO offers"
            " (slot_id, cid, gateway, offer_price, pin_qty, pins_remaining,"
            "  publisher, ledger_sequence, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.slot_id, event.cid, event.gateway, event.offer_price,
                event.pin_qty, event.pin_qty, event.publisher,
                event.ledger_sequence, status, now, now,
            ),
        )
        await self.db.commit()

    async def get_offer(self, slot_id: int) -> OfferRecord | None:
        async with self.db.execute(
            "SELECT * FROM offers WHERE slot_id=?", (slot_id,)
        ) as cur:
            row = await cur.fetchone()
            return _row_to_offer(row) if row else None

    async def update_offer_status(
        self, slot_id: int, status: str, reject_reason: str | None = None
    ) -> None:
        if reject_reason:
            await self.db.execute(
                "UPDATE offers SET status=?, reject_reason=?, updated_at=? WHERE slot_id=?",
                (status, reject_reason, _now(), slot_id),
            )
        else:
            await self.db.execute(
                "UPDATE offers SET status=?, updated_at=? WHERE slot_id=?",
                (status, _now(), slot_id),
            )
        await self.db.commit()

    async def get_offers_by_status(self, status: str) -> list[OfferRecord]:
        async with self.db.execute(
            "SELECT * FROM offers WHERE status=? ORDER BY created_at", (status,)
        ) as cur:
            return [_row_to_offer(row) async for row in cur]

    async def get_approval_queue(self) -> list[OfferRecord]:
        return await self.get_offers_by_status("awaiting_approval")

    async def get_all_offers(self) -> list[OfferRecord]:
        async with self.db.execute("SELECT * FROM offers ORDER BY created_at") as cur:
            return [_row_to_offer(row) async for row in cur]

    # ── Claims & earnings ──────────────────────────────────

    async def save_claim(self, claim: ClaimResult) -> None:
        offer = await self.get_offer(claim.slot_id)
        cid = offer.cid if offer else ""
        await self.db.execute(
            "INSERT INTO claims (slot_id, cid, amount_earned, tx_hash, claimed_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (claim.slot_id, cid, claim.amount_earned or 0, claim.tx_hash or "", _now()),
        )
        await self.db.commit()

    async def get_earnings(self, since: str | None = None) -> EarningsSummary:
        now_dt = datetime.now(timezone.utc)
        total = await self._sum_earnings(None)
        e24h = await self._sum_earnings((now_dt - timedelta(hours=24)).isoformat())
        e7d = await self._sum_earnings((now_dt - timedelta(days=7)).isoformat())
        e30d = await self._sum_earnings((now_dt - timedelta(days=30)).isoformat())

        async with self.db.execute("SELECT COUNT(*) as c FROM claims") as cur:
            row = await cur.fetchone()
            count = row["c"] if row else 0

        return EarningsSummary(
            total_earned=total,
            earned_24h=e24h,
            earned_7d=e7d,
            earned_30d=e30d,
            claims_count=count,
        )

    async def _sum_earnings(self, since: str | None) -> int:
        if since:
            async with self.db.execute(
                "SELECT COALESCE(SUM(amount_earned), 0) as s FROM claims WHERE claimed_at >= ?",
                (since,),
            ) as cur:
                row = await cur.fetchone()
                return row["s"] if row else 0
        else:
            async with self.db.execute(
                "SELECT COALESCE(SUM(amount_earned), 0) as s FROM claims"
            ) as cur:
                row = await cur.fetchone()
                return row["s"] if row else 0

    # ── Pins ───────────────────────────────────────────────

    async def save_pin(self, cid: str, slot_id: int, bytes_pinned: int | None) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO pins (cid, slot_id, bytes_pinned, pinned_at)"
            " VALUES (?, ?, ?, ?)",
            (cid, slot_id, bytes_pinned, _now()),
        )
        await self.db.commit()

    async def is_cid_pinned(self, cid: str) -> bool:
        async with self.db.execute("SELECT 1 FROM pins WHERE cid=?", (cid,)) as cur:
            return await cur.fetchone() is not None

    async def get_all_pins(self) -> list[PinRecord]:
        async with self.db.execute("SELECT * FROM pins ORDER BY pinned_at") as cur:
            return [
                PinRecord(
                    cid=row["cid"],
                    slot_id=row["slot_id"],
                    bytes_pinned=row["bytes_pinned"],
                    pinned_at=row["pinned_at"],
                )
                async for row in cur
            ]

    # ── Activity log ───────────────────────────────────────

    async def log_activity(
        self,
        event_type: str,
        message: str,
        slot_id: int | None = None,
        cid: str | None = None,
        amount: int | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO activity_log (event_type, slot_id, cid, amount, message, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, slot_id, cid, amount, message, _now()),
        )
        await self.db.commit()

    async def get_recent_activity(self, limit: int = 50) -> list[ActivityRecord]:
        async with self.db.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [
                ActivityRecord(
                    id=row["id"],
                    event_type=row["event_type"],
                    slot_id=row["slot_id"],
                    cid=row["cid"],
                    amount=row["amount"],
                    message=row["message"],
                    created_at=row["created_at"],
                )
                async for row in cur
            ]

    # ── Hunter: tracked pins ───────────────────────────────

    async def save_tracked_cid(
        self, cid: str, cid_hash: str, slot_id: int, publisher: str,
        gateway: str | None, pin_qty: int,
    ) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO tracked_cids"
            " (cid, cid_hash, slot_id, publisher, gateway, pin_qty, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, cid_hash, slot_id, publisher, gateway, pin_qty, _now()),
        )
        await self.db.commit()

    async def get_tracked_cid_by_slot(self, slot_id: int) -> str | None:
        """Look up a tracked CID by slot ID."""
        async with self.db.execute(
            "SELECT cid FROM tracked_cids WHERE slot_id=?", (slot_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["cid"] if row else None

    async def save_tracked_pin(self, pin: TrackedPin) -> None:
        now = _now()
        await self.db.execute(
            "INSERT OR REPLACE INTO tracked_pins"
            " (cid, pinner_address, pinner_node_id, pinner_multiaddr,"
            "  slot_id, claimed_at, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pin.cid, pin.pinner_address, pin.pinner_node_id,
                pin.pinner_multiaddr, pin.slot_id, pin.claimed_at,
                pin.status, now, now,
            ),
        )
        await self.db.commit()

    async def get_tracked_pins(
        self, status: list[str] | None = None
    ) -> list[TrackedPin]:
        if status:
            placeholders = ",".join("?" for _ in status)
            sql = f"SELECT * FROM tracked_pins WHERE status IN ({placeholders}) ORDER BY last_checked_at"
            async with self.db.execute(sql, status) as cur:
                return [_row_to_tracked_pin(row) async for row in cur]
        else:
            async with self.db.execute(
                "SELECT * FROM tracked_pins ORDER BY last_checked_at"
            ) as cur:
                return [_row_to_tracked_pin(row) async for row in cur]

    async def update_tracked_pin(
        self,
        cid: str,
        pinner_address: str,
        status: str | None = None,
        consecutive_failures: int | None = None,
        last_verified_at: str | None = None,
        last_checked_at: str | None = None,
        flagged_at: str | None = None,
        flag_tx_hash: str | None = None,
    ) -> None:
        updates = ["updated_at=?"]
        params: list = [_now()]
        if status is not None:
            updates.append("status=?")
            params.append(status)
        if consecutive_failures is not None:
            updates.append("consecutive_failures=?")
            params.append(consecutive_failures)
            updates.append("total_checks=total_checks+1")
            if consecutive_failures > 0:
                updates.append("total_failures=total_failures+1")
        if last_verified_at is not None:
            updates.append("last_verified_at=?")
            params.append(last_verified_at)
        if last_checked_at is not None:
            updates.append("last_checked_at=?")
            params.append(last_checked_at)
        if flagged_at is not None:
            updates.append("flagged_at=?")
            params.append(flagged_at)
        if flag_tx_hash is not None:
            updates.append("flag_tx_hash=?")
            params.append(flag_tx_hash)

        params.extend([cid, pinner_address])
        sql = f"UPDATE tracked_pins SET {', '.join(updates)} WHERE cid=? AND pinner_address=?"
        await self.db.execute(sql, params)
        await self.db.commit()

    # ── Hunter: verification log ───────────────────────────

    async def record_verification(
        self, cid: str, pinner_address: str, result: VerificationResult,
    ) -> None:
        methods_json = json.dumps([
            {"method": m.method, "passed": m.passed, "detail": m.detail, "duration_ms": m.duration_ms}
            for m in result.methods_attempted
        ])
        await self.db.execute(
            "INSERT INTO verification_log"
            " (cid, pinner_address, passed, method_used, methods_attempted, duration_ms, checked_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                cid, pinner_address, 1 if result.passed else 0,
                result.method_used, methods_json, result.duration_ms, result.checked_at,
            ),
        )
        await self.db.commit()

    async def save_cycle_report(self, report: CycleReport) -> None:
        await self.db.execute(
            "INSERT INTO verification_cycles"
            " (started_at, completed_at, total_checked, passed, failed,"
            "  flagged, skipped, errors, duration_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report.started_at, report.completed_at, report.total_checked,
                report.passed, report.failed, report.flagged,
                report.skipped, report.errors, report.duration_ms,
            ),
        )
        await self.db.commit()

    async def get_cycle_history(self, limit: int = 10) -> list[CycleReport]:
        async with self.db.execute(
            "SELECT * FROM verification_cycles ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [
                CycleReport(
                    cycle_id=row["id"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    total_checked=row["total_checked"],
                    passed=row["passed"],
                    failed=row["failed"],
                    flagged=row["flagged"],
                    skipped=row["skipped"],
                    errors=row["errors"],
                    duration_ms=row["duration_ms"],
                )
                async for row in cur
            ]

    # ── Hunter: flags ──────────────────────────────────────

    async def save_flag(self, record: FlagRecord) -> None:
        await self.db.execute(
            "INSERT INTO flag_history"
            " (pinner_address, tx_hash, flag_count_after, bounty_earned, submitted_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                record.pinner_address, record.tx_hash,
                record.flag_count_after, record.bounty_earned,
                record.submitted_at or _now(),
            ),
        )
        await self.db.commit()

    async def get_flag_history(self) -> list[FlagRecord]:
        async with self.db.execute(
            "SELECT * FROM flag_history ORDER BY submitted_at DESC"
        ) as cur:
            return [
                FlagRecord(
                    pinner_address=row["pinner_address"],
                    tx_hash=row["tx_hash"] or "",
                    flag_count_after=row["flag_count_after"],
                    bounty_earned=row["bounty_earned"],
                    submitted_at=row["submitted_at"],
                )
                async for row in cur
            ]

    # ── Hunter: pinner cache ───────────────────────────────

    async def get_cached_pinner(self, address: str) -> PinnerInfo | None:
        async with self.db.execute(
            "SELECT * FROM pinner_cache WHERE address=?", (address,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return PinnerInfo(
                    address=row["address"],
                    node_id=row["node_id"],
                    multiaddr=row["multiaddr"],
                    active=bool(row["active"]),
                    cached_at=row["cached_at"],
                )
        return None

    async def cache_pinner(self, info: PinnerInfo) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO pinner_cache"
            " (address, node_id, multiaddr, active, cached_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (info.address, info.node_id, info.multiaddr, int(info.active), _now()),
        )
        await self.db.commit()


# ── Row converters ─────────────────────────────────────────


def _row_to_offer(row: aiosqlite.Row) -> OfferRecord:
    return OfferRecord(
        slot_id=row["slot_id"],
        cid=row["cid"],
        gateway=row["gateway"],
        offer_price=row["offer_price"],
        pin_qty=row["pin_qty"],
        pins_remaining=row["pins_remaining"],
        publisher=row["publisher"],
        ledger_sequence=row["ledger_sequence"],
        status=row["status"],
        reject_reason=row["reject_reason"],
        net_profit=row["net_profit"],
        estimated_expiry=row["estimated_expiry"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_tracked_pin(row: aiosqlite.Row) -> TrackedPin:
    return TrackedPin(
        cid=row["cid"],
        cid_hash="",  # not stored in tracked_pins table directly
        pinner_address=row["pinner_address"],
        pinner_node_id=row["pinner_node_id"],
        pinner_multiaddr=row["pinner_multiaddr"],
        slot_id=row["slot_id"],
        claimed_at=row["claimed_at"],
        last_verified_at=row["last_verified_at"],
        last_checked_at=row["last_checked_at"],
        consecutive_failures=row["consecutive_failures"],
        total_checks=row["total_checks"],
        total_failures=row["total_failures"],
        status=row["status"],
        flagged_at=row["flagged_at"],
        flag_tx_hash=row["flag_tx_hash"],
    )
