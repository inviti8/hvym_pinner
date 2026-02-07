"""CLI entry point for the hvym_pinner daemon."""

from __future__ import annotations

import asyncio
import logging
import sys

import click

from hvym_pinner.config import load_config
from hvym_pinner.daemon import run_daemon


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


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Start the pinning daemon."""
    cfg = load_config(ctx.obj["config_path"])

    if not cfg.keypair_secret:
        click.echo("Error: No keypair secret configured.", err=True)
        click.echo("Set HVYM_PINNER_SECRET env var or keypair_secret in config.", err=True)
        sys.exit(1)

    if not cfg.contract_id:
        click.echo("Error: No contract ID configured.", err=True)
        click.echo("Set HVYM_PINNER_CONTRACT_ID env var or check deployments.json.", err=True)
        sys.exit(1)

    click.echo(f"Starting hvym_pinner daemon (mode: {cfg.mode.value})")
    asyncio.run(run_daemon(cfg))


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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
