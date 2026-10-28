# Korean display labels (no model calls)

Back to [README](../README.md)

All of the controls described here are presentation only. They never call a model or an external classification API, and they never change analysis or conversation data. The analysis-request dialogs that do start model work are described in [analysis.md](analysis.md).

## 세션 탐색: the mind map

The live dashboard opens straight into the mind map, rooted at **모든 프로젝트** (see below). Choosing a session draws a tree that grows from a single root at the left: the session, then its topics as numbered branches (1, 2, 3 …), each topic's questions (1.1, 1.2 …) and each question's answers as leaves. Topics are always visible; a topic opens its questions when clicked and a question opens its answers, one branch at a time, and clicking an open branch folds it again. A collapsed branch shows how many children it holds. A session without analysis grows session → request → response instead, with requests gathered ten at a time into **요청 1–10** style branches when there are more than ten.

The project filter above the map doubles as the root choice. A fresh page opens the **모든 프로젝트** tree: projects are numbered branches in recent order, a project opens to its sessions, a session to its topics as leaves. **이 프로젝트만 보기** (or a double-click on the project branch) makes that project the root; clicking the project root, or the **모든 프로젝트** crumb, climbs back with the same branch open. Helper sessions (보조 작업) hang under the conversation that started them as a folded **보조 작업 N** branch, which opens in the project tree; the 세션 종류 selector still lists them flat when chosen. With a project chosen, that project is the root and its sessions are numbered branches in recent order (a folded session shows its topic count, or **대기** before analysis); a session branch opens its topics, a topic its questions, and questions are leaves there because the tree stops four levels down. **이 세션만 보기** below the tree, or a double-click on the session branch, makes that session the root so its answers open; clicking the session root, or its project crumb in the breadcrumb, climbs back to the project tree with the same branch still open. **모든 프로젝트** returns to the whole-catalogue tree with the open session's project branch still open. There is no separate session list: the chosen session's provider, project, last activity, message count, title source and its first/latest requests appear below the tree, and each request preview opens the reader at that message.

**기간** beside **프로젝트** in the toolbar grows the tree from time instead: weeks (Monday to Sunday, most recent first, by last activity) open to days, days to sessions as leaves, and sessions without a usable time gather under **날짜 미확정**. The project filter narrows the period tree rather than replacing it. **이 세션만 보기** from a day makes that session the root, and climbing back returns to its day.

The session catalogue carries a per-session topic summary (identities and display titles only), so folded session branches show their topic counts and answer searches before any analysis context is read; the open session's context is read first and the rest follow in the background.

The tree sits in a fixed-height box. Drag the background to pan, use Ctrl+wheel or a two-finger pinch to zoom, and the **−**, **+**, **100%** and **전체 보기** buttons in its corner to step the scale or fit the whole tree; a plain wheel pans. Opening a session fits the tree around its root, opening a branch pans just enough to show it, and opening the reader panel keeps the selected branch in view. Keyboard: ↑ and ↓ move between visible branches, → opens a branch or steps into it, ← steps back to the parent or folds an open branch, Home and End jump to the root and the last branch. The search box lights the branches whose labels contain the term (a folded branch counts the sessions, requests and topics hidden inside it), dims the rest, marks folded branches with **검색 N**, and reports the total beside the breadcrumb; the header count shows how many sessions match.

The built-in graph uses Korean classification, status, relationship, and evidence labels. JSON values such as `decision`, message IDs, node titles, summaries, and source quotes remain unchanged.

Labels rest on their branch lines; the selected path is drawn in the accent colour while other branches stay readable. Hovering a label shows its full title and the ordering note (first question position, user-evidence position or 순서 미확정). The **세션 관리 ⋯**, **주제 관리 ⋯**, **질문 관리 ⋯** and **답변 관리 ⋯** menus in the toolbar above the tree hold name editing, restoration and auxiliary-topic controls for whichever branch is selected. **←** and **→** walk the open topic's questions in stored order. Selecting a confirmed question or answer opens the reader panel at that exact original message; **질문·근거 보기** opens the detail panel with the saved summary, evidence and relationships. Question branches also include items the user asked as a question even when the analysis filed them as 미분류, 추론 or 제안: any item outside the decision and action classes whose verified user quote ends in a question mark or a Korean interrogative ending. Hovering such a branch shows **사용자가 물음으로 적은 항목** with the stored class, and its verified assistant quotes appear as **어시스턴트 원문 #번호** leaves.

A session without analysis shows a one-line **원문 순서 묶음** notice with **이 세션 분석** and **원문 읽기** buttons. Its request and response branches come from the stored chronology only; no topic or answer meaning is inferred.

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

While the live page is open, its session catalogue refreshes every five seconds after the preceding request completes. Newly collected sessions appear without reloading the graph or re-fetching complete conversations; existing topic, question and answer branches remain in place. The selected session, topic, question and answer, the session-list page, the open auxiliary group, and session search/project filters survive a transparent refresh and a reload.

The session catalogue starts with **일반 세션**. Choose **보조 작업** to browse helper sessions grouped under their nearest ordinary conversation, or **분류 미확정** for records whose origin cannot be checked. These categories come from existing Codex session metadata, not a model or the wording of the conversation. Scope and parent choices are remembered by the browser.

A newly opened branch animates its connecting lines first, then gently reveals its child nodes in sequence. Only that branch animates; existing branches, detail views and resize redraws stay still, and a reveal survives the redraws that late session data triggers. Keyboard focus remains on the selected node. The viewer respects the operating system's reduced-motion preference and provides an **애니메이션 줄이기** checkbox for the current browser.

## Answer evidence

An answered question can contain assistant quotations in its own evidence without a separate `answers` edge. The tree shows these as separate leaves on a dotted branch; selecting one shows the quoted excerpt below the tree and opens the exact original message. Each quote is independently rechecked against the full source message, role and stored evidence identity before display. Offline HTML shows the reference without the quotation and without network requests.

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
