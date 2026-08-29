"""Path-free external-application integration projections for Milestone 3."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION = "external-integrations.v2"
EXECUTION_NOT_RUN = "not_run"
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_CONNECTION_STATES = frozenset({"NOT_INSTALLED", "INSTALLED_NOT_CONNECTED", "CONNECTED", "UNSUPPORTED_API", "AUTH_REQUIRED"})


@dataclass(frozen=True)
class ExternalIntegrationSpec:
    integration_id: str
    display_name: str
    application_id: str
    category: str
    official_channel: str
    supports_official_connection: bool


# This is an integration framework catalog, not discovery of arbitrary desktop
# software.  AIRI specifically remains external-managed until an official,
# separately reviewed API or IPC adapter is registered.
_SPECS: tuple[ExternalIntegrationSpec, ...] = (
    ExternalIntegrationSpec("airi", "AIRI", "airi", "external_ai_application", "none", False),
    ExternalIntegrationSpec("blender", "Blender", "blender", "external_creative_application", "none", False),
    ExternalIntegrationSpec("comfyui_external", "ComfyUI external", "comfyui-external", "external_ai_application", "official_loopback_api", True),
)
_BY_ID = {item.integration_id: item for item in _SPECS}


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _application_index(snapshot: object) -> dict[str, Mapping[str, Any]]:
    rows = snapshot if isinstance(snapshot, list) else []
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = _safe_id(row.get("id"))
        if identifier is not None:
            result[identifier] = row
    return result


def _trusted_observation(value: object) -> str | None:
    """Accept only a server-injected state enum, never endpoint/credential data."""

    if not isinstance(value, Mapping) or set(value) != {"connection_state"}:
        return None
    state = value.get("connection_state")
    return state if isinstance(state, str) and state in _CONNECTION_STATES else None


class ExternalIntegrationRegistry:
    """Finite, read-only integration view over an already-sanitized app list."""

    def __init__(
        self,
        *,
        applications_snapshot: Callable[[], list[Mapping[str, Any]]] | None = None,
        observations_snapshot: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
    ) -> None:
        self._applications_snapshot = applications_snapshot or (lambda: [])
        self._observations_snapshot = observations_snapshot or (lambda: {})

    def _state(self, spec: ExternalIntegrationSpec) -> tuple[str, Mapping[str, Any] | None]:
        applications = _application_index(self._applications_snapshot())
        app = applications.get(spec.application_id)
        if app is None:
            return "NOT_INSTALLED", None
        component_state = str(app.get("component_status") or "missing").lower()
        if component_state in {"missing", "not_installed", "unavailable", "unknown"}:
            return "NOT_INSTALLED", app
        observations = self._observations_snapshot()
        observation = _trusted_observation(observations.get(spec.integration_id)) if isinstance(observations, Mapping) else None
        if observation == "AUTH_REQUIRED":
            return "AUTH_REQUIRED", app
        if spec.supports_official_connection and observation == "CONNECTED":
            return "CONNECTED", app
        if spec.official_channel == "none":
            return "UNSUPPORTED_API", app
        return "INSTALLED_NOT_CONNECTED", app

    def _detail(self, spec: ExternalIntegrationSpec) -> dict[str, Any]:
        connection_state, app = self._state(spec)
        launchable = bool(app and app.get("launchable") is True)
        if connection_state == "NOT_INSTALLED":
            reason = "The allowlisted external application is not installed or its local registry record is unavailable."
            next_action = "Install or register the application with its own installer, then refresh this server-owned projection."
        elif connection_state == "AUTH_REQUIRED":
            reason = "The external application requires its own authorization; Hub does not read or store its credentials."
            next_action = "Complete authorization in the external application using its supported settings flow."
        elif connection_state == "CONNECTED":
            reason = "A separately registered official integration channel is connected."
            next_action = "Review the exact official integration contract before using it."
        elif connection_state == "UNSUPPORTED_API":
            reason = "The application may be installed, but no supported official API or IPC adapter is registered."
            next_action = "Use the external application directly; do not expose or guess an endpoint, key or WebView bridge."
        else:
            reason = "The application is installed but no official connection has been established."
            next_action = "Connect only through a separately reviewed official API or IPC adapter."
        return {
            "schema_version": EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION,
            "integration_id": spec.integration_id,
            "display_name": spec.display_name,
            "category": spec.category,
            "installation_state": "INSTALLED" if app is not None and connection_state != "NOT_INSTALLED" else "NOT_INSTALLED",
            "connection_state": connection_state,
            "official_channel": spec.official_channel,
            "credentials": "external_managed",
            "launch_capability": {
                "state": "AVAILABLE" if launchable else "UNAVAILABLE",
                "mode": "server_owned_allowlist" if launchable else "not_available",
                "reason": "A user may request the existing server-owned allowlisted launcher; Hub does not receive executable arguments from the browser." if launchable else "No server-owned allowlisted launcher is currently available.",
            },
            "reason": reason,
            "next_action": next_action,
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def snapshot(self) -> dict[str, Any]:
        integrations = [self._detail(spec) for spec in _SPECS]
        return {
            "schema_version": EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION,
            "status": "completed",
            "integrations": integrations,
            "counts": {state: sum(1 for item in integrations if item["connection_state"] == state) for state in sorted(_CONNECTION_STATES)},
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
            "reason": "External integrations are finite, path-free server-owned projections; no desktop application was launched or inspected beyond its existing allowlisted public registry record.",
            "next_action": "Inspect the exact integration state and use only an official, separately reviewed adapter when one exists.",
        }

    def detail(self, integration_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(integration_id)
        spec = _BY_ID.get(identifier or "")
        return deepcopy(self._detail(spec)) if spec is not None else None

    def launch_plan(self, integration_id: object) -> dict[str, Any] | None:
        detail = self.detail(integration_id)
        if detail is None:
            return None
        available = detail["launch_capability"]["state"] == "AVAILABLE"
        return {
            "schema_version": EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION,
            "status": "completed" if available else "unavailable",
            "integration_id": detail["integration_id"],
            "launch_capability": detail["launch_capability"],
            "reason": "This is an explanatory launch plan only. It never launches an application, supplies arguments, reads credentials or opens an external WebView." if available else detail["launch_capability"]["reason"],
            "next_action": "A separately initiated user action may use the existing allowlisted Hub launcher." if available else "Install/register the application through its own supported installer first.",
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }


__all__ = ["EXTERNAL_INTEGRATIONS_V2_SCHEMA_VERSION", "ExternalIntegrationRegistry", "ExternalIntegrationSpec"]
