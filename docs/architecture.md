# Architecture and limitations

Back to [README](../README.md)

## Data flow

```text
Codex rollout JSONL -> read-only scanner -> SQLite
                                         -> session transcript
                                         -> Codex analyzer (explicit opt-in)
                                         -> Conversation Map validation/build
                                         -> map.json imports
                                         -> conservative cross-session merge
                                         -> offline HTML / JSON graph
```

Local Claude Code `projects/**/*.jsonl` transcripts enter the same read-only scanner through `source_adapters.py` when `--claude-home` is given. The live dashboard, timeline and search read the SQLite database only; see [viewer.md](viewer.md). The module-by-module layout is listed in the [README](../README.md#project-layout).

## Limitations

- Experimental local JSONL parsing; Codex may change its event schema.
- Every model request is limited to roughly 60,000 serialized characters; longer sessions are split automatically. Cross-chunk conflict/decision reconciliation is not yet implemented.
- No semantic entity resolution, inference of cross-session contradictions, or branch-specific canonical decisions.
- No MCP server or automatic Windows background service yet. (`service_runtime.py` offers an optional current-user login autostart for its supervised processes; it does not install a Windows service.)
- Latest import per session replaces the previous map. Cross-session node titles must match exactly after normalization to merge; ambiguous title matches stay separate, and existing source bindings take precedence.
- Imported maps receive basic structural validation; upstream Conversation Map's `check` and `build` must be used to establish evidence validation.
- Session files and resulting maps can contain sensitive conversation material; stored content is not redacted. Analysis failure logs omit raw provider/validator diagnostics.
- The automatic Codex analyzer has not been verified end-to-end on an authenticated installation. This is a reconstruction of conversation knowledge, not access to the model's actual internal context.
