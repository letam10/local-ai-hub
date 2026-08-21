# V7 Phase 3.5 API transport finalization

The HTTP server is now a transport shell. Domain behavior is registered by
`src/services/api/router_registry.py` and implemented by route adapters that
call existing services. `api_server.py` owns only request parsing, loopback
server creation, JSON response serialization, static UI serving, artifact
byte streaming, upload staging and the explicit ComfyUI special transport.

## Route ownership

`architecture/api_routes.yaml` is generated from the Router plus the five
explicit low-level transport entries. `architecture/api_aliases.yaml` is the
single alias registry; aliases do not duplicate service/business logic.
The local forensic report in
`Reports/V7_PHASE35_LEGACY_API_AUDIT.local.md` records all 71 Phase 3 input
rows and their finite disposition.

Normal Image & Mask, project, creative, node, jobs, component, media, vision,
voice, video and image routes are JSON adapters. Media/vision/voice/video/image
aliases all converge on the same `submit_tool` service. ComfyUI bridge routes
are marked `SPECIAL_PROTOCOL`; artifact GET/HEAD and uploads are explicit
streaming transports; `/ui` is explicit static transport.

## V5 compatibility

`src/services/api/v5_productization.py` is retained as a compatibility
implementation for durable-job lifecycle records. Current product-surface
composition is exposed through `src/services/product_surface.py`, which is a
small forwarding facade and is not a route registry. No V5 route or product
module is imported by route adapters.

## Safety and startup

Route registration is metadata-only and performs no model, GPU, process,
network-provider or filesystem workload. Path-bearing media input remains
owned by the existing artifact/Job Manager contracts. Streaming preserves
opaque artifact IDs, full GET, HEAD-without-body, single-range GET and 416 for
invalid or multiple ranges. Uploads retain bounded Content-Length, chunked
SHA-256 staging and atomic publication.
