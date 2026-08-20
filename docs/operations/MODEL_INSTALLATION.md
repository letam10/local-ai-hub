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
# Model installation and import

Model Manager performs bounded discovery under the configured Models root. It
does not scan drives or hash multi-gigabyte files at startup. Presence and
size can produce `INSTALLED_UNVERIFIED`; only a matching bounded smoke receipt
can produce `OPERATIONAL`.

Use the server-owned flow: inspect -> plan -> explicit confirmation -> staged
download or native opaque selection -> checksum/size verification -> atomic
finalization -> receipt -> verify. Browser requests contain a model ID and
opaque `selection_id` only. Raw filesystem paths, credentials, download URLs,
commands and model file lists are never accepted from the browser.

The tracked example catalog currently records official metadata without
complete pinned assets/digests, so automatic download is disabled. Existing
compatible files are reused and remain untouched; manual import is the safe
route until a reviewed source recipe exists. Shared models are retained while
any module references them.

## V7 production disposition

`Config/v7_production_catalog.example.json` is the final user-facing catalog.
Every entry declares `AUTO_INSTALL_READY`, `AUTH_REQUIRED`,
`LICENSE_REQUIRED`, `MANUAL_IMPORT_ONLY`, or `UNSUPPORTED_SOURCE`. The UI
shows a matching action and never invents a download size. Installed size is
read from a receipt/cache or shown as `Size unavailable`; a bounded size scan is
explicitly requested and never runs during every refresh.
