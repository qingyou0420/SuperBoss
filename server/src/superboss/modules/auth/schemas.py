"""Auth public data transfer objects."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SessionPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
