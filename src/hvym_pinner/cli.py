"""CLI entry point for the hvym_pinner daemon."""

from __future__ import annotations

import asyncio
import logging
import sys

import click
from stellar_sdk import Keypair

from hvym_pinner.config import load_config
from hvym_pinner.daemon import run_daemon, NETWORK_PASSPHRASES
from hvym_pinner.stellar.queries import ContractQueries, STROOPS_PER_XLM
from hvym_pinner.storage.sqlite import SQLiteStateStore
from hvym_pinner.bindings.hvym_pin_service import ClientAsync


def _xlm(stroops: int) -> str:
    return f"{stroops / STROOPS_PER_XLM:.7f} XLM"


def _require_secret(cfg):
    """Exit with error if no keypair secret is configured."""
    if not cfg.keypair_secret:
        click.echo("Error: No keypair secret configured.", err=True)
        click.echo("Set HVYM_PINNER_SECRET env var or keypair_secret in config.", err=True)
        sys.exit(1)


def _require_contract(cfg):
    """Exit with error if no contract ID is configured."""
    if not cfg.contract_id:
        click.echo("Error: No contract ID configured.", err=True)
        click.echo("Set HVYM_PINNER_CONTRACT_ID or check deployments.json.", err=True)
        sys.exit(1)


def _passphrase(cfg) -> str:
    return cfg.network_passphrase or NETWORK_PASSPHRASES.get(cfg.network, "")


@click.group()
@click.option("-c", "--config", "config_path", default=None, help="Path to config TOML file")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """hvym_pinner - Autonomous IPFS pinning daemon for Pintheon."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["verbose"] = verbose

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ── Daemon ─────────────────────────────────────────────


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Start the pinning daemon."""
    cfg = load_config(ctx.obj["config_path"])
    _require_secret(cfg)
    _require_contract(cfg)

    click.echo(f"Starting hvym_pinner daemon (mode: {cfg.mode.value})")
    asyncio.run(run_daemon(cfg))


# ── Info ───────────────────────────────────────────────


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon configuration."""
    cfg = load_config(ctx.obj["config_path"])
    click.echo(f"Mode:       {cfg.mode.value}")
    click.echo(f"Network:    {cfg.network}")
    click.echo(f"RPC URL:    {cfg.rpc_url}")
    click.echo(f"Contract:   {cfg.contract_id or '(not set)'}")
    click.echo(f"Factory:    {cfg.factory_contract_id or '(not set)'}")
    click.echo(f"Kubo RPC:   {cfg.kubo_rpc_url}")
    click.echo(f"Min price:  {cfg.min_price} stroops")
    click.echo(f"DB path:    {cfg.db_path}")
    click.echo(f"Secret:     {'***configured***' if cfg.keypair_secret else '(not set)'}")


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Query on-chain pinner status, wallet balance, and contract fees."""
    cfg = load_config(ctx.obj["config_path"])
    _require_secret(cfg)
    _require_contract(cfg)

    async def _info():
        keypair = Keypair.from_secret(cfg.keypair_secret)
        public_key = keypair.public_key
        queries = ContractQueries(cfg.contract_id, cfg.rpc_url, _passphrase(cfg))

        try:
            click.echo(f"Address:    {public_key}")

            # Wallet balance
            balance = await queries.get_wallet_balance(public_key)
            click.echo(f"Balance:    {balance} stroops ({_xlm(balance)})")

            # Contract fees
            join_fee = await queries.get_join_fee()
            stake = await queries.get_pinner_stake()
            if join_fee is not None:
                click.echo(f"Join fee:   {join_fee} stroops ({_xlm(join_fee)})")
            if stake is not None:
                click.echo(f"Stake:      {stake} stroops ({_xlm(stake)})")
            if join_fee is not None and stake is not None:
                total = join_fee + stake
                click.echo(f"Total cost: {total} stroops ({_xlm(total)})")

            # Pinner status
            pinner = await queries.get_pinner(public_key)
            click.echo("")
            if pinner is None:
                click.echo("Pinner:     NOT REGISTERED")
                click.echo("  Run 'hvym-pinner register' to join as a pinner.")
            else:
                click.echo(f"Pinner:     REGISTERED")
                click.echo(f"  Active:         {pinner.active}")
                click.echo(f"  Node ID:        {pinner.node_id}")
                click.echo(f"  Multiaddr:      {pinner.multiaddr}")
                click.echo(f"  Min price:      {pinner.min_price} stroops")
                click.echo(f"  Pins completed: {pinner.pins_completed}")
                click.echo(f"  Flags:          {pinner.flags}")
                click.echo(f"  Staked:         {pinner.staked} stroops ({_xlm(pinner.staked)})")
        finally:
            await queries.close()

    asyncio.run(_info())


