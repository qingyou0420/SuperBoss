"""Stable, secret-safe connector failures."""

from __future__ import annotations


class ConnectorError(Exception):
    """An expected connector failure with a stable process exit code."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


INVALID_INPUT = "Input validation failed."
OUTBOX_BUSY = "Another operation is active; retry this command shortly."
OUTBOX_CONFLICT = "An unfinished operation exists; run retry first."
OUTBOX_INVALID = "Local recovery state is invalid."
CREDENTIAL_ERROR = "Device credentials are unavailable or invalid."
FILE_CHANGED = "A local attachment changed; submit a new result package."
SERVER_REJECTED = "The server rejected the operation."
TEMPORARY_FAILURE = "The operation could not finish; run retry."
