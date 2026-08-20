# Component repair, update and uninstall

Every destructive component action is inspect → plan → stale-state recheck →
explicit confirmation → apply → receipt refresh. Repair only restores known
owned leaves from a reviewed source; it never fabricates missing bytes. Update
stages an immutable candidate, preserves unchanged files and a rollback slot,
then switches atomically after verification. Uninstall deletes only catalog
owned ordinary leaves and preserves unknown files, shared dependencies,
Projects, Output, Backups and user media.

If a source is unavailable, an installed operational component remains usable;
Repair offers Manual Import or a trusted fallback instead of deleting it.
