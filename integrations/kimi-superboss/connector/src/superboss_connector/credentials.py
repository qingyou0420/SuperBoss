"""OS-keyring storage for rotating device refresh credentials."""

from __future__ import annotations

import keyring

from .errors import CREDENTIAL_ERROR, ConnectorError

USERNAME = "device_refresh"


class CredentialStore:
    def __init__(self, origin: str) -> None:
        self.service = f"SuperBoss/KimiConnector/{origin}"

    def load_refresh(self) -> str:
        try:
            value = keyring.get_password(self.service, USERNAME)
        except Exception as error:
            raise ConnectorError(3, CREDENTIAL_ERROR) from error
        if not value:
            raise ConnectorError(3, CREDENTIAL_ERROR)
        return value

    def save_refresh(self, value: str) -> None:
        try:
            keyring.set_password(self.service, USERNAME, value)
        except Exception as error:
            raise ConnectorError(3, CREDENTIAL_ERROR) from error
