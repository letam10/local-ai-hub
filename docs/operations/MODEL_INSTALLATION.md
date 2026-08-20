# Model installation contract

Model Manager uses `Config/model_catalog.example.json` as a tracked schema and
catalog example. It reports a plan before any write: model ID, source, license,
estimated download/disk size, authentication requirement and affected modules.

An explicit future installation must use an official HTTPS source or an
explicit manual import, verify a checksum when supplied, stage under a task
temporary root, reject traversal/reparse escapes, atomically finalize only an
absent target, and write an installation receipt. Existing working model files
are never overwritten automatically. Shared models retain `modules_using_model`
references and are not removed when one module is disabled.

No model is downloaded by core bootstrap and no conversational/chat model is
implicitly installed for AIRI or Hub startup.
