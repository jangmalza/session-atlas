# Korean display labels (no model calls)

Back to [README](../README.md)

All of the controls described here are presentation only. They never call a model or an external classification API, and they never change analysis or conversation data. The analysis-request dialogs that do start model work are described in [analysis.md](analysis.md).

## 세션 탐색: the mind map

The live dashboard opens straight into the mind map. Its session column lists collected sessions with the saved display name, project, recent activity and message count; **세션 목록 펼치기** widens that column into a full list with first/latest request previews, and **마인드맵으로 돌아가기** folds it back. Choosing a session opens its columns: session → topic → question → answer for an analysed session, or session → request → response for one that has not been analysed yet. Column headings are numbered in both layouts, and on narrow screens the numbered stage bar above the map scrolls to each column.

The built-in graph uses Korean classification, status, relationship, and evidence labels. JSON values such as `decision`, message IDs, node titles, summaries, and source quotes remain unchanged.

The map highlights the selected path while keeping other branches readable. The **세션 관리 ⋯**, **주제 관리 ⋯** and **질문 관리 ⋯** menus hold name editing, restoration and auxiliary-topic controls. **←** and **→** in the question column walk the selected topic's stored order across pages; questions whose position is unconfirmed remain explicitly labelled. Selecting a confirmed question or answer opens the reader panel at that exact original message; **질문·근거 보기** opens the detail panel with the saved summary, evidence and relationships.

A session without analysis shows a one-line **원문 순서 묶음** notice with an **이 세션 분석** button. Its request and response columns come from the stored chronology only; no topic or answer meaning is inferred.

## 원문 패널: the reader panel

The reader panel at the bottom shows original messages with speaker, message number and time. **앞선 N개 / 다음 N개** page in units of 7 or 100 (**100개씩 보기**), **최신 메시지** jumps to the end, **새 메시지 따라가기** keeps following this session as it grows, **전체 높이** expands the panel over the map (Escape restores it) and **글자 크기** offers three sizes remembered by the browser. **이 세션의 원문 읽기** on a selected session opens the panel full height at the remembered reading position. **원문 패널 닫기** closes it.

## Search, favorites and reading positions

**원문 검색** searches all collected conversations through the existing local, read-only search endpoint. Results offer source reading and each verified question path separately; multiple matches are choices, not an automatic assignment. The search response's `analysisNodeIds` contains only source item IDs with rechecked evidence on that exact message. Unanalysed results remain readable without any analysis.

**즐겨찾기·최근 본** lists favourites and recently opened sessions; **☆ 즐겨찾기** toggles the selected session and **이어서 읽기** reopens the reader at the last message read in that session. These are stored only in the current browser and never written to the database.

## Session list behaviour and request previews

Sessions sort by latest activity regardless of analysis state. List rows and map cards show the saved display name, project, recent activity, message count and, in the expanded list, first/latest request previews.

Request previews are bounded 180-character display excerpts of stored user messages, not generated summaries. Known injected context wrappers are omitted only from the display; original content and message IDs remain untouched. Fork/helper previews require the exact metadata-verified post-boundary source record; inherited or unverifiable requests are never used as that session's own request. Missing previews are labelled rather than invented.

On Python 3.14+, primary-session previews can also read an exact `.jsonl.zst` sibling when a known rollout path has been archived and compressed. The reader verifies the session header, explicit fork boundary and each collected message's ID, uncompressed byte offset, role, timestamp and content. It streams the archive in memory without extracting, importing messages or changing titles, groups or display state.

## Topic ordering

Topics in the mind map use the earliest verified original user-question position, including questions in descendant topics. Cards show **첫 질문 원문 #번호**. If no question position is confirmed, existing verified user evidence on another item in the same stored topic can supplement its order, labelled **사용자 근거 원문 #번호** with an exact original-message link. Topics without either remain **순서 미확정** at the end.

## Auxiliary topics and display-name edits (sidecar file)

