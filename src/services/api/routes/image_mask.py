"""Image & Mask Studio JSON transport adapters."""

from __future__ import annotations

from typing import Mapping

from src.services.image_mask_studio import StudioConflictError

from ..context import ApiContext
from ..response import ApiResponse
from ..router import ApiRequest, Router


def _invoke(callback) -> ApiResponse:
    try:
        return ApiResponse(200, callback())
    except StudioConflictError as exc:
        return ApiResponse(409, {"status": "conflict", "error": str(exc), "current_revision": exc.current_revision, "action": "Reload the Studio revision before writing again."})
    except KeyError:
        return ApiResponse(404, {"status": "error", "error": "image_mask_resource_not_found"})
    except ValueError as exc:
        return ApiResponse(400, {"status": "error", "error": str(exc)[:240]})


def overview(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").overview(project_id=request.query.get("project", [None])[0]))


def preflight(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(context.get("image_mask_studio").preflight)


def session_list(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return overview(request, context, params)


def get_session(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    value = context.get("image_mask_studio").get_session(params["session_id"])
    return ApiResponse(200 if value is not None else 404, value or {"status": "error", "error": "image_mask_session_not_found"})


def compare(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").compare(params["session_id"], before_snapshot_id=request.query.get("before", [None])[0], after_snapshot_id=request.query.get("after", [None])[0]))


def export_mask(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").export_mask(params["session_id"], params["layer_id"]))


def create_session(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").create_session(request.json(strict=True)))


def session_action(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    action = params["action"]
    studio = context.get("image_mask_studio")
    body = request.json(strict=True)
    if action == "link-project":
        value = context.call("image_mask_link_project", params["session_id"], body)
        status = 409 if isinstance(value, dict) and value.get("status") == "pending_project_attach" else 200
        return ApiResponse(status, value)
    methods = {"undo": studio.undo, "redo": studio.redo, "save": studio.save, "layers": studio.add_layer, "presets": studio.capture_preset}
    method = methods.get(action)
    if method is None:
        return ApiResponse(404, {"status": "error", "error": "image_mask_action_not_found"})
    return _invoke(lambda: method(params["session_id"], body))


def import_mask(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").import_mask(params["session_id"], request.json(strict=True)))


def layer_action(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    studio = context.get("image_mask_studio")
    action = params["action"]
    body = request.json(strict=True)
    methods = {"operations": studio.apply_mask_operation, "move": studio.move_layer, "remove": studio.remove_layer}
    method = methods.get(action)
    if method is None:
        return ApiResponse(404, {"status": "error", "error": "image_mask_layer_action_not_found"})
    return _invoke(lambda: method(params["session_id"], params["layer_id"], body))


def restore_snapshot(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").restore_snapshot(params["session_id"], params["snapshot_id"], request.json(strict=True)))


def apply_preset(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").apply_preset(params["session_id"], params["preset_id"], request.json(strict=True)))


def update_session(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").update_session(params["session_id"], request.json(strict=True)))


def update_layer(request: ApiRequest, context: ApiContext, params: Mapping[str, str]) -> ApiResponse:
    return _invoke(lambda: context.get("image_mask_studio").update_layer(params["session_id"], params["layer_id"], request.json(strict=True)))


def register(router: Router) -> None:
    owner = "src/services/api/routes/image_mask.py"
    router.register(route_id="image_mask.overview", method="GET", path="/api/image-mask-studio/overview", domain="image_mask_studio", owner=owner, handler=overview)
    router.register(route_id="image_mask.preflight", method="GET", path="/api/image-mask-studio/preflight", domain="image_mask_studio", owner=owner, handler=preflight)
    router.register(route_id="image_mask.sessions", method="GET", path="/api/image-mask-studio/sessions", domain="image_mask_studio", owner=owner, handler=session_list)
    router.register(route_id="image_mask.session", method="GET", path="/api/image-mask-studio/sessions/{session_id}", domain="image_mask_studio", owner=owner, handler=get_session)
    router.register(route_id="image_mask.compare", method="GET", path="/api/image-mask-studio/sessions/{session_id}/compare", domain="image_mask_studio", owner=owner, handler=compare)
    router.register(route_id="image_mask.export", method="GET", path="/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}/export", domain="image_mask_studio", owner=owner, handler=export_mask)
    router.register(route_id="image_mask.create", method="POST", path="/api/image-mask-studio/sessions", domain="image_mask_studio", owner=owner, handler=create_session)
    router.register(route_id="image_mask.action", method="POST", path="/api/image-mask-studio/sessions/{session_id}/{action}", domain="image_mask_studio", owner=owner, handler=session_action)
    router.register(route_id="image_mask.import", method="POST", path="/api/image-mask-studio/sessions/{session_id}/masks/import", domain="image_mask_studio", owner=owner, handler=import_mask)
    router.register(route_id="image_mask.layer_action", method="POST", path="/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}/{action}", domain="image_mask_studio", owner=owner, handler=layer_action)
    router.register(route_id="image_mask.restore", method="POST", path="/api/image-mask-studio/sessions/{session_id}/snapshots/{snapshot_id}/restore", domain="image_mask_studio", owner=owner, handler=restore_snapshot)
    router.register(route_id="image_mask.apply_preset", method="POST", path="/api/image-mask-studio/sessions/{session_id}/presets/{preset_id}/apply", domain="image_mask_studio", owner=owner, handler=apply_preset)
    router.register(route_id="image_mask.put_session", method="PUT", path="/api/image-mask-studio/sessions/{session_id}", domain="image_mask_studio", owner=owner, handler=update_session)
    router.register(route_id="image_mask.put_layer", method="PUT", path="/api/image-mask-studio/sessions/{session_id}/layers/{layer_id}", domain="image_mask_studio", owner=owner, handler=update_layer)
