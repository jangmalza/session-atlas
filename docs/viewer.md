# Live viewer, timeline and search

Back to [README](../README.md)

The live dashboard (`live_dashboard.py`, with the timeline and search queries in `session_timeline.py`) is started either by `watch --live-dir ... --port ...` alongside the collector, or by a separate read-only `serve` process. Its Korean labels, mind map and display-name controls are described in [korean-ui.md](korean-ui.md); the collector itself in [collection.md](collection.md).

## Live dashboard directory and server

Use a new, empty directory for `--live-dir`; existing files are not overwritten unless the directory was already created for this database. The server binds only to `127.0.0.1` and serves its dashboard HTML, aggregate status, read-only timeline queries and explicit local display-preference edits. Viewing never starts a model: the only analysis endpoints are the explicitly confirmed per-session job described in [analysis.md](analysis.md). `--port 0` chooses an available port and prints the URL. Stop the watcher with Ctrl+C. No Windows service is installed; the optional login autostart in [runtime-and-backup.md](runtime-and-backup.md) must be enabled explicitly.

To retain IDs from an existing exported graph and show a preserved Conversation Map alongside it:

```powershell
python .\context_graph.py --db .\local-data\test.db watch --interval 10 `
  --live-dir .\local-data\live --port 8765 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

Snapshots are read-only inputs. The optional Conversation Map retains its original analysis, question timeline, evidence, IDs and relationships. If its source HTML is explicitly regenerated later, the watcher copies that update. It does not generate new question nodes from unanalysed messages. Existing offline HTML files remain usable snapshots; automatic status updates are available at the printed local address while the watcher is running.

## Session and message timeline with existing analyses

Use **전체 원문 검색** to search the literal text of collected messages, including sessions without analyses. Korean fragments, one-character terms, punctuation and case-insensitive English are supported. Narrow by the selected project, all/current session, or speaker. Results show matching excerpts with highlights and are paged 30 messages at a time. Selecting a result opens a window centred on the original message, expands its full text and highlights the match; previous/next message pages let you keep reading the surrounding conversation. **검색 결과 다시 보기** returns to the same results. Search is requested explicitly and does not run models or change stored data. Terms are sent in a bounded POST body rather than URLs/access logs. The initial implementation scans deduplicated text read-only, without creating a search index in the database.

Open **세션 · 메시지 타임라인** in the live dashboard. Select a session, search its title/project/ID, filter by project or show only sessions with saved analyses. The timeline displays collected user/assistant messages in the same order as transcript export, with original text, timestamps and stable collection IDs. Missing times are labelled rather than invented. Messages are paged in groups of 100; use the latest-message button or toggle automatic following while reading older pages.

Session cards keep meaningful saved analysis titles. Ordinary non-fork conversations can use an existing Codex title or up to 80 characters of their first usable user request. Forks and helper sessions use only a user request verified against the exact JSONL record after an explicit history boundary, marked **분기 후 요청**. Known environment/AGENTS/browser/page-context wrappers are skipped for display only. If a source file or verifiable request is unavailable (including encrypted helper task messages), the UI shows the recorded task name when available and **고유 요청 확인 불가**, rather than reusing inherited parent dialogue. These are deterministic display labels, not model-generated summaries; original messages, saved analyses and IDs are never rewritten. Titles can be searched, and the session ID remains in the card tooltip.

Lineage is read from the configured `--codex-home` (before `serve` or `watch`), using its existing `state_*.sqlite` and verified JSONL metadata. Missing/unsupported metadata remains unclassified. Reads are cached and read-only; this does not create or modify Codex metadata, collected rows or graph relations.

Request excerpts are served through the live session catalogue. Offline question-order metadata keeps its previous non-source fallback title instead of embedding new conversation excerpts; saved analysis titles remain intact.

Existing decisions, questions, actions and other analysis items appear beside their verified source messages. Selecting one shows its original summary, rationale, evidence and relationships. The reader rechecks the complete source message and literal quote before displaying a connection. Positional analysis IDs are not assumed to identify the same live message after an earlier message is inserted. Ambiguous or unsupported anchors remain under **시점 미확정 분석**, with their stored evidence and relationships intact.

Use **정리된 내용** to browse all saved analysis items in a session, including items outside the current message page. Filter cards by decisions, questions, actions, proposals or other classifications. Cards show titles, summary previews and source-message positions; the inspector shows the complete saved details and evidence. Category colours supplement the text labels. The inspector opens when an item is selected and can be closed to widen the conversation. **글자 크기** offers 17/19/21px reading sizes, and the session list can be hidden for more reading space. The font preference is stored locally in the browser; these display settings do not change source or analysis data.

New messages update automatically; new interpretations require an explicit CLI or selected-session analysis request. Imported analyses update the timeline as well, including changes to rationales that do not alter graph geometry. A transcript/prefix hash establishes the saved analysis range; otherwise the range is marked uncertain. Browsing does not write to the database or rewrite saved analysis, source text, evidence, IDs or relationships.

The timeline serves the selected session's original text on demand through loopback-only, read-only JSON endpoints. Original conversation text is never embedded in `status.json` or request logs. The page requires the running local server; the existing graph and Conversation Map snapshots remain available offline.

## Read-only viewer beside a running collector (`serve`)

If a collector is already running, open a separate read-only viewer without restarting it or creating a second collector:

```powershell
python .\context_graph.py --db .\local-data\test.db serve --interval 10 `
  --live-dir .\local-data\timeline-view --port 8766 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

`serve` opens an existing SQLite database in read-only/query-only mode. It does not collect files, update the database, or run models. New messages and explicitly saved analyses become visible as the existing collector and manual analysis commands update that database. Without a running collector, it continues to show the last stored data.

## Private Tailscale viewer

Use a separate viewer directory and port to keep ordinary localhost access unchanged. Both options below are required together; substitute the exact device HTTPS `.ts.net` origin and the permitted Tailscale login:

```powershell
python .\context_graph.py --db .\local-data\test.db serve `
  --live-dir .\local-data\tailscale-view --port 8767 `
  --graph-snapshot .\local-data\graph.ko.html `
  --tailscale-origin https://device.example.ts.net `
  --tailscale-user user@example.com
```

With Tailscale connected, run this in another terminal after checking `tailscale serve status` for existing routes:

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8767
```

This uses private [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve), not public Funnel. Every viewer request requires the exact `Tailscale-User-Login` injected by Serve, including requests whose Host is rewritten to localhost. Searches accept only the configured HTTPS Origin. Missing or ambiguous identities are denied, including tagged devices without a user identity. The trusted-proxy boundary requires the server to remain bound to loopback; this does not isolate other local processes on the same PC.

The original database, evidence and graph IDs stay local and unchanged. Keep the PC and viewer running; the background Serve route persists but does not automatically start the Python viewer after a reboot. Turn off this route with `tailscale serve --https=443 off` when it is no longer needed.

Existing Tailscale viewers can also be managed by the supervisor in [runtime-and-backup.md](runtime-and-backup.md) with their exact `tailscaleOrigin` and `tailscaleUser`; the supervisor does not change Tailscale permissions or exposure.
