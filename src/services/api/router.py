"""Explicit, deterministic route registration for the V7 API boundary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs

from .context import ApiContext
from .errors import safe_error_response
from .response import ApiResponse


class DuplicateRouteError(ValueError):
    pass


class RoutePatternError(ValueError):
    pass


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    query: Mapping[str, list[str]]
    headers: Mapping[str, str]
    _body_reader: Callable[[bool], dict[str, Any]] | None = None

    def json(self, *, strict: bool = True) -> dict[str, Any]:
        if self._body_reader is None:
            return {}
        return self._body_reader(strict)


Handler = Callable[[ApiRequest, ApiContext, Mapping[str, str]], ApiResponse]


@dataclass(frozen=True)
class Route:
    route_id: str
    method: str
    path: str
    domain: str
    owner: str
    handler: Handler
    streaming: bool = False
    transport_class: str = "JSON"

    @property
    def key(self) -> tuple[str, str]:
        return self.method.upper(), self.path

    def match(self, path: str) -> dict[str, str] | None:
        names: list[str] = []
        chunks: list[str] = []
        for part in self.path.strip("/").split("/") if self.path != "/" else []:
            if part.startswith("{") and part.endswith("}"):
                name = part[1:-1]
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                    raise RoutePatternError("invalid_route_parameter")
                names.append(name)
                chunks.append(r"([^/]+)")
            else:
                chunks.append(re.escape(part))
        pattern = r"^/" + "/".join(chunks) + r"/?$" if chunks else r"^/?$"
        match = re.fullmatch(pattern[1:-1] if pattern.startswith("^") else pattern, path)
        if not match:
            return None
        return dict(zip(names, match.groups()))


class Router:
    """An allowlisted route table with duplicate registration protection."""

    def __init__(self) -> None:
        self._routes: list[Route] = []
        self._keys: set[tuple[str, str]] = set()

    def register(self, *, route_id: str, method: str, path: str, domain: str, owner: str, handler: Handler, streaming: bool = False, transport_class: str = "JSON") -> Route:
        route = Route(route_id, method.upper(), path, domain, owner, handler, streaming, transport_class)
        if route.key in self._keys:
            raise DuplicateRouteError(f"duplicate_route:{route.method}:{route.path}")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,95}", route_id):
            raise RoutePatternError("invalid_route_id")
        self._keys.add(route.key)
        self._routes.append(route)
        # Static routes sort before parameterized routes, making matching
        # deterministic without accepting arbitrary filesystem patterns.
        self._routes.sort(key=lambda item: (item.path.count("{"), -len(item.path), item.method))
        return route

    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def resolve(self, request: ApiRequest) -> tuple[Route, dict[str, str]] | None:
        for route in self._routes:
            if route.method != request.method.upper():
                continue
            params = route.match(request.path)
            if params is None:
                continue
            return route, params
        return None

    def dispatch(self, request: ApiRequest, context: ApiContext) -> ApiResponse | None:
        resolved = self.resolve(request)
        if resolved is None:
            return None
        route, params = resolved
        try:
            return route.handler(request, context, params)
        except Exception as exc:  # route adapters never expose internals
            return safe_error_response(exc)


def request_from_handler(handler: Any, method: str) -> ApiRequest:
    parsed = handler._route_parsed_path  # set by HubHandler before dispatch
    return ApiRequest(
        method=method.upper(),
        path=parsed.path,
        query=parse_qs(parsed.query, keep_blank_values=True),
        headers={str(key).lower(): str(value) for key, value in handler.headers.items()},
        _body_reader=lambda strict: handler._read_json(strict=strict),
    )


__all__ = ["ApiRequest", "DuplicateRouteError", "Route", "Router", "RoutePatternError", "request_from_handler"]