# ── Registration ───────────────────────────────────────


@cli.command()
@click.option("--node-id", required=True, help="IPFS peer ID (e.g., 12D3KooW...)")
@click.option("--multiaddr", required=True, help="IPFS multiaddress (e.g., /ip4/1.2.3.4/tcp/4001)")
@click.option("--min-price", type=int, default=100, help="Minimum offer price to accept (stroops)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def register(ctx: click.Context, node_id: str, multiaddr: str, min_price: int, yes: bool) -> None:
    """Register as a pinner on the pin service contract.

    This transfers join_fee + pinner_stake from your wallet to the contract.
    The stake is returned when you leave (if not deactivated by flags).
    """
    cfg = load_config(ctx.obj["config_path"])
    _require_secret(cfg)
    _require_contract(cfg)

    async def _register():
        keypair = Keypair.from_secret(cfg.keypair_secret)
        public_key = keypair.public_key
        passphrase = _passphrase(cfg)

        queries = ContractQueries(cfg.contract_id, cfg.rpc_url, passphrase)
        client = ClientAsync(cfg.contract_id, cfg.rpc_url, passphrase)

        # Check if already registered
        existing = await queries.get_pinner(public_key)
        if existing is not None:
            click.echo(f"Already registered as pinner (active={existing.active})")
            click.echo("Use 'hvym-pinner update-pinner' to change settings.")
            return

        # Show costs
        join_fee = await queries.get_join_fee() or 0
        stake = await queries.get_pinner_stake() or 0
        balance = await queries.get_wallet_balance(public_key)
        total_cost = join_fee + stake

        click.echo(f"Registering as pinner on {cfg.network}")
        click.echo(f"  Address:    {public_key}")
        click.echo(f"  Node ID:    {node_id}")
        click.echo(f"  Multiaddr:  {multiaddr}")
        click.echo(f"  Min price:  {min_price} stroops")
        click.echo(f"  Join fee:   {join_fee} stroops ({_xlm(join_fee)})")
        click.echo(f"  Stake:      {stake} stroops ({_xlm(stake)})")
        click.echo(f"  Total cost: {total_cost} stroops ({_xlm(total_cost)})")
        click.echo(f"  Balance:    {balance} stroops ({_xlm(balance)})")

        if balance < total_cost:
            click.echo(f"\nInsufficient balance! Need {total_cost - balance} more stroops.", err=True)
            sys.exit(1)

        if not yes:
            click.confirm("\nProceed with registration?", abort=True)

        click.echo("\nSubmitting join_as_pinner transaction...")
        try:
            tx = await client.join_as_pinner(
                caller=public_key,
                node_id=node_id.encode("utf-8"),
                multiaddr=multiaddr.encode("utf-8"),
                min_price=min_price,
                source=public_key,
                signer=keypair,
            )
            await tx.simulate()
            result = await tx.sign_and_submit()

            tx_hash = ""
            if tx.send_transaction_response:
                tx_hash = tx.send_transaction_response.hash

            click.echo(f"Registration successful!")
            click.echo(f"  Tx hash:  {tx_hash}")
            click.echo(f"  Active:   {result.active}")
            click.echo(f"  Staked:   {result.staked} stroops")
            click.echo(f"\nYou can now start the daemon with 'hvym-pinner run'")

        except Exception as exc:
            click.echo(f"\nRegistration failed: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_register())


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def unregister(ctx: click.Context, yes: bool) -> None:
    """Leave the pinner registry and reclaim your stake.

    Only works if your pinner is still active (not deactivated by flags).
    """
    cfg = load_config(ctx.obj["config_path"])
    _require_secret(cfg)
    _require_contract(cfg)

    async def _unregister():
        keypair = Keypair.from_secret(cfg.keypair_secret)
        public_key = keypair.public_key
        passphrase = _passphrase(cfg)

        queries = ContractQueries(cfg.contract_id, cfg.rpc_url, passphrase)
        client = ClientAsync(cfg.contract_id, cfg.rpc_url, passphrase)

        # Check if registered
        pinner = await queries.get_pinner(public_key)
        if pinner is None:
            click.echo("Not registered as a pinner.")
            return

        click.echo(f"Leaving pinner registry")
        click.echo(f"  Address:  {public_key}")
        click.echo(f"  Active:   {pinner.active}")
        click.echo(f"  Staked:   {pinner.staked} stroops ({_xlm(pinner.staked)})")

        if not pinner.active:
            click.echo("\nWarning: Pinner is inactive (deactivated by flags).", err=True)
            click.echo("Stake may have been forfeited.", err=True)

        if not yes:
            click.confirm("\nProceed with unregistration?", abort=True)

        click.echo("\nSubmitting leave_as_pinner transaction...")
        try:
            tx = await client.leave_as_pinner(
                caller=public_key,
                source=public_key,
                signer=keypair,
            )
            await tx.simulate()
            refund = await tx.sign_and_submit()

            tx_hash = ""
            if tx.send_transaction_response:
                tx_hash = tx.send_transaction_response.hash

            click.echo(f"Unregistration successful!")
            click.echo(f"  Tx hash:  {tx_hash}")
            click.echo(f"  Refunded: {refund} stroops ({_xlm(refund)})")

        except Exception as exc:
            click.echo(f"\nUnregistration failed: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_unregister())


@cli.command("update-pinner")
@click.option("--node-id", default=None, help="New IPFS peer ID")
@click.option("--multiaddr", default=None, help="New IPFS multiaddress")
@click.option("--min-price", type=int, default=None, help="New minimum offer price (stroops)")
@click.option("--active/--inactive", default=None, help="Set pinner active status")
@click.pass_context
def update_pinner(
    ctx: click.Context,
    node_id: str | None,
    multiaddr: str | None,
    min_price: int | None,
    active: bool | None,
) -> None:
    """Update pinner settings on-chain."""
    cfg = load_config(ctx.obj["config_path"])
    _require_secret(cfg)
    _require_contract(cfg)

    if all(v is None for v in [node_id, multiaddr, min_price, active]):
        click.echo("Nothing to update. Specify at least one option.", err=True)
        sys.exit(1)

    async def _update():
        keypair = Keypair.from_secret(cfg.keypair_secret)
        public_key = keypair.public_key
        passphrase = _passphrase(cfg)
        client = ClientAsync(cfg.contract_id, cfg.rpc_url, passphrase)

        click.echo("Updating pinner settings...")
        changes = []
        if node_id is not None:
            changes.append(f"node_id={node_id}")
        if multiaddr is not None:
            changes.append(f"multiaddr={multiaddr}")
        if min_price is not None:
            changes.append(f"min_price={min_price}")
        if active is not None:
            changes.append(f"active={active}")
        click.echo(f"  Changes: {', '.join(changes)}")

        try:
            tx = await client.update_pinner(
                caller=public_key,
                node_id=node_id.encode("utf-8") if node_id else None,
                multiaddr=multiaddr.encode("utf-8") if multiaddr else None,
                min_price=min_price,
                active=active,
                source=public_key,
                signer=keypair,
            )
            await tx.simulate()
            result = await tx.sign_and_submit()

            tx_hash = ""
            if tx.send_transaction_response:
                tx_hash = tx.send_transaction_response.hash

            click.echo(f"Update successful!")
            click.echo(f"  Tx hash:     {tx_hash}")
            click.echo(f"  Node ID:     {result.node_id.decode('utf-8') if isinstance(result.node_id, bytes) else result.node_id}")
            click.echo(f"  Multiaddr:   {result.multiaddr.decode('utf-8') if isinstance(result.multiaddr, bytes) else result.multiaddr}")
            click.echo(f"  Min price:   {result.min_price} stroops")
            click.echo(f"  Active:      {result.active}")

        except Exception as exc:
            click.echo(f"\nUpdate failed: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_update())


# ── CID Hunter ─────────────────────────────────────────


@cli.group()
def hunter():
    """CID Hunter - verify other pinners are serving content."""
    pass


@hunter.command("status")
@click.pass_context
def hunter_status(ctx: click.Context) -> None:
    """Show CID Hunter summary and tracked pin counts."""
    cfg = load_config(ctx.obj["config_path"])

    if not cfg.hunter.enabled:
        click.echo("CID Hunter is disabled.")
        click.echo("Enable with [hunter] enabled = true in config.")
        return

    async def _status():
        store = SQLiteStateStore(cfg.db_path)
        await store.initialize()
        try:
            pins = await store.get_tracked_pins()
            flags = await store.get_flag_history()
            cycles = await store.get_cycle_history(1)

            verified = len([p for p in pins if p.status == "verified"])
            suspect = len([p for p in pins if p.status == "suspect"])
            flagged = len([p for p in pins if p.status == "flag_submitted"])
            tracking = len([p for p in pins if p.status == "tracking"])
            total_bounties = sum(f.bounty_earned or 0 for f in flags)

            click.echo("CID Hunter Status")
            click.echo(f"  Enabled:            {cfg.hunter.enabled}")
            click.echo(f"  Cycle interval:     {cfg.hunter.cycle_interval}s")
            click.echo(f"  Failure threshold:  {cfg.hunter.failure_threshold}")
            click.echo(f"  Verification:       {', '.join(cfg.hunter.verification_methods)}")
            click.echo("")
            click.echo("Tracked Pins")
            click.echo(f"  Total:              {len(pins)}")
            click.echo(f"  Tracking:           {tracking}")
            click.echo(f"  Verified:           {verified}")
            click.echo(f"  Suspect:            {suspect}")
            click.echo(f"  Flagged:            {flagged}")
            click.echo("")
            click.echo("Flags")
            click.echo(f"  Total submitted:    {len(flags)}")
            click.echo(f"  Bounties earned:    {total_bounties} stroops ({_xlm(total_bounties)})")

            if cycles:
                c = cycles[0]
                click.echo("")
                click.echo("Last Cycle")
                click.echo(f"  At:                 {c.completed_at}")
                click.echo(f"  Checked:            {c.total_checked}")
                click.echo(f"  Passed:             {c.passed}")
                click.echo(f"  Failed:             {c.failed}")
                click.echo(f"  Duration:           {c.duration_ms}ms")
        finally:
            await store.close()

    asyncio.run(_status())


@hunter.command("tracked")
@click.option("--status", "filter_status", default=None, help="Filter by status (tracking, verified, suspect, flag_submitted)")
@click.pass_context
def hunter_tracked(ctx: click.Context, filter_status: str | None) -> None:
    """List tracked (CID, pinner) pairs."""
    cfg = load_config(ctx.obj["config_path"])

    async def _tracked():
        store = SQLiteStateStore(cfg.db_path)
        await store.initialize()
        try:
            if filter_status:
                pins = await store.get_tracked_pins([filter_status])
            else:
                pins = await store.get_tracked_pins()

            if not pins:
                click.echo("No tracked pins.")
                return

            for p in pins:
                click.echo(f"  [{p.status:15s}] CID={p.cid[:24]}... pinner={p.pinner_address[:12]}... "
                           f"checks={p.total_checks} failures={p.consecutive_failures}")
        finally:
            await store.close()

    asyncio.run(_tracked())


@hunter.command("suspects")
@click.pass_context
def hunter_suspects(ctx: click.Context) -> None:
    """List pinners that are suspected of not serving content."""
    cfg = load_config(ctx.obj["config_path"])

    async def _suspects():
        store = SQLiteStateStore(cfg.db_path)
        await store.initialize()
        try:
            pins = await store.get_tracked_pins(["suspect"])
            if not pins:
                click.echo("No suspects.")
                return

            for p in pins:
                click.echo(f"  CID={p.cid[:24]}... pinner={p.pinner_address[:12]}... "
                           f"failures={p.consecutive_failures}/{cfg.hunter.failure_threshold}")
        finally:
            await store.close()

    asyncio.run(_suspects())


@hunter.command("flags")
@click.pass_context
def hunter_flags(ctx: click.Context) -> None:
    """Show flag submission history."""
    cfg = load_config(ctx.obj["config_path"])

    async def _flags():
        store = SQLiteStateStore(cfg.db_path)
        await store.initialize()
        try:
            flags = await store.get_flag_history()
            if not flags:
                click.echo("No flags submitted.")
                return

            for f in flags:
                bounty = f"bounty={f.bounty_earned or 0}" if f.bounty_earned else "no bounty"
                click.echo(f"  pinner={f.pinner_address[:12]}... flags_after={f.flag_count_after} "
                           f"{bounty} tx={f.tx_hash[:16]}... at={f.submitted_at}")
        finally:
            await store.close()

    asyncio.run(_flags())


@hunter.command("cycles")
@click.option("-n", "--limit", type=int, default=5, help="Number of recent cycles to show")
@click.pass_context
def hunter_cycles(ctx: click.Context, limit: int) -> None:
    """Show recent verification cycle reports."""
    cfg = load_config(ctx.obj["config_path"])

    async def _cycles():
        store = SQLiteStateStore(cfg.db_path)
        await store.initialize()
        try:
            cycles = await store.get_cycle_history(limit)
            if not cycles:
                click.echo("No verification cycles recorded.")
                return

            for c in cycles:
                click.echo(
                    f"  #{c.cycle_id} at={c.completed_at} "
                    f"checked={c.total_checked} passed={c.passed} failed={c.failed} "
                    f"flagged={c.flagged} errors={c.errors} duration={c.duration_ms}ms"
                )
        finally:
            await store.close()

    asyncio.run(_cycles())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
