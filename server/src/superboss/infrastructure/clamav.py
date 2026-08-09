"""Scanner contracts shared by file lifecycle work and the clamd adapter."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ScanStatus(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"


@dataclass(frozen=True)
class ScanVerdict:
    status: ScanStatus
    signature: str | None = None


class Scanner(Protocol):
    async def scan(self, chunks: AsyncIterator[bytes]) -> ScanVerdict: ...
