# Live viewer, reader panel and search

Back to [README](../README.md)

The live dashboard (`live_dashboard.py`, with the message, context and search queries in `session_timeline.py`) is started either by `watch --live-dir ... --port ...` alongside the collector, or by a separate read-only `serve` process. Its Korean labels, mind map and display-name controls are described in [korean-ui.md](korean-ui.md); the collector itself in [collection.md](collection.md).

## Live dashboard directory and server

Use a new, empty directory for `--live-dir`; existing files are not overwritten unless the directory was already created for this database. The server binds only to `127.0.0.1` and serves its dashboard HTML, aggregate status, read-only message and search queries and explicit local display-preference edits. Viewing never starts a model: the only analysis endpoints are the explicitly confirmed per-session job described in [analysis.md](analysis.md).

To retain IDs from an existing exported graph and show a preserved Conversation Map alongside it:

```powershell
python .\context_graph.py --db .\local-data\test.db watch --interval 10 `
  --live-dir .\local-data\live --port 8765 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

Snapshots are read-only inputs. The optional Conversation Map retains its original analysis, question timeline, evidence, IDs and relationships. If its source HTML is explicitly regenerated later, the watcher copies that update. It does not generate new question nodes from unanalysed messages. Existing offline HTML files remain usable snapshots; automatic status updates are available at the printed local address. The map snapshot is reachable at `?view=map`; the dashboard itself opens straight into the mind map.

## Reading original messages

The dashboard has a single screen: the mind map, a tree that grows from the session at the left through its topics and questions to its answers. Topics are always shown; questions and answers open when their branch is clicked. Choosing a project above the map makes that project the root with its sessions as branches; a session branch can be made the root (**이 세션만 보기**) and the root climbs back up. The tree pans by dragging, zooms with Ctrl+wheel or pinch, fits with **전체 보기**, and can be walked with the arrow keys; the search box lights matching branches of the open session. A session without analysis grows session → request → response instead, with its user requests and assistant responses in original order. Selecting a question, request or response opens the **reader panel** at the bottom with that message and its surrounding conversation.

The reader panel is the full reading surface:

- **앞선 N개 / 다음 N개** page through the session in units of 7 (the default around a selected message) or 100 (**100개씩 보기**).
- **최신 메시지** jumps to the end; **새 메시지 따라가기** polls this session's message count every two seconds and reloads the last page when it grows, keeping the end in view if it was already visible.
- **전체 높이** expands the panel over the map; Escape or the same button restores it.
- **글자 크기** offers three sizes and is remembered by the browser.
- **이 세션의 원문 읽기** on a selected session opens the panel full height, 100 messages per page, starting at the remembered reading position or the first message.

Positions, favourites and recent sessions are stored only in that browser (**즐겨찾기·최근 본**, **이어서 읽기**).

Use **원문 검색** to search the literal text of collected messages, including sessions without analyses. Korean fragments, one-character terms, punctuation and case-insensitive English are supported. Results show matching excerpts and open either the reader panel at that message or the verified question in the mind map. The search response's `analysisNodeIds` contains only source item IDs with rechecked evidence on that exact message.

Session cards keep meaningful saved analysis titles. Ordinary non-fork conversations can use an existing Codex title or up to 80 characters of their first usable user request. Forks and helper sessions use only a user request verified against the exact JSONL record after an explicit history boundary, marked **분기 후 요청**. Known environment/AGENTS/browser/page-context wrappers are skipped for titles and previews only; stored content is unchanged.

Lineage is read from the configured `--codex-home` (before `serve` or `watch`), using its existing `state_*.sqlite` and verified JSONL metadata. Missing/unsupported metadata remains unclassified. Reads are cached and read-only; this does not create or modify Codex metadata, collected rows or graph relations.

Existing decisions, questions, actions and other analysis items appear in the mind map beside their verified source messages. Selecting one shows its original summary, rationale, evidence and relationships in the detail panel. The reader rechecks the complete source message and literal quote before displaying a connection. Positional analysis IDs are not assumed to identify the same live message after an earlier message is inserted.

New messages update automatically; new interpretations require an explicit CLI or selected-session analysis request. Imported analyses update the map as well, including changes to rationales that do not alter graph geometry. A transcript/prefix hash establishes the saved analysis range; otherwise the range is marked uncertain. Browsing does not write to the database or rewrite saved analysis.

Original text is served on demand through loopback-only, read-only JSON endpoints (`timeline/messages.json`, `timeline/context.json`, `timeline/turns.json`, `timeline/search.json`). Original conversation text is never embedded in `status.json` or request logs. The reader requires the running local server; the existing graph and Conversation Map snapshots remain available offline.

## Read-only viewer beside a running collector (`serve`)

If a collector is already running, open a separate read-only viewer without restarting it or creating a second collector:

```powershell
python .\context_graph.py --db .\local-data\test.db serve --interval 10 `
  --live-dir .\local-data\viewer --port 8766 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

`serve` opens an existing SQLite database in read-only/query-only mode. It does not collect files, update the database, or run models. New messages and explicitly saved analyses become visible as the existing collector and manual analysis commands update that database. Without a running collector, it continues to show the last stored data. One read-only viewer per audience is enough; every viewer serves the same mind map, reader panel and search.

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

This uses private [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve), not public Funnel. Every viewer request requires the exact `Tailscale-User-Login` injected by Serve, including requests whose Host is rewritten to localhost. Searches accept only the configured HTTPS Origin. Missing or ambiguous identities are denied, including tagged devices without a user identity. The trusted identity never appears in logs or responses.

The original database, evidence and graph IDs stay local and unchanged. Keep the PC and viewer running; the background Serve route persists but does not automatically start the Python viewer after a reboot. Turn off this route with `tailscale serve --https=443 off` when it is no longer needed.

Existing Tailscale viewers can also be managed by the supervisor in [runtime-and-backup.md](runtime-and-backup.md) with their exact `tailscaleOrigin` and `tailscaleUser`; the supervisor does not change Tailscale permissions or exposure.
