# V7 migration plan

Migration is incremental and compatibility-first. Each package must pass its
focused tests before the next package begins.

1. **A — metadata and ownership:** manifests, ownership map, dependency rules,
   baseline and impact map.
2. **B — platform paths/filesystem:** central APP_ROOT/DATA_ROOT facade and
   containment helpers while preserving V6 imports.
3. **C — module manifests:** strict data-only manifest validation and allowlisted
   discovery.
4. **D — Model Manager:** catalog, discovery, safe plans, fake installation and
   receipts; no real download in the foundation.
5. **E — Runtime Manager:** runtime/environment catalog, leaf inspection and
   receipts; no environment rebuild in the foundation.
6. **F — core bootstrap:** clean clone, model-free startup, local Config
   initialization, WebView/runtime readiness reporting.
7. **G — Module Manager integration:** compose manifest + runtime + model +
   evidence states.
8. **H/I — API/UI extraction:** move bounded routes/features gradually with
   compatibility forwarding.
9. **J — architecture CI:** import direction, manifest/catalog, path, weight and
   version guards.
10. **K — documentation/version cleanup:** current README and compatibility
    markers; retain historical milestone docs.

No package may silently download models, alter drivers/CUDA, migrate existing
installations, or mark an AI capability operational without a bounded receipt.
