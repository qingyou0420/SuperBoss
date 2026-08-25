"""Blocking boto3 boundary behavior for the asynchronous object-storage adapter."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from superboss.infrastructure.s3 import Boto3ObjectStorage


def test_s3_adapter_imports_without_type_stub_packages_or_network() -> None:
    """Production imports must not depend on development-only boto3 type stubs."""
    source_root = Path(__file__).resolve().parents[3] / "src"
    script = """
import importlib.abc
import socket
import sys

class BlockTypeStubs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mypy_boto3_s3" or fullname.startswith("mypy_boto3_s3."):
            raise ModuleNotFoundError(f"blocked production-only import: {fullname}", name=fullname)
        return None

def deny_network(*_args, **_kwargs):
    raise AssertionError("network access attempted during S3 adapter import")

sys.meta_path.insert(0, BlockTypeStubs())
sys.path.insert(0, sys.argv[1])
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network

from superboss.infrastructure.s3 import Boto3ObjectStorage

storage = Boto3ObjectStorage(bucket="files-bucket", client=object())
assert storage is not None
assert not any(
    name == "mypy_boto3_s3" or name.startswith("mypy_boto3_s3.")
    for name in sys.modules
)
"""

    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source_root)],
        cwd=source_root.parent,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_boto3_default_client_is_lazy_and_uses_bounded_transport_config(
    monkeypatch,
) -> None:
    """An ambiguous completion cannot leave a boto call retrying past service recovery bounds."""
    from superboss.infrastructure import s3

    captured: dict[str, object] = {}
    event_loop_thread = threading.get_ident()
    factory_threads: list[int] = []

    class Client:
        def delete_object(self, **_kwargs: object) -> None:
            return None

    def client(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        factory_threads.append(threading.get_ident())
        return Client()

    monkeypatch.setattr(s3.boto3, "client", client)
    storage = Boto3ObjectStorage(bucket="files-bucket", endpoint_url="http://s3")
    assert captured == {}

    await storage.delete_object("objects/one")

    config = captured["config"]
    assert factory_threads != [event_loop_thread]
    assert config.connect_timeout == 5
    assert config.read_timeout == 10
    assert config.retries["max_attempts"] == 2
