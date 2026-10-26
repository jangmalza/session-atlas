# Session Atlas (MVP)

Local-first conversation archive for Codex CLI/Desktop and local Claude Code transcripts. Python 3.10+; Python 3.14 is recommended for compressed archives. Windows 11 supported.

> Experimental MVP. The automatic Codex analyzer has **not** been verified end-to-end on an authenticated installation. This is a reconstruction of conversation knowledge, not access to the model's actual internal context.

## Capabilities

- Read-only incremental collection from `CODEX_HOME/sessions/**/rollout-*.jsonl` and `archived_sessions`
- SQLite checkpointing, session deduplication, user/assistant text-only normalization
- Export a session transcript for [Conversation Map](https://github.com/tonitalej/conversation-map)
- Import analyzed `map.json` and merge evidence across sessions by project + classification + exactly normalized title, only when the title match is unambiguous; distinct source nodes in one session remain separate
- Export searchable offline HTML and graph JSON
- Opt-in automated analysis using `codex exec` and a separate Conversation Map checkout
- Poll for newly appended session messages

## Quick start (Windows PowerShell)

```powershell
git clone --branch feat/context-graph-mvp https://github.com/jangmalza/session-atlas.git
cd session-atlas
python --version
python .\context_graph.py --claude-home "$HOME\.claude" watch --live-dir "$HOME\.codex-context-graph\live" --port 8765
# Open http://127.0.0.1:8765 in your browser. Ctrl+C stops this foreground run.
```

By default, Codex sessions are read from `$env:CODEX_HOME` or `$HOME\.codex`. The SQLite database is written outside the repository to `$HOME\.codex-context-graph\knowledge.db`.

The session list, full-text search, original-message reader and chronological request/response view work without model calls. Semantic topic/answer graphs use explicitly imported or requested analyses. Omit `--claude-home` to collect only Codex. Native Claude desktop/cloud chat stores are not imported; the adapter reads local Claude Code `projects/**/*.jsonl` text messages, preserves native message/parent UUIDs and excludes thinking/tool/API-error blocks. Helper transcripts remain separate from ordinary sessions. Original files are never rewritten.

Codex `.jsonl.zst` archives are collected on Python 3.14+ using uncompressed offsets for stable message identities. Unchanged compressed files reuse their checkpoint; original and archived copies keep the same message IDs. Verified observations let matching event/response mirrors be deduplicated across archive copies without merging unrelated repeated requests.

## Managed runtime and backup

Copy `runtime-profile.example.json` to a private directory outside the repository and replace its example paths. It defines one collector and any local/read-only viewers. Existing Tailscale viewers can be added with their exact `tailscaleOrigin` and `tailscaleUser`; the supervisor does not change Tailscale permissions or exposure.

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

Backups use SQLite's online backup API and include the display-settings sidecar. Include the current graph snapshot to retain merged projection IDs. Restore always writes a **new** database and refuses existing destination files; it verifies archive entry names, checksums and SQLite integrity. Original transcript directories, browser-local favorites, authentication files and runtime configuration are not included. Backups contain collected conversations and must stay private.

```powershell
python .\context_graph.py --db "$HOME\.codex-context-graph\knowledge.db" backup --output "$HOME\.codex-context-graph\backup.zip" --graph-snapshot "$HOME\.codex-context-graph\live\graph.html"
python .\context_graph.py restore --file "$HOME\.codex-context-graph\backup.zip" --output "$HOME\.codex-context-graph\restored.db"
# If a projection was included, use restored.graph.json as --graph-snapshot.
```

## Browser regression tests

```powershell
npm ci
npx playwright install chromium
npm run test:e2e
```

E2E tests launch a separate Chromium, synthetic SQLite database and local Python server. They never open the user's browser profile, production database or real transcripts; application requests outside the fixture origin are blocked. They check the four-column map, original reader and position restoration, search, bookmarks, management menus, responsive layout, chronological turns and display edits without iframe reload. GitHub Actions runs browser tests on Windows and Ubuntu with Python 3.14; failed traces/screenshots contain only synthetic data. Set `E2E_PYTHON` when a specific Python executable is needed locally.

## Automatic semantic analysis (opt-in)

Requires Node.js 18+, installed/authenticated Codex CLI, and a local checkout of Conversation Map:

```powershell
git clone https://github.com/tonitalej/conversation-map.git "$HOME\src\conversation-map"
python .\context_graph.py scan
python .\context_graph.py analyze --cm-root "$HOME\src\conversation-map" --allow-model
python .\context_graph.py graph --html .\output\graph.html
```

**Privacy:** `--allow-model` sends the exported conversation text to the configured Codex model. The local collector alone does not make model calls. Avoid placing passwords, production credentials, or other secrets in exports. Never commit local databases, transcripts, or generated maps.

## Codex topics + Decisions assignment (explicit opt-in)

The hybrid mode uses Codex to create topic candidates, descriptions, items and evidence, then calls the [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions) to assign those items to existing topic IDs. Existing `analyze` commands remain Codex-only unless `--topic-classifier decisions` is selected. Collection, watch, serve and opening HTML never call either model.

Set `OPENAI_API_KEY` through your environment/secret manager before requesting classification. Keep it out of command arguments, browser code, logs and Git. The implementation uses Python's standard library and the fixed `https://api.openai.com/v1/decisions` endpoint with `gpt-6-luna`; it does not install an SDK or read credentials during collection/viewing.

For a new session, explicitly allow both operations:

```powershell
python .\context_graph.py --db .\local-data\test.db analyze `
  --session "YOUR-SESSION-ID" `
  --cm-root "$HOME\src\conversation-map" `
  --topic-classifier decisions `
  --allow-model --allow-api
```

For a session with a saved analysis, classify its existing items without running Codex again:

```powershell
python .\context_graph.py --db .\local-data\test.db assign-topics `
  --session "YOUR-SESSION-ID" `
  --allow-api
```

These are live-call examples, not startup commands. Hybrid analysis requires an explicit session and rejects `--force`. If a saved map already exists, hybrid mode reuses it and performs only the assignment step; newly collected messages are not silently reanalysed. Missing permission/key or an invalid confidence threshold fails before any Codex call or database creation.

- Decisions receives topic titles/descriptions/ancestor paths and item titles/summaries/classifications/evidence quotes. It does not receive the complete raw transcript or original IDs as identifiers; the request uses temporary aliases.
- The classifier chooses the same task/object/goal, rather than incidental matching keywords. It can return `review` or `new_topic` instead of forcing a match. `new_topic` is a pending suggestion, not an automatic call to generate another topic.
- `--topic-confidence 0.85` is the default experimental acceptance threshold, not an accuracy guarantee. Refusals, uncertain results and low confidence remain unassigned. Calibrate the threshold with reviewed examples before relying on automatic assignment.
- The current implementation allows up to 253 candidate topics (plus the two fallback choices) and sends 20 item questions per request. More topics fail before transmission instead of silently dropping candidates.
- Successful per-item decisions are cached under `decisions-cache/` next to the DB. The cache retains normalized choices/confidence and hashes, not request bodies, raw provider responses or API keys. It survives a later batch failure; unchanged items avoid repeat requests. There are no automatic provider retries.
- Results go to a separate `topic_assignments` sidecar table. Original `session_maps`, messages, evidence, node IDs and relations stay unchanged. The viewer applies assignments only to copied hierarchy metadata, shows pending counts, and ignores stale/invalid results. Original Conversation Map exports retain their original hierarchy.
- New analysis + assignments commit together only after successful classification; an API/validation/save failure does not publish a half-finished result. A concurrent source-map change prevents applying outdated assignments.

A viewer started from this version refreshes its derived topic view when the sidecar changes. Older running viewer processes must be restarted to load the new code. Real API/model calls are excluded from automated tests; transport and Codex responses are mocked.

Newly generated analyses request Korean titles, summaries, details, classification rationales, relationship notes, and analyst notes. Proper names and technical terms may keep their original spelling. Source messages, evidence quotes, explicitly stated owners/dates, identifiers, JSON field names, and enum values retain their original form.

The Korean output policy has a separate draft cache version so a new analysis does not reuse older drafts whose language was unspecified. Existing completed analyses still skip when unchanged, and old cache files are preserved. Updating the code alone does not run a model or translate previously imported analyses.

Manual import of an upstream-validated map:

```powershell
python .\context_graph.py status
python .\context_graph.py export-transcript --session <SESSION_ID> --output .\local-data\transcript.json
# Analyze with Conversation Map and obtain its validated map.json
python .\context_graph.py import-map --session <SESSION_ID> --file .\local-data\map.json
python .\context_graph.py graph --html .\output\graph.html
```

## Korean display labels (no model calls)

The live **대화 탐색** screen offers separate **세션 목록** and **마인드맵** views. The full-width list keeps request previews and recent-activity ordering. Choosing a session opens its four-column mind map: session → topic → question → answer, with the original connecting lines and branch animation. The map's session column remains visible for choosing another conversation. Switching views preserves the selected branch, search/project filters and all four pages; the last view is restored on reload. Stage buttons move within the map, while **세션 목록** or **넓은 목록** opens the full-width list. Smaller screens stack columns without horizontal overflow. Long help and collection statistics remain available in expandable controls. The **화면** selector offers paper, dark and system themes and remembers the choice in this browser; theme changes apply to the source reader too. All styles and fonts work offline. These presentation preferences are separate from saved display names and never change analysis or conversation data.

The built-in graph uses Korean classification, status, relationship, and evidence labels. JSON values such as `decision`, message IDs, node titles, summaries, and source quotes remain unchanged.

Inside **대화 탐색**, **세션 목록**, **마인드맵** and **시간순 질문·답변** are the primary views. Expand **보조 보기** for **전체 분석 항목** and **관계 그림**; relationship viewing still requires a selected analysis item. The disclosure closes after choosing a view or pressing Escape, and its label identifies the active auxiliary view. This grouping does not change the underlying views, saved selection or data.

The four-column map highlights the selected path while keeping other branches readable. Column and answer **⋯** menus retain name editing, restoration and auxiliary-topic controls. **이전 질문 / 다음 질문** walks the selected topic's stored order across pages; questions whose position is unconfirmed remain explicitly labelled. Selecting a confirmed question or answer opens a bottom source panel with its exact original message and surrounding dialogue. **앞선 대화 / 다음 대화** reads adjacent pages without replacing the selected map branch, and **전체 원문에서 열기** retains the full reader. Missing evidence never causes an adjacent answer or source position to be guessed.

**원문 검색** searches all collected conversations through the existing local, read-only search endpoint. Results offer source reading and each verified question path separately; multiple matches are choices, not an automatic assignment. The search response's `analysisNodeIds` contains only source item IDs with rechecked evidence on that exact message. Unanalysed results remain readable without generating a graph. **즐겨찾기·최근 본** and **이어서 읽기** retain up to 200 favorite session IDs, 20 recent session IDs and 100 session/message reading positions in this browser's local storage. They do not store source text, titles or credentials, do not change display-name settings and are not synchronized across devices. Offline HTML keeps graph browsing and available local navigation settings; source reading and full-text search require the running viewer. No model or external analysis API is called by these controls.

The analysis page initially opens **세션 목록**, or restores the last-used view. Choose a collected session to open its **마인드맵**, then a topic and a question to expand saved answers. List rows show the saved display name, project, recent activity, message count and first/latest request previews; the map uses compact session cards. Sessions sort by latest activity regardless of analysis status; unknown times come last and ties keep the catalogue order. Lists page eight entries at a time, and session search matches titles, projects, IDs and request previews. Selecting another session clears the previous topic/question branch; selecting the same session retains it. Question selection uses the complete source occurrence (session ID, original source-node ID and graph-node ID), preserving distinct occurrences of merged nodes. The original-conversation button opens the verified message and surrounding dialogue; returning restores the branch, page and list/map view. Unanalysed sessions remain available with a link to their raw dialogue. No model is called and no graph nodes or relations are generated by the viewer. Add `&mode=mind` to a `?view=graph` URL to enter session exploration directly.

Request previews are bounded 180-character display excerpts of stored user messages, not generated summaries. Known injected context wrappers are omitted only from the display; original content and message IDs remain untouched. Fork/helper previews require the exact metadata-verified post-boundary source record; inherited or unverifiable requests are never used as that session's own request. Missing previews are explicitly labelled. Preview text is rendered literally, cached until source/metadata changes, and excluded from offline HTML and exported question metadata. It is available only through the existing protected live reader. Saved analysis titles and custom display names keep their existing priority.

On Python 3.14+, primary-session previews can also read an exact `.jsonl.zst` sibling when a known rollout path has been archived and compressed. The reader verifies the session header, explicit fork boundary and each collected message's ID, uncompressed byte offset, role, timestamp and content. It streams the archive in memory without extracting, importing messages or changing titles, groups or display preferences. Archive changes invalidate cached previews. Corrupt, mismatched or unverifiable records remain unavailable; older Python versions retain the existing uncompressed behavior without installing dependencies. This fallback affects request previews only.

Topics in the mind map and the Q&A topic selector use the earliest verified original user-question position, including questions in descendant topics. Cards show **첫 질문 원문 #번호**. If no question position is confirmed, existing verified user evidence on another item in the same stored topic can supplement its order, labelled **사용자 근거 원문 #번호** with an exact original-message link. This does not create a question or change an item's classification. Context-only environment/browser records, assistant evidence, ambiguous membership and missing eligibility metadata cannot supply this fallback. Topics without either source show **순서 미확정** after the confirmed topics. Equal positions and unconfirmed topics retain their saved relative order. Missing timestamps do not discard a verified original-message position. This is display ordering only: topic membership, source messages, evidence, IDs and relationships remain unchanged.

Explicitly reviewed topics can be kept under **보조 정보** instead of the default topic list. Select a topic and use **보조 정보로 이동** or **기본 목록으로 복원** in the mind map or Q&A view. A successful save selects its destination group/page and survives reloads. The auxiliary group opens the existing analysis items, evidence and relations. Preferences live beside the database in `<database-stem>.topic-display.json` with `schemaVersion: "topic-display-1"` and a `sessions` object; each session entry contains the exact `sourceMapSha256`, `auxiliaryTopicIds` and optional `primaryTopicIds` for explicit restorations inside auxiliary parents. They apply only to that saved map and those IDs, never by title matching. Missing, invalid or outdated preferences leave the original topics visible. The database, source map, topic membership and graph identities are unchanged; new live exports carry only the derived display grouping.

Display edits are explicit same-origin JSON requests to the running viewer. They update only this local preferences file, using a cross-process lock and atomic replacement. The source-map hash and preference revision prevent stale pages from overwriting newer settings; reload after a conflict. Other sessions' preferences are retained. A freshly reviewed edit after a new analysis starts preferences for that source map without carrying forward outdated grouping. Offline snapshots have disabled edit controls. No model or external classification API is involved.

Open **표시 이름 수정** for a selected topic to enter a name and press **이름 저장**, or use **원래 이름으로 복원**. The original analysis name is shown beside the editor and is never overwritten. A saved name changes only that topic's labels and breadcrumb text in the mind map and Q&A view; IDs, chronology, question selection, evidence and relations stay intact. The optional `displayNames` object in the same sidecar maps existing topic IDs to single-line names of 1–120 characters. Group moves preserve these names, and restoring a name leaves its group unchanged. Draft input survives catalogue polling, while only an explicit save persists across reloads.

Select a session and open **세션 표시 이름 수정** to save or restore its display name, including sessions that have not been analysed. The optional top-level `sessionNames` object in the same preferences file maps collected session IDs to aliases. These names survive appended messages and analysis updates; restoring shows the current original title from the existing title rules. The original session header `title`, native Codex metadata and saved analysis titles are never rewritten. The mind map, Q&A selector, source timeline, helper-parent labels and search-result labels use the alias, while session filtering still matches the original title too. Renaming retains the selected topic/question and pages, and an older in-flight catalogue response cannot undo a successful save. This endpoint uses the same identity, same-origin, revision and atomic-write protections as topic edits and never invokes a model.

Questions and answers have **표시 제목 수정** controls, with **제목 저장** and **원래 제목으로 복원** in a focused dialog. Titles are scoped to the original session and source item ID, so repeated questions and merged graph nodes keep their distinct occurrences. `itemTitles` in that session's preferences stores analysis-item aliases. Saved answer-evidence cards use a separate `evidenceTitles` mapping keyed by question source ID and immutable collected message ID; saving rechecks that the message is an existing verified assistant quotation on that question. These cards retain their original-message number, quotes and navigation even when renamed. No node, relationship, classification or original analysis title is rewritten. The graph payload stays unchanged, and live/offline presentation metadata carries the labels. Original titles remain searchable; ambiguous merged overview labels retain the canonical graph title rather than applying one session's alias to another.

While the live page is open, its session catalogue refreshes every five seconds after the preceding request completes. Newly collected sessions appear without reloading the graph or re-fetching complete conversations; existing topic, question and answer branches remain in place. The selected session/topic/question, all four column pages, topic group, and session search/project filters survive a trip to the original conversation. Restoration waits for the complete catalogue and analysis context, so later pages are not reset against an incomplete startup list. A failed refresh keeps the last good list and retries. Offline HTML does not poll.

The session catalogue starts with **일반 대화**. Choose **보조 작업** to browse helper sessions grouped under their nearest ordinary conversation, or **분류 미확정** for records whose origin cannot be checked. The same controls are available in the original-message timeline. These categories come from existing Codex session metadata, not a model or the wording of the conversation. Scope, parent group, selection and pages survive navigation; direct original-message links and full-text search can still open helper sessions. No stored session is deleted or merged by these filters.

New branches animate their connecting lines first, then gently reveal child nodes in sequence. Only the changed column animates; existing branches, detail views and resize redraws stay still. Keyboard focus remains on the selected node or paging control. The viewer respects the operating system's reduced-motion preference and provides an **애니메이션 줄이기** checkbox for the current browser session. These effects change presentation only and make no model or Decisions API calls.

The **시간순 질문·답변** view keeps the separately labelled session and topic selectors. A **session** is one chat history; a **topic** is a saved content group within it. Select a collected session, then all topics, a specific topic or **주제 미확정**. Questions and existing linked answers are grouped by their nearest stored topic and ordered within each group by rechecked user-message evidence; selecting a parent topic includes its descendants. Topic membership uses saved source IDs, `parentId` and `childIds`, never title similarity or a new model inference. Missing, ambiguous or cyclic membership remains unassigned. Identically named topics and merged nodes from different sessions retain separate identities. Sessions without analysis show an explicit waiting state and a link to their original messages.

Clicking a confirmed question opens its original message with surrounding conversation and paging. Returning restores the selected session, topic and question position. Search matches question and linked-answer text within the selected session/topic. Ties within a message, missing order and missing answer links remain explicit. Only existing `answers` relationships are paired, without guessing from adjacency or classification. Original graph data, source messages, evidence and identities remain unchanged. Live views recheck anchors through the read-only timeline API; newly exported live HTML also embeds ordering and topic hierarchy metadata for offline reading. New messages still need an explicit user-requested analysis before they appear as new analysed Q&A. The main navigation contains only **대화 탐색** and **원문·검색**. Saved Conversation Map snapshots and existing `?view=map` links remain available for compatibility, without a **주제 연결 지도** menu button.

An answered question can contain assistant quotations in its own evidence without a separate `answers` edge. The **답변 확인** column and chronological Q&A view show these as separate **저장된 답변 근거** cards, with a quoted excerpt and exact original-message navigation. Each quote is independently rechecked against the full source message, role and stored evidence identity before display. Quotes from the same original message share a card; existing linked answers are not duplicated. Evidence connections use dashed lines and do not create graph nodes or relations. Open questions and neighboring assistant messages are not inferred answers. Offline metadata retains only evidence positions/counts; quote previews and live original navigation require the running viewer.

**전체 분석 항목** retains the readable single-column list (20 items per page), full summaries and collapsed evidence. **관계 그림** explicitly opens the optional directed graph, paged eight neighbours at a time. The content width is limited for wide screens; narrow screens open the reader over the list. Keyboard selection is supported for question cards and graph nodes.

When served through the live dashboard, **원문 대화 열기** resolves the saved source node to a verified message anchor and opens that message in the timeline with surrounding conversation. Uncertain anchors are not guessed. Offline HTML still shows its embedded evidence without a server. Graph display changes never modify the embedded graph JSON, source messages, evidence, IDs or relationships and do not run a model.

To translate an existing Conversation Map HTML's menus, buttons, classification labels, tooltips, and static fallback:

```powershell
python .\localize_map.py --input .\local-data\map.html --output .\output\map.ko.html
Start-Process .\output\map.ko.html
```

This writes a separate offline HTML, preserves the embedded analysis JSON exactly, and recalculates the viewer's Content Security Policy hashes. It does not call a model, modify a database, or change the separate Conversation Map checkout.

On desktop, the localized viewer fills the available window height so selecting or filtering entries does not shrink the map. Narrow screens retain the original tabbed layout.

The **질문 순서** button opens a chronological question list. Questions with verified user evidence are ordered by their earliest original user-message position, with times shown in the viewing device's time zone. Questions without a verified user position appear separately as **순서 미확정**. Questions grounded in the same source message are marked as tied. Selecting a title opens the existing question details; source data, evidence, IDs, and relationships remain unchanged. This view uses only the existing embedded data and makes no model calls.

## Windows character encoding (CP949)

If a long conversation contains Unicode characters that Windows' legacy CP949 code page cannot encode, older versions may fail with `'cp949' codec can't encode character` during `codex exec`. This version explicitly uses UTF-8 for all Codex and Node.js subprocess pipes (stdin/stdout/stderr). Upgrade to the latest `feat/context-graph-mvp` branch before retrying:

```powershell
git fetch origin
git switch feat/context-graph-mvp
git pull --ff-only origin feat/context-graph-mvp
python -m unittest discover -s tests -v
```

For an **older checkout** that has not yet been updated, `python -X utf8 .\\context_graph.py analyze ...` is a temporary workaround. Changing the terminal code page with `chcp` alone is not a reliable fix for Python subprocess pipes.

## Long-session analysis (automatic chunking)

Sessions longer than 60,000 serialized characters are automatically split into sequential chunks before model analysis. You do **not** have to edit the session file, truncate the conversation, or raise the context limit.

- Each part is sent to Codex separately, so long sessions incur multiple sequential model calls and can take longer.
- Chunks preserve original message IDs. If one individual message exceeds the limit, it is divided into text fragments without losing characters.
- All generated drafts are checked and built against the **full, original** transcript, retaining quote traceability.
- To prevent accidental ID collisions, each chunk's analysis keys are namespaced before merging. Same-title topics can still be deduplicated by Conversation Map.
- If any chunk or evidence validation fails, this run does **not** replace an existing stored graph. The next run retries the whole session.
- Each chunk is interpreted independently; cross-chunk supersessions and semantic relationships may be missed. This is not yet a fully reconciled long-term memory graph.

Example for a long session:

```powershell
python .\context_graph.py analyze `
  --session "YOUR-SESSION-ID" `
  --cm-root "$HOME\src\conversation-map" `
  --allow-model
```

Progress output prints `chunk 1/N`, `chunk 2/N`, and so on. A 184,408-character transcript will generally require at least four chunks depending on message boundaries. The run still requires user opt-in to send the conversation to the configured Codex model.

## Validation recovery and retry cache

The Conversation Map validator checks actual quoted evidence, including the speaker. If the Codex model incorrectly classifies a user quote as `assistant_statement`, the tool now reads the validator's **structured diagnostics**, corrects only reported `attribution-mismatch` classifications (including mixed-speaker `unknown`), and re-runs the full validator. Other errors still stop the import. It does not use `--best-effort` to ignore validation failures.

Both analysis modes omit raw Codex/validator output from failure logs. Logs retain the failed stage, exit status or timeout, and known validation error codes/counts. Diagnostic messages, source-node names and unknown error codes are not echoed because they may contain conversation text or credentials. Stored transcripts, evidence and local analysis caches remain verbatim.

Before validation, the adapter removes inapplicable `supersededBy` fields from non-decision items, following Conversation Map's handling of that field. It preserves the original classification, evidence, and explicit relations. A current decision that names a replacement still reaches the validator for consistency checking.

Each successfully generated **model draft** is also cached locally before validation. On failure or interruption after generating a chunk, unchanged chunks can be reused without additional Codex model calls. Every cached draft is still checked against the full source transcript before being saved as a final map.

- Default cache location: `$HOME\.codex-context-graph\analysis-cache\<hashed-session-id>\`
- With `--db .\local-data\test.db`, the cache is under `.\local-data\analysis-cache\`.
- Cached files contain **raw conversation quotes** and must be treated as sensitive data; they are ignored by Git. Do not share or commit them.
- `--force` bypasses both successful-run and draft cache skipping, requesting fresh model analysis.
- Cache keys account for the transcript chunk and current analysis schema; changed chunks are re-analyzed.
- A previously failed run on an **older version** had no persistent draft cache, so its first retry after upgrading must call Codex for all chunks once more.

```powershell
git fetch origin
git switch feat/context-graph-mvp
git pull --ff-only origin feat/context-graph-mvp
python -m unittest discover -s tests -v

python -X utf8 .\context_graph.py analyze `
  --session "YOUR-SESSION-ID" `
  --cm-root "$HOME\src\conversation-map" `
  --allow-model
```

Note: Repeated validation failure may indicate other issues (unsupported quote, invalid relationship, changed source) that should **not** be auto-suppressed. In that case, report the newly displayed error codes.

## Continuous collection

```powershell
python .\context_graph.py watch --interval 10
# Automatic collection plus a local, automatically updating HTML dashboard:
python .\context_graph.py watch --interval 10 --live-dir "$HOME\.codex-context-graph\live" --port 8765
Start-Process "http://127.0.0.1:8765/"
```

`watch` never calls a model. The old `watch --analyze` flags are rejected. Run `analyze --allow-model` separately only when you explicitly want semantic analysis.

New messages are deduplicated into SQLite and counted as awaiting analysis. The live page polls aggregate collection status; incoming raw messages do not reload or alter the displayed analysis. Explicitly imported/analyzed results update the graph, preserving existing projection IDs through source-node provenance. Refreshes use atomic file replacement and leave collected messages, stored maps, source quotes, and analysis caches alone.

The transcript view treats an `event_msg` and a `response_item` as mirrors only when they are adjacent in the same source file, have identical roles/text, have timezone-aware timestamps within one second in file order, and uniquely match each other. It keeps the `response_item` ID for a confirmed pair. Later repetitions, intervening conversation, missing times and ambiguous pairs remain visible; no raw message row is deleted. Graph projection likewise keeps distinct source IDs within each session. If an older projection merged them, one deterministic source retains the existing graph ID and the others receive separate stable IDs, restoring their explicit relations without rewriting the saved map.

Use a new, empty directory for `--live-dir`; existing files are not overwritten unless the directory was already created for this database. The server binds only to `127.0.0.1` and serves its dashboard HTML, aggregate status, read-only timeline queries and explicit local display-preference edits. It has no model/analysis HTTP endpoint. `--port 0` chooses an available port and prints the URL. Stop the watcher with Ctrl+C. No Windows service or startup task is installed.

To retain IDs from an existing exported graph and show a preserved Conversation Map alongside it:

```powershell
python .\context_graph.py --db .\local-data\test.db watch --interval 10 `
  --live-dir .\local-data\live --port 8765 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

Snapshots are read-only inputs. The optional Conversation Map retains its original analysis, question timeline, evidence, IDs and relationships. If its source HTML is explicitly regenerated later, the watcher copies that update. It does not generate new question nodes from unanalysed messages. Existing offline HTML files remain usable snapshots; automatic status updates are available at the printed local address while the watcher is running.

### Session and message timeline with existing analyses

Use **전체 원문 검색** to search the literal text of collected messages, including sessions without analyses. Korean fragments, one-character terms, punctuation and case-insensitive English are supported. Narrow by the selected project, all/current session, or speaker. Results show matching excerpts with highlights and are paged 30 messages at a time. Selecting a result opens a window centred on the original message, expands its full text and highlights the match; previous/next message pages let you keep reading the surrounding conversation. **검색 결과 다시 보기** returns to the same results. Search is requested explicitly and does not run models or change stored data. Terms are sent in a bounded POST body rather than URLs/access logs. The initial implementation scans deduplicated text read-only, without creating a search index in the database.

Open **세션 · 메시지 타임라인** in the live dashboard. Select a session, search its title/project/ID, filter by project or show only sessions with saved analyses. The timeline displays collected user/assistant messages in the same order as transcript export, with original text, timestamps and stable collection IDs. Missing times are labelled rather than invented. Messages are paged in groups of 100; use the latest-message button or toggle automatic following while reading older pages.

Session cards keep meaningful saved analysis titles. Ordinary non-fork conversations can use an existing Codex title or up to 80 characters of their first usable user request. Forks and helper sessions use only a user request verified against the exact JSONL record after an explicit history boundary, marked **분기 후 요청**. Known environment/AGENTS/browser/page-context wrappers are skipped for display only. If a source file or verifiable request is unavailable (including encrypted helper task messages), the UI shows the recorded task name when available and **고유 요청 확인 불가**, rather than reusing inherited parent dialogue. These are deterministic display labels, not model-generated summaries; original messages, saved analyses and IDs are never rewritten. Titles can be searched, and the session ID remains in the card tooltip.

Lineage is read from the configured `--codex-home` (before `serve` or `watch`), using its existing `state_*.sqlite` and verified JSONL metadata. Missing/unsupported metadata remains unclassified. Reads are cached and read-only; this does not create or modify Codex metadata, collected rows or graph relations.

Request excerpts are served through the live session catalogue. Offline question-order metadata keeps its previous non-source fallback title instead of embedding new conversation excerpts; saved analysis titles remain intact.

Existing decisions, questions, actions and other analysis items appear beside their verified source messages. Selecting one shows its original summary, rationale, evidence and relationships. The reader rechecks the complete source message and literal quote before displaying a connection. Positional analysis IDs are not assumed to identify the same live message after an earlier message is inserted. Ambiguous or unsupported anchors remain under **시점 미확정 분석**, with their stored evidence and relationships intact.

Use **정리된 내용** to browse all saved analysis items in a session, including items outside the current message page. Filter cards by decisions, questions, actions, proposals or other classifications. Cards show titles, summary previews and source-message positions; the inspector shows the complete saved details and evidence. Category colours supplement the text labels. The inspector opens when an item is selected and can be closed to widen the conversation. **글자 크기** offers 17/19/21px reading sizes, and the session list can be hidden for more reading space. The font preference is stored locally in the browser; these display settings do not change source or analysis data.

New messages update automatically, but new interpretations are produced only by an explicit `analyze --allow-model` request. Imported analyses update the timeline as well, including changes to rationales that do not alter graph geometry. A transcript/prefix hash establishes the saved analysis range; otherwise the range is marked uncertain. These views do not write to the database or rewrite any saved analysis, source text, evidence, IDs or relationships.

The timeline serves the selected session's original text on demand through loopback-only, read-only JSON endpoints. Original conversation text is never embedded in `status.json` or request logs. The page requires the running local server; the existing graph and Conversation Map snapshots remain available offline.

If a collector is already running, open a separate read-only viewer without restarting it or creating a second collector:

```powershell
python .\context_graph.py --db .\local-data\test.db serve --interval 10 `
  --live-dir .\local-data\timeline-view --port 8766 `
  --graph-snapshot .\local-data\graph.ko.html `
  --map-snapshot .\local-data\map.ko.html
```

`serve` opens an existing SQLite database in read-only/query-only mode. It does not collect files, update the database, or run models. New messages and explicitly saved analyses become visible as the existing collector and manual analysis commands update that database. Without a running collector, it continues to show the last stored data.

### Private Tailscale viewer

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

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Architecture

```text
Codex rollout JSONL -> read-only scanner -> SQLite
                                         -> session transcript
                                         -> Codex analyzer (explicit opt-in)
                                         -> Conversation Map validation/build
                                         -> map.json imports
                                         -> conservative cross-session merge
                                         -> offline HTML / JSON graph
```

## Limitations

- Experimental local JSONL parsing; Codex may change its event schema.
- Every model request is limited to roughly 60,000 serialized characters; longer sessions are split automatically. Cross-chunk conflict/decision reconciliation is not yet implemented.
- No semantic entity resolution, inference of cross-session contradictions, or branch-specific canonical decisions.
- No MCP server or automatic Windows background service yet.
- Latest import per session replaces the previous map. Cross-session node titles must match exactly after normalization to merge; ambiguous title matches stay separate, and existing source bindings take precedence.
- Imported maps receive basic structural validation; upstream Conversation Map's `check` and `build` must be used to establish evidence validation.
- Session files and resulting maps can contain sensitive conversation material; stored content is not redacted. Analysis failure logs omit raw provider/validator diagnostics.

License: MIT. Conversation Map is a separate project and is not redistributed here.
