"""Mode controller - manages auto/approve operating mode."""

from __future__ import annotations

import logging

from hvym_pinner.models.config import DaemonMode
from hvym_pinner.storage.sqlite import SQLiteStateStore

log = logging.getLogger(__name__)


class DaemonModeController:
    """Controls the daemon operating mode with state persistence."""

    def __init__(self, store: SQLiteStateStore, initial_mode: DaemonMode) -> None:
        self._store = store
        self._mode = initial_mode

    def get_mode(self) -> DaemonMode:
        return self._mode

    def set_mode(self, mode: DaemonMode) -> None:
        old = self._mode
        self._mode = mode
        if old != mode:
            log.info("Mode changed: %s -> %s", old.value, mode.value)
