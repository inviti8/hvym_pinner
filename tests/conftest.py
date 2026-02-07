"""Shared fixtures for hvym_pinner tests."""

from __future__ import annotations

import pytest
from pytest_metadata.plugin import metadata_key

from hvym_pinner.api.data_api import DataAggregator
from hvym_pinner.api.mode import DaemonModeController
from hvym_pinner.daemon import PinnerDaemon
from hvym_pinner.models.config import DaemonConfig, DaemonMode, HunterConfig
from hvym_pinner.policy.filter import PolicyOfferFilter
from hvym_pinner.storage.sqlite import SQLiteStateStore

from tests.mocks import (
    MockExecutor,
    MockFlagSubmitter,
    MockPoller,
    MockQueries,
    MockSubmitter,
    MockVerifier,
)

TEST_SECRET = "SBWVJTD3F5ETMVWCNI7MM4HUAPUSCUXXMUEJZJTPRWRJGXW2BF4SVQTK"
TEST_PUBLIC = "GDNAG4KFFVF5HCSGRWZIXZNL2SR2KBGJSHW2A6FI6DZI62XF6IBLO4GD"

CONTRACT_ID = "CCEDYFIHUCJFITWEOT7BWUO2HBQQ72L244ZXQ4YNOC6FYRDN3MKDQFK7"
FACTORY_CONTRACT_ID = "CACBN6G2EPPLAQORDB3LXN3SULGVYBAETFZTNYTNDQ77B7JFRIBT66V2"

EXPLORER_BASE = "https://stellar.expert/explorer/testnet"


def stellar_expert_link(kind: str, id: str, label: str | None = None) -> str:
    """Build an HTML anchor to stellar.expert for the report."""
    url = f"{EXPLORER_BASE}/{kind}/{id}"
    text = label or f"{id[:8]}...{id[-4:]}"
    return f'<a href="{url}" target="_blank">{text}</a>'


def tx_link(tx_hash: str) -> str:
    """Build an explorer link for a transaction hash."""
    return stellar_expert_link("tx", tx_hash)


# ── Report metadata & explorer links ─────────────────────────────


def pytest_configure(config):
    """Add network info to the HTML report Environment table."""
    meta = config.stash.setdefault(metadata_key, {})
    meta["Network"] = "Stellar Testnet"
    meta["Pin Service Contract"] = CONTRACT_ID
    meta["Factory Contract"] = FACTORY_CONTRACT_ID
    meta["Pinner Account"] = TEST_PUBLIC


def pytest_html_results_summary(prefix, summary, postfix):
    """Inject clickable Stellar explorer links into the report summary."""
    prefix.append(
        '<div style="margin:8px 0;padding:10px;background:#f8f9fa;border:1px solid #dee2e6;'
        'border-radius:4px;font-family:monospace;font-size:13px;">'
        "<strong>Stellar Testnet Explorer Links</strong><br/>"
        f'Pin Service: {stellar_expert_link("contract", CONTRACT_ID, CONTRACT_ID)}<br/>'
        f'Factory: {stellar_expert_link("contract", FACTORY_CONTRACT_ID, FACTORY_CONTRACT_ID)}<br/>'
        f'Pinner Account: {stellar_expert_link("account", TEST_PUBLIC, TEST_PUBLIC)}'
        "</div>"
    )


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
    return MockQueries(wallet_balance=10_000_000)


@pytest.fixture
def mock_verifier():
    return MockVerifier(passed=True)


@pytest.fixture
def mock_flag_submitter():
    return MockFlagSubmitter(succeed=True)


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
        store=store,
        queries=mock_queries,
        mode_ctrl=d.mode_ctrl,
        our_address=TEST_PUBLIC,
        start_time=0.0,
    )
    return d
