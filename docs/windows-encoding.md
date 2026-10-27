# Windows character encoding (CP949)

Back to [README](../README.md)

If a long conversation contains Unicode characters that Windows' legacy CP949 code page cannot encode, older versions may fail with `'cp949' codec can't encode character` during `codex exec`. This version explicitly uses UTF-8 for all Codex and Node.js subprocess pipes (stdin/stdout/stderr). Upgrade to the latest `main` branch before retrying:

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
python -m unittest discover -s tests -v
```

For an **older checkout** that has not yet been updated, `python -X utf8 .\context_graph.py analyze ...` is a temporary workaround. Changing the terminal code page with `chcp` alone is not a reliable fix for Python subprocess pipes.

The analysis commands themselves are described in [analysis.md](analysis.md).
