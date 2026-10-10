"""Process exit codes used by DevKnife."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable process exit codes for shell scripts and CI."""

    SUCCESS = 0
    INVALID_USAGE = 2
    NETWORK_ERROR = 3
    CRYPTO_ERROR = 4
    CONFIG_ERROR = 5
    INTERNAL_ERROR = 10
