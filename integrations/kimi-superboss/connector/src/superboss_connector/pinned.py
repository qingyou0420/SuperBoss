"""HTTPS transport whose TCP destination is a prevalidated literal address."""

from __future__ import annotations

import ipaddress
import select
import socket
import ssl
import time
from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any, Self

import httpcore
import httpx

SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


def _transport_error(error: Exception) -> httpx.RequestError:
    if isinstance(error, httpcore.TimeoutException):
        return httpx.ConnectTimeout("Pinned upload transport timed out.")
    return httpx.ConnectError("Pinned upload transport failed.")


class _PinnedSocketStream(httpcore.NetworkStream):
    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            self._socket.settimeout(timeout)
            return self._socket.recv(max_bytes)
        except TimeoutError as error:
            raise httpcore.ReadTimeout from error
        except OSError as error:
            raise httpcore.ReadError from error

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        try:
            self._socket.settimeout(timeout)
            self._socket.sendall(buffer)
        except TimeoutError as error:
            raise httpcore.WriteTimeout from error
        except OSError as error:
            raise httpcore.WriteError from error

    def close(self) -> None:
        self._socket.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        try:
            self._socket.settimeout(timeout)
            wrapped = ssl_context.wrap_socket(
                self._socket,
                server_hostname=server_hostname,
            )
        except TimeoutError as error:
            self.close()
            raise httpcore.ConnectTimeout from error
        except OSError as error:
            self.close()
            raise httpcore.ConnectError from error
        return _PinnedSocketStream(wrapped)

    def get_extra_info(self, info: str) -> Any:
        if info == "ssl_object" and isinstance(self._socket, ssl.SSLSocket):
            return self._socket._sslobj  # type: ignore[attr-defined]
        if info == "client_addr":
            return self._socket.getsockname()
        if info == "server_addr":
            return self._socket.getpeername()
        if info == "socket":
            return self._socket
        if info == "is_readable":
            return bool(select.select([self._socket], [], [], 0)[0])
        return None


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        hostname: str,
        addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ) -> None:
        self._hostname = hostname.casefold()
        self._addresses = addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        if host.casefold() != self._hostname:
            raise httpcore.ConnectError
        deadline = None if timeout is None else time.monotonic() + timeout
        last_error: OSError | None = None
        for address in self._addresses:
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                sock.settimeout(remaining)
                if local_address is not None:
                    local = ipaddress.ip_address(local_address)
                    if local.version != address.version:
                        raise OSError("local address family mismatch")
                    bind_target: tuple[Any, ...] = (
                        (str(local), 0, 0, 0) if local.version == 6 else (str(local), 0)
                    )
                    sock.bind(bind_target)
                for option in socket_options or ():
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                target: tuple[Any, ...] = (
                    (str(address), port, 0, 0) if address.version == 6 else (str(address), port)
                )
                sock.connect(target)
                peer = ipaddress.ip_address(sock.getpeername()[0])
                if peer != address:
                    raise OSError("connected peer differs from pinned address")
                return _PinnedSocketStream(sock)
            except OSError as error:
                last_error = error
                sock.close()
        if isinstance(last_error, TimeoutError):
            raise httpcore.ConnectTimeout from last_error
        raise httpcore.ConnectError from last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        del path, timeout, socket_options
        raise httpcore.ConnectError

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class _CoreResponseStream(httpx.SyncByteStream):
    def __init__(self, stream: Iterable[bytes]) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._stream
        except (
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ) as error:
            raise _transport_error(error) from error

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


class PinnedHTTPTransport(httpx.BaseTransport):
    """Use approved literal TCP peers while retaining URL hostname for TLS SNI and Host."""

    def __init__(
        self,
        hostname: str,
        addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedNetworkBackend(hostname, addresses),
        )

    def __enter__(self) -> Self:
        self._pool.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        self._pool.__exit__(exc_type, exc_value, traceback)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.SyncByteStream):
            raise httpx.ConnectError("Pinned upload request is not synchronous.")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool.handle_request(core_request)
        except (
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ) as error:
            raise _transport_error(error) from error
        if not isinstance(response.stream, Iterable):
            raise httpx.ConnectError("Pinned upload response is not synchronous.")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()
