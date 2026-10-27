# Collection, deduplication and archives

Back to [README](../README.md)

Collection is read-only and incremental. Original session files are never rewritten, and the collector never calls a model or an external API.

## Sources and default paths

Codex sessions are collected from `CODEX_HOME/sessions/**/rollout-*.jsonl` and `archived_sessions`. By default, `CODEX_HOME` is read from `$env:CODEX_HOME` or `$HOME\.codex`; pass `--codex-home` before the subcommand to override it. The SQLite database is written outside the repository to `$HOME\.codex-context-graph\knowledge.db`, or to the path given with `--db` before the subcommand. Collection uses SQLite checkpointing, session deduplication and user/assistant text-only normalization.

Python 3.10+ is supported; Python 3.14 is recommended for compressed archives. Windows 11 is supported.

## Claude Code adapter

Pass `--claude-home "$HOME\.claude"` before the subcommand to collect local Claude Code transcripts as well; omit it to collect only Codex. Native Claude desktop/cloud chat stores are not imported; the adapter (`source_adapters.py`) reads local Claude Code `projects/**/*.jsonl` text messages, preserves native message/parent UUIDs and excludes thinking/tool/API-error blocks. Helper transcripts remain separate from ordinary sessions. Original files are never rewritten.

## Compressed Codex archives (`.jsonl.zst`)

Codex `.jsonl.zst` archives are collected on Python 3.14+ using uncompressed offsets for stable message identities. Unchanged compressed files reuse their checkpoint; original and archived copies keep the same message IDs. Verified observations let matching event/response mirrors be deduplicated across archive copies without merging unrelated repeated requests.

On Python 3.14+ the viewer's request previews can also read an exact `.jsonl.zst` sibling of a known rollout path; see [korean-ui.md](korean-ui.md).

## Continuous collection (`watch`)

```powershell
python .\context_graph.py watch --interval 10
# Automatic collection plus a local, automatically updating HTML dashboard:
python .\context_graph.py watch --interval 10 --live-dir "$HOME\.codex-context-graph\live" --port 8765
Start-Process "http://127.0.0.1:8765/"
```

The `watch` collection loop never calls a model. The old `watch --analyze` flags are rejected. Request analysis separately with `analyze --allow-model` or the live viewer's selected-session confirmation (see [analysis.md](analysis.md)).

New messages are deduplicated into SQLite and counted as awaiting analysis. The live page polls aggregate collection status; incoming raw messages do not reload or alter the displayed analysis. Explicitly imported/analyzed results update the graph, preserving existing projection IDs through source-node provenance. Refreshes use atomic file replacement and leave collected messages, stored maps, source quotes, and analysis caches alone.

Stop the watcher with Ctrl+C. No Windows service or startup task is installed by `watch`; the optional supervisor is described in [runtime-and-backup.md](runtime-and-backup.md). The live dashboard options (`--live-dir`, `--port`, snapshots, the read-only `serve` viewer and the Tailscale viewer) are described in [viewer.md](viewer.md).

## Event/response mirror deduplication

The transcript view treats an `event_msg` and a `response_item` as mirrors only when they are adjacent in the same source file, have identical roles/text, have timezone-aware timestamps within one second in file order, and uniquely match each other. It keeps the `response_item` ID for a confirmed pair. Later repetitions, intervening conversation, missing times and ambiguous pairs remain visible; no raw message row is deleted. Graph projection likewise keeps distinct source IDs within each session. If an older projection merged them, one deterministic source retains the existing graph ID and the others receive separate stable IDs, restoring their explicit relations without rewriting the saved map.

## Multiple Git worktrees

Supply `--project-map path-to-projects.json` **before** the subcommand. Example mapping:

```json
{
  "C:\\projects\\OmniRoute": "OmniRoute",
  "C:\\worktrees\\OmniRoute": "OmniRoute"
}
```

```powershell
python .\context_graph.py --project-map .\projects.json scan
```
