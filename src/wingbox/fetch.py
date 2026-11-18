"""The download of `wingbox add <language>`: the one place in Wingbox that opens a network connection.

Only wingbox/add.py imports this module, and only the command `wingbox add` runs that; a build, a refresh,
a hook, a query and the MCP server never do (tests/test_local_only.py proves it from the import statements).

`download` takes an https URL on files.pythonhosted.org, the file host of the Python Package Index, and
nothing else: no other scheme or host, no port, no user, no redirect. The caller names the size the file
must have and checks its sha256 afterwards. Proxies set in the environment (HTTPS_PROXY) are used, as pip
does; certificates are checked by the system's default rules.
"""
from __future__ import annotations

import os
import urllib.request
from typing import IO, TYPE_CHECKING, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from http.client import HTTPMessage

HOST = "files.pythonhosted.org"
TIMEOUT_SECONDS = 60.0
_CHUNK = 1 << 16


class FetchRefused(OSError):
    """The URL or the answer is not what `wingbox add` may download."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: "HTTPMessage", newurl: str
    ) -> Optional[urllib.request.Request]:
        raise FetchRefused(f"the download was redirected to {newurl}; only https://{HOST}/ is allowed")


def allowed(url: str) -> bool:
    """An https URL on the package index's file host, without user, port, query or fragment."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https" and parts.netloc == HOST and parts.path.startswith("/packages/")
        and not parts.query and not parts.fragment
    )


def _open(url: str, timeout: float) -> IO[bytes]:
    return urllib.request.build_opener(NoRedirect()).open(url, timeout=timeout)


def download(url: str, target: str, size: int, timeout: Optional[float] = None) -> None:
    """Write the file at url to target: exactly size bytes, or an OSError and no file."""
    if not allowed(url):
        raise FetchRefused(f"not a file of the Python Package Index (https://{HOST}/packages/...): {url}")
    received = 0
    try:
        with _open(url, TIMEOUT_SECONDS if timeout is None else timeout) as response, open(target, "wb") as output:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > size:
                    raise FetchRefused(f"{url} is longer than the pinned {size} bytes")
                output.write(chunk)
        if received != size:
            raise FetchRefused(f"{url} gave {received} bytes, the pinned file has {size}")
    except BaseException as error:
        try:
            os.unlink(target)
        except OSError:
            pass
        if isinstance(error, Exception) and not isinstance(error, OSError):
            # A transfer that breaks off (http.client.IncompleteRead) is no OSError: say it as one.
            raise FetchRefused(f"the download of {url} broke off: {error!r}") from error
        raise
