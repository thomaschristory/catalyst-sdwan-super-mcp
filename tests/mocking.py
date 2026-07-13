"""A minimal respx-shaped mock router built on ``httpx2.MockTransport``.

respx cannot mock httpx2 — it pins ``httpx>=0.25`` and patches httpx's transport
internals (upstream respx #316/#317 are still open). httpx2 instead expects a
``MockTransport`` to be *injected* into the client, so this module provides the
small slice of respx's API the suite actually uses:

    router.get(url).mock(return_value=httpx2.Response(200, json=...))
    router.post(url).respond(200, json=...)
    route.side_effect = [httpx2.Response(503), httpx2.Response(200)]
    route.called / route.call_count / route.calls.last.request

The router is created *before* the client under test (unlike respx, which patched
globally after the fact) and handed over as ``router.transport``. Routes may still
be registered later — the transport resolves them at request time.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import httpx2

__all__ = ["MockRouter", "Route"]


class Call:
    """A single recorded request."""

    def __init__(self, request: httpx2.Request) -> None:
        self.request = request


class CallList(list[Call]):
    @property
    def last(self) -> Call:
        return self[-1]


SideEffect = httpx2.Response | Exception | type[Exception]


class Route:
    """One registered (method, url) expectation."""

    def __init__(self, method: str, url: str) -> None:
        self.method = method.upper()
        self._url = httpx2.URL(url)
        self.calls = CallList()
        self._return_value: httpx2.Response | None = None
        self._side_effect: list[SideEffect] | None = None

    # -- registration ---------------------------------------------------

    def mock(
        self,
        return_value: httpx2.Response | None = None,
        side_effect: SideEffect | Sequence[SideEffect] | None = None,
    ) -> Route:
        if return_value is not None:
            self._return_value = return_value
        if side_effect is not None:
            self.side_effect = side_effect  # type: ignore[assignment]
        return self

    def respond(
        self,
        status_code: int = 200,
        *,
        json: Any = None,
        text: str | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Route:
        kwargs: dict[str, Any] = {"headers": headers}
        if json is not None:
            kwargs["json"] = json
        if text is not None:
            kwargs["text"] = text
        if content is not None:
            kwargs["content"] = content
        self._return_value = httpx2.Response(status_code, **kwargs)
        return self

    @property
    def side_effect(self) -> list[SideEffect] | None:
        return self._side_effect

    @side_effect.setter
    def side_effect(self, value: SideEffect | Sequence[SideEffect]) -> None:
        # respx accepts a bare exception/response or an ordered sequence. A bare
        # value is reused for every call; a sequence is consumed one per call.
        if isinstance(value, list | tuple):
            self._side_effect = list(value)
        else:
            self._side_effect = [value]
            self._repeat_single = True

    # -- matching / replay ----------------------------------------------

    def matches(self, request: httpx2.Request) -> bool:
        if request.method != self.method:
            return False
        if (request.url.scheme, request.url.host, request.url.port) != (
            self._url.scheme,
            self._url.host,
            self._url.port,
        ):
            return False
        # Compare the raw (wire-encoded) path, so a percent-encoded path param
        # is matched exactly as sent rather than after decoding.
        if _raw_path(request.url) != _raw_path(self._url):
            return False
        # A pattern with a query string constrains those params; one without
        # ignores the query entirely (respx semantics).
        return all(request.url.params.get(key) == value for key, value in self._url.params.items())

    def _next(self) -> httpx2.Response:
        effects = self._side_effect
        if effects:
            effect = effects[0] if getattr(self, "_repeat_single", False) else effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            if isinstance(effect, type) and issubclass(effect, Exception):
                raise effect("mocked error")
            return effect
        if self._return_value is not None:
            return self._return_value
        raise AssertionError(f"Route {self.method} {self._url} has no response configured")

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _raw_path(url: httpx2.URL) -> bytes:
    """The wire path, without the query string."""
    return url.raw_path.split(b"?", 1)[0]


class MockRouter:
    """Registry of routes, exposed to clients as an ``httpx2.MockTransport``."""

    def __init__(self) -> None:
        self._routes: list[Route] = []
        self.transport = httpx2.MockTransport(self._handle)

    # -- registration ---------------------------------------------------

    def route(self, method: str, url: str) -> Route:
        route = Route(method, url)
        self._routes.append(route)
        return route

    def get(self, url: str) -> Route:
        return self.route("GET", url)

    def post(self, url: str) -> Route:
        return self.route("POST", url)

    def put(self, url: str) -> Route:
        return self.route("PUT", url)

    def delete(self, url: str) -> Route:
        return self.route("DELETE", url)

    # -- scoping ---------------------------------------------------------

    @contextmanager
    def scope(self, *, assert_all_called: bool = True) -> Iterator[MockRouter]:
        """Register routes for the duration of a block.

        Mirrors ``respx.mock(assert_all_called=...)``: routes registered inside the
        block are discarded on exit, and (by default) every one of them must have
        been called at least once.
        """
        self._routes = []
        try:
            yield self
        except BaseException:
            self._routes = []
            raise
        registered = self._routes
        self._routes = []
        if assert_all_called:
            uncalled = [f"{r.method} {r._url}" for r in registered if not r.called]
            if uncalled:
                raise AssertionError("Mocked routes were never called: " + ", ".join(uncalled))

    # -- transport handler -----------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        for route in self._routes:
            if route.matches(request):
                route.calls.append(Call(request))
                return route._next()
        raise AssertionError(f"Unmocked request: {request.method} {request.url}")
