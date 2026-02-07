"""Offer filter - evaluates PIN events against local policy and wallet health."""

from __future__ import annotations

import logging

from hvym_pinner.models.events import PinEvent
from hvym_pinner.models.records import FilterResult
from hvym_pinner.stellar.queries import ContractQueries

log = logging.getLogger(__name__)

# Estimated transaction fee for a collect_pin() call (in stroops)
ESTIMATED_TX_FEE = 100_000  # 0.01 XLM - conservative estimate


class PolicyOfferFilter:
    """Evaluates PIN offers against configurable policy rules.

    Checks:
    1. Offer price >= minimum price
    2. Wallet has enough XLM to cover tx fees
    3. Slot is still active on-chain (not expired/filled)
    4. Net profit is positive after estimated fees
    """

    def __init__(
        self,
        queries: ContractQueries,
        our_address: str,
        min_price: int = 100,
        max_content_size: int = 1_073_741_824,
    ) -> None:
        self._queries = queries
        self._our_address = our_address
        self._min_price = min_price
        self._max_content_size = max_content_size

    @property
    def min_price(self) -> int:
        return self._min_price

    @min_price.setter
    def min_price(self, value: int) -> None:
        self._min_price = value

    async def evaluate(self, event: PinEvent) -> FilterResult:
        """Evaluate an offer against policy rules."""
        # 1. Price check
        if event.offer_price < self._min_price:
            return FilterResult(
                accepted=False,
                reason="price_too_low",
                slot_id=event.slot_id,
                offer_price=event.offer_price,
                wallet_balance=0,
                estimated_tx_fee=ESTIMATED_TX_FEE,
                net_profit=event.offer_price - ESTIMATED_TX_FEE,
            )

        # 2. Check wallet balance
        balance = await self._queries.get_wallet_balance(self._our_address)
        if balance < ESTIMATED_TX_FEE * 2:  # need buffer beyond single tx fee
            return FilterResult(
                accepted=False,
                reason="insufficient_xlm",
                slot_id=event.slot_id,
                offer_price=event.offer_price,
                wallet_balance=balance,
                estimated_tx_fee=ESTIMATED_TX_FEE,
                net_profit=event.offer_price - ESTIMATED_TX_FEE,
            )

        # 3. Verify slot is still active
        slot_active = await self.verify_slot_active(event.slot_id)
        if not slot_active:
            return FilterResult(
                accepted=False,
                reason="slot_not_active",
                slot_id=event.slot_id,
                offer_price=event.offer_price,
                wallet_balance=balance,
                estimated_tx_fee=ESTIMATED_TX_FEE,
                net_profit=event.offer_price - ESTIMATED_TX_FEE,
            )

        # 4. Net profit check
        net_profit = event.offer_price - ESTIMATED_TX_FEE
        if net_profit <= 0:
            return FilterResult(
                accepted=False,
                reason="unprofitable",
                slot_id=event.slot_id,
                offer_price=event.offer_price,
                wallet_balance=balance,
                estimated_tx_fee=ESTIMATED_TX_FEE,
                net_profit=net_profit,
            )

        # All checks passed
        log.info(
            "Offer accepted: slot %d, price %d stroops, net profit %d",
            event.slot_id,
            event.offer_price,
            net_profit,
        )
        return FilterResult(
            accepted=True,
            reason="accepted",
            slot_id=event.slot_id,
            offer_price=event.offer_price,
            wallet_balance=balance,
            estimated_tx_fee=ESTIMATED_TX_FEE,
            net_profit=net_profit,
        )

    async def verify_slot_active(self, slot_id: int) -> bool:
        """Query on-chain to confirm slot is still claimable."""
        # Check if expired
        expired = await self._queries.is_slot_expired(slot_id)
        if expired is True:
            return False

        # Check slot exists and has remaining pins
        slot = await self._queries.get_slot(slot_id)
        if slot is None:
            return False
        if slot.pins_remaining <= 0:
            return False

        return True