Explicitly reviewed topics can be kept under **보조 정보** instead of the default topic list. Select a topic and use **보조 정보로 이동** or **기본 목록으로 복원** in the topic menu. A successful save selects its destination group/page and survives reloads. The auxiliary group opens the existing analysis items, evidence and relations. Preferences live beside the database in `<database-stem>.topic-display.json`.

Display edits are explicit same-origin JSON requests to the running viewer. They update only this local preferences file, using a cross-process lock and atomic replacement. The source-map hash and preference revision prevent stale pages from overwriting newer settings; reload after a conflict. Other sessions' preferences are retained. A freshly reviewed edit after a new analysis starts preferences for that analysis from the saved defaults.

Open **표시 이름 수정** for a selected topic to enter a name and press **이름 저장**, or use **원래 이름으로 복원**. The original analysis name is shown beside the editor and is never overwritten. A saved name changes only that topic's labels and breadcrumb text; IDs, chronology, question selection, evidence and relations stay intact. The optional `displayNames` object in the preferences file maps topic IDs to aliases.

Select a session and open **세션 표시 이름 수정** to save or restore its display name, including sessions that have not been analysed. The optional top-level `sessionNames` object in the same preferences file maps collected session IDs to aliases. These names survive appended messages and analysis updates; restoring shows the current original title from the existing title rules. The original title is never overwritten.

Questions and answers have **표시 제목 수정** controls, with **제목 저장** and **원래 제목으로 복원** in a focused dialog. Titles are scoped to the original session and source item ID, so repeated questions and merged graph nodes keep their distinct occurrences. `itemTitles` in that session's preferences stores analysis-item aliases. Saved answer-evidence cards use a separate `evidenceTitles` map keyed by the exact evidence message.

## Catalogue refresh, session categories and animation

While the live page is open, its session catalogue refreshes every five seconds after the preceding request completes. Newly collected sessions appear without reloading the graph or re-fetching complete conversations; existing topic, question and answer branches remain in place. The selected session/topic/question, all column pages, topic group, and session search/project filters survive a transparent refresh and a reload.

The session catalogue starts with **일반 세션**. Choose **보조 작업** to browse helper sessions grouped under their nearest ordinary conversation, or **분류 미확정** for records whose origin cannot be checked. These categories come from existing Codex session metadata, not a model or the wording of the conversation. Scope and parent choices are remembered by the browser.

New branches animate their connecting lines first, then gently reveal child nodes in sequence. Only the changed column animates; existing branches, detail views and resize redraws stay still, and a reveal survives the redraws that list scrolling or late session data trigger. Keyboard focus remains on the selected node or paging control. The viewer respects the operating system's reduced-motion preference and provides an **애니메이션 줄이기** checkbox for the current browser.

## Answer evidence

An answered question can contain assistant quotations in its own evidence without a separate `answers` edge. The answer column shows these as separate **저장된 답변 근거** cards, with a quoted excerpt and exact original-message navigation. Each quote is independently rechecked against the full source message, role and stored evidence identity before display. Offline HTML shows the reference without the quotation and without network requests.

Graph display changes never modify the embedded graph JSON, source messages, evidence, IDs or relationships.

## Localizing a Conversation Map HTML (`localize_map.py`)

To translate an existing Conversation Map HTML's menus, buttons, classification labels, tooltips, and static fallback:

```powershell
python .\localize_map.py --input .\local-data\map.html --output .\output\map.ko.html
Start-Process .\output\map.ko.html
```

This writes a separate offline HTML, preserves the embedded analysis JSON exactly, and recalculates the viewer's Content Security Policy hashes. It does not call a model, modify a database, or change the separate Conversation Map checkout.

On desktop, the localized viewer fills the available window height so selecting or filtering entries does not shrink the map. Narrow screens retain the original tabbed layout.

The **질문 순서** button opens a chronological question list. Questions with verified user evidence are ordered by their earliest original user-message position, with times shown in the viewing device's time zone. Questions without a verified user position appear separately as **순서 미확정**. Questions grounded in the same source message are marked as tied. Selecting a title opens the existing entry in the map.
