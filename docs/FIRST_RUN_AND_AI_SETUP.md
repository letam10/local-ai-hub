# First run and optional AI setup

Core opens without model weights. The catalog remains visible and each
optional component reports its local state separately from upstream source
availability. Use `Check Source`, `Check Update`, `Import Model`, or a reviewed
component bundle explicitly. Scheduled checks are metadata-only and never
install automatically.

A component bundle expands runtime, dependency and model steps in server-owned
order. Existing compatible leaves are reused; missing or gated components stay
partial, unavailable, auth-required or license-required until the user takes
the corresponding explicit action.
