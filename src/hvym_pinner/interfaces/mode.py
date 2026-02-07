"""ModeController protocol - routes offers based on daemon operating mode."""

from __future__ import annotations

from typing import Protocol

from hvym_pinner.models.config import DaemonMode


class ModeController(Protocol):
    """Routes offers based on daemon operating mode."""

    def get_mode(self) -> DaemonMode:
        """Return current operating mode."""
        ...

    def set_mode(self, mode: DaemonMode) -> None:
        """Switch operating mode. Takes effect immediately."""
        ...
