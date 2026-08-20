"""ComfyUI bridge routes; the backend owns process and workflow semantics."""

from __future__ import annotations

from typing import Mapping

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def health(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "comfyui": context.call("comfy_health")})


def start(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("comfy_start")
    return ApiResponse(status, payload)


def workflows(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return ApiResponse(200, {"status": "completed", "workflows": context.call("comfy_workflows")})


def workflow(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.call("comfy_workflow", params["workflow_id"])
    return ApiResponse(200 if value is not None else 404, {"status": "completed", "workflow": value} if value is not None else {"status": "error", "error": "comfy_workflow_not_found"})


def save_workflow(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    status, payload = context.call("comfy_save_workflow", params["workflow_id"], request.json(strict=True))
    return ApiResponse(status, payload)


def register(router: Router) -> None:
    owner = "src/services/api/routes/comfyui.py"
    router.register(route_id="comfy.health", method="GET", path="/api/comfyui/advanced", domain="comfyui", owner=owner, handler=health, transport_class="SPECIAL_PROTOCOL")
    router.register(route_id="comfy.start", method="POST", path="/api/comfyui/advanced/start", domain="comfyui", owner=owner, handler=start, transport_class="SPECIAL_PROTOCOL")
    router.register(route_id="comfy.workflows", method="GET", path="/api/comfyui/workflows", domain="comfyui", owner=owner, handler=workflows, transport_class="SPECIAL_PROTOCOL")
    router.register(route_id="comfy.workflow", method="GET", path="/api/comfyui/workflows/{workflow_id}", domain="comfyui", owner=owner, handler=workflow, transport_class="SPECIAL_PROTOCOL")
    router.register(route_id="comfy.workflow_save", method="PUT", path="/api/comfyui/workflows/{workflow_id}", domain="comfyui", owner=owner, handler=save_workflow, transport_class="SPECIAL_PROTOCOL")
