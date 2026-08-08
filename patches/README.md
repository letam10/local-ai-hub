# Upstream patch workflow

Each directory records the exact upstream revision in `dependencies.lock.json`.
Clone that upstream repository outside this Git repository, check out the
recorded commit, apply any `*.patch` file from the repository root, then copy
the companion `*_cli.py` wrapper into the upstream checkout root.

The wrappers receive locations through `LOCALAIHUB_ROOT`, the component-specific
`*_HOME` variable and the local `Config/components.json` registry. They do not
embed machine-specific paths or model weights.
