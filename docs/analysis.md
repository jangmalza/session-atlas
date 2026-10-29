# Semantic analysis (opt-in)

Back to [README](../README.md)

Collection, automatic refresh and opening HTML never call a model or an API. Everything on this page starts only after an explicit request: `analyze --allow-model`, `--topic-classifier decisions --allow-api`, or the confirmed **이 세션 분석** job in the live viewer.

The CLI commands below are unchanged, but the pipeline code (Codex analysis, Conversation Map validation, chunking, draft cache and Decisions topic assignment) now lives in `analysis_pipeline.py`, split out of `context_graph.py`. `context_graph.py` remains the CLI entry point.

## Automatic semantic analysis from the live viewer

The live viewer has **분석 설정** in its header, a **분석** button beside every session branch that still awaits analysis, and **이 세션 분석** in the session menu. The dialog names the session (or **제목 없는 세션**), the task in plain words, the Codex model and reasoning effort it will run with, the message count with a rough duration, and exactly what leaves the PC; after **분석 시작** it shows a stage bar (대기 → 백업 → 분석 → 검증 → 정리 → 저장) with the elapsed time, then **완료** or the failed stage with its reason. Opening either dialog, saving settings, collecting new messages and refreshing pages never start model work. **분석 설정** can also name the **Codex 모델** passed to `codex exec` (`--model` on the CLI); leave it empty to use the model from the Codex config. Set it when the Codex default is not available to the signed-in account, which the job reports as `codex_exec_failed`. The dialog's **추론 강도** (`--reasoning` on the CLI) likewise overrides the Codex config's reasoning effort for analysis only; `medium` is a sensible choice, since a config set to `max` can push one chunk past the 30-minute limit, reported as `codex_timeout`. A timed-out Codex run is ended together with its child processes. The 30-minute limit is kept on the wall clock, so it also fires after the machine sleeps through a run, and a chunk whose Codex process writes nothing for 15 minutes (no output on its pipes, no change to the draft file) is ended the same way; both are reported as `codex_timeout`. Such a job can simply be started again: the drafts of chunks that completed before the stall are reused from the cache.

1. Open **분석 설정**. Check the installed Codex CLI's local authentication status and set the Conversation Map checkout folder. The check runs only [`codex login status`](https://learn.chatgpt.com/docs/cli/reference); its output is discarded. Sign in with `codex login` in the server PC's terminal if needed.
2. For Decisions classification, register a key in that dialog. On Windows it is stored as a database-scoped generic credential in the current user's [Windows credential set](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew). On macOS and Linux it is stored in the OS keychain (Keychain, GNOME Keyring, KWallet or another Secret Service provider) through the [`keyring`](https://pypi.org/project/keyring/) package; install it with `pip install keyring` (an editable install of this project pulls it in automatically). When no usable keychain is available the dialog says so, saving is disabled, and the server environment variable `OPENAI_API_KEY` is used instead. Keys are never returned to the browser or written to app files, browser storage, databases, backups or logs. Registration checks format only and does not call the API. Non-Windows hosts use `OPENAI_API_KEY`; web key storage is disabled there. Deleting a registered Windows key leaves a separately configured environment key intact and visibly identifies that remaining source.
3. Select a session, choose **이 세션 분석**, review the selected session, message count and transmission scope, then explicitly consent and run the selected operation. Codex-only, Codex followed by Decisions, and Decisions-only classification of an existing map are separate choices. Existing maps cannot be regenerated from this UI; their original nodes, quotes, IDs and relations stay intact. Newly appended messages in already analysed sessions remain awaiting analysis.

One background job runs per database, shared across local and Tailscale viewers. Progress shows backup, analysis/chunks, evidence validation, result building, topic classification and completion/failure. Closing the dialog or restarting a viewer does not restart the job. Failed/interrupted jobs require a new explicit request. Existing chunk/classification caches are reused; provider output and exceptions are not exposed. A job creates a verified private backup in `<database-stem>.analysis-control/<job-id>/before.zip`, works on a staging copy, and adopts only the selected session's validated result in a transaction. If that session's source/map/assignments change after confirmation, the result is not published. Display-name settings and unrelated sessions are never overwritten. A committed job receipt prevents a lost progress-file update from presenting a successful DB commit as a failure.

