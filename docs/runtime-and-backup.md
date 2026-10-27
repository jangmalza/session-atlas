# Managed runtime and backup

Back to [README](../README.md)

## Managed runtime (`service_runtime.py`)

Copy `runtime-profile.example.json` to a private directory outside the repository and replace its example paths. It defines one collector and any local/read-only viewers. One viewer per audience is enough (for example one local read-only viewer and one Tailscale viewer): every viewer serves the same mind map with its reader panel and search, so separate timeline or search viewers are no longer needed. Existing Tailscale viewers can be added with their exact `tailscaleOrigin` and `tailscaleUser`; the supervisor does not change Tailscale permissions or exposure.

```powershell
python .\service_runtime.py start --config "$HOME\.codex-context-graph\profile.json"
python .\service_runtime.py status --config "$HOME\.codex-context-graph\profile.json"
python .\service_runtime.py restart --config "$HOME\.codex-context-graph\profile.json"
python .\service_runtime.py stop --config "$HOME\.codex-context-graph\profile.json"
# Optional Windows current-user login startup, with no visible console:
python .\service_runtime.py enable-autostart --config "$HOME\.codex-context-graph\profile.json"
python .\service_runtime.py disable-autostart --config "$HOME\.codex-context-graph\profile.json"
```

The supervisor restarts only its own children, uses bounded retry delays, refuses occupied ports and stops through an instance-specific control file. A collector heartbeat is stored separately from viewer refresh timestamps, so a viewer can report a stopped or unconfirmed collector. Login startup does not run before Windows sign-in. Collection and viewing never enable model/API analysis.

An editable install (`uv pip install -e .` or `pip install -e .`) also provides the `session-atlas-runtime` console command for the same subcommands.

## Backup and restore

Backups use SQLite's online backup API and include the display-settings sidecar. Include the current graph snapshot to retain merged projection IDs. Restore always writes a **new** database and refuses existing destination files; it verifies archive entry names, checksums and SQLite integrity. Original transcript directories, browser-local favorites, authentication files and runtime configuration are not included. Backups contain collected conversations and must stay private.

```powershell
python .\context_graph.py --db "$HOME\.codex-context-graph\knowledge.db" backup --output "$HOME\.codex-context-graph\backup.zip" --graph-snapshot "$HOME\.codex-context-graph\live\graph.html"
python .\context_graph.py restore --file "$HOME\.codex-context-graph\backup.zip" --output "$HOME\.codex-context-graph\restored.db"
# If a projection was included, use restored.graph.json as --graph-snapshot.
```
