"""Flag submitter - submits flag_pinner() transactions to the contract."""

from __future__ import annotations

import logging

from stellar_sdk import Keypair
from stellar_sdk.contract.exceptions import SimulationFailedError, TransactionFailedError

from hvym_pinner.bindings.hvym_pin_service import ClientAsync, Error
from hvym_pinner.models.hunter import FlagResult
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


class SorobanFlagSubmitter:
    """Submits flag_pinner() transactions using the contract bindings."""

    def __init__(
        self,
        contract_id: str,
        rpc_url: str,
        network_passphrase: str,
        keypair: Keypair,
        store: SQLiteStateStore,
    ) -> None:
        self._keypair = keypair
        self._public_key = keypair.public_key
        self._client = ClientAsync(
            contract_id=contract_id,
            rpc_url=rpc_url,
            network_passphrase=network_passphrase,
        )
        self._store = store

    async def submit_flag(self, pinner_address: str) -> FlagResult:
        """Build, sign, and submit a flag_pinner() transaction.

        Returns the pinner's flag count after our flag.
        """
        log.info("Submitting flag_pinner for %s", pinner_address[:16])

        try:
            tx = await self._client.flag_pinner(
                caller=self._public_key,
                pinner_addr=pinner_address,
                source=self._public_key,
                signer=self._keypair,
            )
            await tx.simulate()
            flag_count = await tx.sign_and_submit()

            tx_hash = ""
            if tx.send_transaction_response:
                tx_hash = tx.send_transaction_response.hash

            log.info(
                "flag_pinner succeeded: %s now has %d flags (tx=%s)",
                pinner_address[:16],
                flag_count,
                tx_hash[:16] if tx_hash else "?",
            )

            return FlagResult(
                success=True,
                pinner_address=pinner_address,
                flag_count=flag_count,
                tx_hash=tx_hash,
            )

        except SimulationFailedError as exc:
            err_msg = str(exc)
            if str(Error.AlreadyFlagged.value) in err_msg:
                log.info("Already flagged %s", pinner_address[:16])
                return FlagResult(
                    success=False,
                    pinner_address=pinner_address,
                    error="already_flagged",
                )
            log.warning("flag_pinner simulation failed for %s: %s", pinner_address[:16], exc)
            return FlagResult(
                success=False,
                pinner_address=pinner_address,
                error=f"simulation_failed: {exc}",
            )

        except TransactionFailedError as exc:
            tx_hash = ""
            if exc.assembled_transaction.send_transaction_response:
                tx_hash = exc.assembled_transaction.send_transaction_response.hash
            log.error("flag_pinner tx failed for %s (tx=%s)", pinner_address[:16], tx_hash[:16] if tx_hash else "?")
            return FlagResult(
                success=False,
                pinner_address=pinner_address,
                tx_hash=tx_hash or None,
                error=f"tx_failed: {exc}",
            )

        except Exception as exc:
            log.error("flag_pinner unexpected error for %s: %s", pinner_address[:16], exc)
            return FlagResult(
                success=False,
                pinner_address=pinner_address,
                error=str(exc),
            )

    async def has_already_flagged(self, pinner_address: str) -> bool:
        """Check if we've already flagged this pinner."""
        flags = await self._store.get_flag_history()
        return any(f.pinner_address == pinner_address for f in flags)