The same exact Host, Tailscale identity, same-origin JSON and action-header protections used for display edits protect these controls. The API key is used only in the analysis worker; a stored Decisions key is not passed to Codex. The CLI described below remains available. Actual authenticated model/API runs are separate from the mocked browser and worker regression tests.

## Codex analysis from the CLI

Requires Node.js 18+, installed/authenticated Codex CLI, and a local checkout of Conversation Map:

```powershell
git clone https://github.com/tonitalej/conversation-map.git "$HOME\src\conversation-map"
python .\context_graph.py scan
python .\context_graph.py analyze --cm-root "$HOME\src\conversation-map" --allow-model
python .\context_graph.py graph --html .\output\graph.html
```

**Privacy:** `--allow-model` sends the exported conversation text to the configured Codex model. The local collector alone does not make model calls. Avoid placing passwords, production credentials, or other secrets in exports. Never commit local databases, transcripts, or generated maps.

## Codex topics + Decisions assignment (explicit opt-in)

The hybrid mode uses Codex to create topic candidates, descriptions, items and evidence, then calls the [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions) to assign those items to existing topic IDs. Existing `analyze` commands remain Codex-only unless `--topic-classifier decisions` is selected. Collection, automatic refresh and opening HTML never call either model. The live viewer starts a separate analysis worker only after the explicit confirmation described above.

Set `OPENAI_API_KEY` through your environment/secret manager before requesting classification. Keep it out of command arguments, browser code, logs and Git. The implementation uses Python's standard library and the fixed `https://api.openai.com/v1/decisions` endpoint with `gpt-6-luna`; it does not install an SDK or read credentials during collection/viewing. The Decisions API became generally available on 2026-10-22; the request format in `decisions_topics.py` matches its documented choice-question shape, but live calls are not covered by automated tests.

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

### Korean analysis output

Newly generated analyses request Korean titles, summaries, details, classification rationales, relationship notes, and analyst notes. Proper names and technical terms may keep their original spelling. Source messages, evidence quotes, explicitly stated owners/dates, identifiers, JSON field names, and enum values retain their original form.

The Korean output policy has a separate draft cache version so a new analysis does not reuse older drafts whose language was unspecified. Existing completed analyses still skip when unchanged, and old cache files are preserved. Updating the code alone does not run a model or translate previously imported analyses.

## Manual import of an upstream-validated map

```powershell
python .\context_graph.py status
python .\context_graph.py export-transcript --session <SESSION_ID> --output .\local-data\transcript.json
# Analyze with Conversation Map and obtain its validated map.json
python .\context_graph.py import-map --session <SESSION_ID> --file .\local-data\map.json
python .\context_graph.py graph --html .\output\graph.html
```

Imported `map.json` evidence is merged across sessions by project + classification + exactly normalized title, only when the title match is unambiguous; distinct source nodes in one session remain separate. The latest import per session replaces the previous map. Imported maps receive basic structural validation; upstream Conversation Map's `check` and `build` must be used to establish evidence validation.

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

The Conversation Map validator checks actual quoted evidence, including the speaker. If the Codex model incorrectly classifies a user quote as `assistant_statement`, the tool reads the validator's **structured diagnostics**, corrects only reported `attribution-mismatch` classifications (including mixed-speaker `unknown`), and re-runs the full validator. Other errors still stop the import. It does not use `--best-effort` to ignore validation failures.

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
git switch main
git pull --ff-only origin main
python -m unittest discover -s tests -v

python -X utf8 .\context_graph.py analyze `
  --session "YOUR-SESSION-ID" `
  --cm-root "$HOME\src\conversation-map" `
  --allow-model
```

Note: Repeated validation failure may indicate other issues (unsupported quote, invalid relationship, changed source) that should **not** be auto-suppressed. In that case, report the newly displayed error codes.

See also [Windows character encoding (CP949)](windows-encoding.md) for the `-X utf8` workaround on older checkouts.
