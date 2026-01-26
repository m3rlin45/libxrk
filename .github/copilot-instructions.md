# GitHub Copilot Instructions for libxrk

## Python Environment

**Always use `poetry` to run Python commands in this project.**

- Run Python scripts: `poetry run python ...`
- Install dependencies: `poetry install`
- Add dependencies: `poetry add <package>`
- Add dev dependencies: `poetry add --group dev <package>`

Do NOT use bare `python` or `python3` commands directly.

## Testing

### CPython Tests (fast, run frequently)

```bash
poetry run pytest tests/ -v
```

### Quality Checks (run before finishing work)

```bash
poetry run poe check
```

This runs:
- `black --check .` - Code formatting
- `mypy .` - Type checking  
- `pytest` - Tests

If formatting fails, run `poetry run black .` to fix it.

### Pyodide/WebAssembly Tests (slow, ~2-3 minutes)

```bash
poetry run poe pyodide-test
```

This builds the library for WebAssembly and runs tests in Pyodide. **This is a long-running command - do not interrupt it.** Run it as a background process if needed.

### Cython Rebuild

After modifying `aim_xrk.pyx`, rebuild the Cython extension:

```bash
poetry build
poetry install --only-root
```

## Before Finishing Work

**Always run `poetry run poe check` before completing any task.**