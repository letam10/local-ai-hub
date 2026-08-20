# V6 Whisper first-party contract

`Services/Whisper/transcribe_japanese_clip.py` is now tracked first-party
source, invoked only by the tracked Hub wrapper and a Hub-owned worker process.
It is not a disposable test helper.

## Preservation and replacement

The pre-remediation untracked helper was preserved in the P0 reconciliation
bundle before this source package. Its recorded SHA-256 is
`c9871f2bfd1e05af16b683a8bab51c490ee697c8777a3179f93d34cb7b1d85e1`.
The canonical untracked copy must not be deleted as part of source integration;
the canonical update procedure must verify the preserved copy before replacing
it with this tracked contract.

## Boundary

- Public `transcribe_media` and `create_subtitled_video` requests carry only a
  server-owned `source_artifact_id`.  Raw paths and path-like payload fields
  are rejected before Job Manager submission or worker invocation; the client
  cannot select a local model, executable, command, or manifest.
- The adapter selects exactly one Faster-Whisper model ID from the local,
  server-owned model registry; browser/job payloads cannot select a model path.
- The adapter resolves the opaque source through the existing artifact store
  and accepts only a published audio/video artifact after type, file and
  reparse checks.  The resolved source path is an internal worker hand-off and
  is never a public job field.
- The worker revalidates the selected model under the Hub Models root and emits
  only finite status codes and an opaque transcript token.
- Transcript JSON is `localaihub-transcript.v2` with deterministic
  `start`, `end`, and normalized `text` segment fields.  The wrapper creates
  the matching SRT from those segments.
- Worker receipts contain no workstation path, command, secret, or stderr
  detail.  The adapter returns a bounded internal `files` batch containing the
  transcript JSON and SRT; the job publisher requires both files and converts
  them atomically to opaque artifact IDs carrying shared job provenance.  A
  scalar `srt` path is rejected and never persisted.
- Subtitle composition may consume the private SRT and source paths inside the
  Hub process only; the final public result is still an opaque artifact
  publication through the existing artifact store and transport.
- A successful standalone environment import is not an operational claim.
  Promotion still requires a separately authorized bounded Hub job/artifact
  smoke with current runtime/model/component evidence.
