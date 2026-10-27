# Testing

Back to [README](../README.md)

Real model/API calls are excluded from every automated test: Codex responses and the Decisions transport are mocked. Actual authenticated runs are separate from these regression tests.

## Python unit tests

```powershell
python -m unittest discover -s tests -v
```

## Node viewer tests

```powershell
node --test tests/qa_timeline.test.cjs tests/session_mindmap.test.cjs tests/mindmap_lifecycle.test.cjs tests/explorer_tools.test.cjs
```

These verify question ordering, answer links and the viewer lifecycle without a browser; CI runs the same command.

## Browser regression tests (Playwright E2E)

```powershell
npm ci
npx playwright install chromium
npm run test:e2e
```

E2E tests launch a separate Chromium, synthetic SQLite database and local Python server. They never open the user's browser profile, production database or real transcripts; application requests outside the fixture origin are blocked. They check the four-column map, original reader and position restoration, search, bookmarks, management menus, responsive layout, chronological turns and display edits without iframe reload. GitHub Actions runs browser tests on Windows and Ubuntu with Python 3.14; failed traces/screenshots contain only synthetic data. Set `E2E_PYTHON` when a specific Python executable is needed locally, for example to a Python 3.14 executable when `python` on `PATH` is not one.

## Continuous integration

The GitHub Actions workflow (`.github/workflows/mvp-tests.yml`) runs the Python unit tests and the Node viewer tests on Ubuntu and Windows with Python 3.13 and 3.14 and Node 22, and the browser E2E tests on both operating systems with Python 3.14.

## Formatting

```powershell
uvx ruff format <files>
```

The line length (120) is configured in `pyproject.toml`.

## Editable install

```powershell
uv pip install -e .
# or: pip install -e .
```

This provides the `session-atlas` and `session-atlas-runtime` console commands. A regular wheel is not supported because the modules load their HTML/CSS/JS assets from the same directory.
