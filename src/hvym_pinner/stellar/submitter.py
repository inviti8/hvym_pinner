"""Soroban claim submitter - submits collect_pin() via contract bindings."""

from __future__ import annotations

import logging

from stellar_sdk import Keypair
from stellar_sdk.contract.exceptions import (
    SimulationFailedError,
    TransactionFailedError,
)

from hvym_pinner.bindings.hvym_pin_service import ClientAsync, Error
from hvym_pinner.models.records import ClaimResult

log = logging.getLogger(__name__)

# Known contract error substrings for specific handling
_ERROR_ALREADY_CLAIMED = str(Error.AlreadyClaimed.value)
_ERROR_SLOT_EXPIRED = str(Error.SlotExpired.value)
_ERROR_SLOT_NOT_ACTIVE = str(Error.SlotNotActive.value)
_ERROR_NOT_PINNER = str(Error.NotPinner.value)
_ERROR_PINNER_INACTIVE = str(Error.PinnerInactive.value)


def _classify_error(exc: Exception) -> str:
    """Try to extract a meaningful error classification from a contract error."""
    msg = str(exc)
    if _ERROR_ALREADY_CLAIMED in msg:
        return "already_claimed"
    if _ERROR_SLOT_EXPIRED in msg:
        return "slot_expired"
    if _ERROR_SLOT_NOT_ACTIVE in msg:
        return "slot_not_active"
    if _ERROR_NOT_PINNER in msg:
        return "not_pinner"
    if _ERROR_PINNER_INACTIVE in msg:
        return "pinner_inactive"
    return "unknown"


class SorobanClaimSubmitter:
    """Submits collect_pin() transactions using the contract bindings.

    Uses ClientAsync from the auto-generated bindings. The bindings handle
    XDR encoding, transaction building, simulation, and submission.
    """

    def __init__(
        self,
        contract_id: str,
        rpc_url: str,
        network_passphrase: str,
        keypair: Keypair,
    ) -> None:
        self._keypair = keypair
        self._public_key = keypair.public_key
        self._client = ClientAsync(
            contract_id=contract_id,
            rpc_url=rpc_url,
            network_passphrase=network_passphrase,
        )

    async def submit_claim(self, slot_id: int) -> ClaimResult:
        """Build, sign, and submit a collect_pin() transaction.

        Returns a ClaimResult with success status, amount earned, and tx hash.
        The binding's collect_pin() returns pins_remaining as an int.
        """
        log.info("Submitting collect_pin for slot %d", slot_id)

        try:
            tx = await self._client.collect_pin(
                caller=self._public_key,
                slot_id=slot_id,
                source=self._public_key,
                signer=self._keypair,
            )

            await tx.simulate()
            pins_remaining = await tx.sign_and_submit()

            tx_hash = ""
            if tx.send_transaction_response:
                tx_hash = tx.send_transaction_response.hash

            log.info(
                "collect_pin succeeded for slot %d (pins_remaining=%s, tx=%s)",
                slot_id,
                pins_remaining,
                tx_hash[:16] if tx_hash else "?",
            )

            # The amount earned equals the slot's offer_price, but we don't
            # have it here. The caller should look it up from the offer record.
            # We return None for amount_earned; the daemon loop fills it in.
            return ClaimResult(
                success=True,
                slot_id=slot_id,
                tx_hash=tx_hash,
            )

        except SimulationFailedError as exc:
            error_type = _classify_error(exc)
            log.warning(
                "collect_pin simulation failed for slot %d: %s (%s)",
                slot_id,
                error_type,
                exc,
            )
            return ClaimResult(
                success=False,
                slot_id=slot_id,
                error=f"simulation_failed:{error_type}",
            )

        except TransactionFailedError as exc:
            error_type = _classify_error(exc)
            tx_hash = ""
            if exc.assembled_transaction.send_transaction_response:
                tx_hash = exc.assembled_transaction.send_transaction_response.hash
            log.error(
                "collect_pin tx failed for slot %d: %s (tx=%s)",
                slot_id,
                error_type,
                tx_hash[:16] if tx_hash else "?",
            )
            return ClaimResult(
                success=False,
                slot_id=slot_id,
                tx_hash=tx_hash or None,
                error=f"tx_failed:{error_type}",
            )

        except Exception as exc:
            log.error("collect_pin unexpected error for slot %d: %s", slot_id, exc)
            return ClaimResult(
                success=False,
                slot_id=slot_id,
                error=str(exc),
            )
