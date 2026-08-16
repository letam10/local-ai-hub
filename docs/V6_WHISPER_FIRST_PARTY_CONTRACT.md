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

- The adapter selects exactly one Faster-Whisper model ID from the local,
  server-owned model registry; browser/job payloads cannot select a model path.
- The worker revalidates the selected model under the Hub Models root and emits
  only finite status codes and an opaque transcript token.
- Transcript JSON is `localaihub-transcript.v2` with deterministic
  `start`, `end`, and normalized `text` segment fields.  The wrapper creates
  the matching SRT from those segments.
- Worker receipts contain no workstation path, command, secret, or stderr
  detail.  The adapter reconstructs private output paths only inside the
  Hub-owned job process; the normal job publicizer converts them to opaque
  artifacts before API/UI exposure.
- A successful standalone environment import is not an operational claim.
  Promotion still requires a separately authorized bounded Hub job/artifact
  smoke with current runtime/model/component evidence.
