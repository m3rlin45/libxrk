# GitHub Copilot Instructions for libxrk

## Python Environment

**Always use `poetry` to run Python commands in this project.**

- Run tests: `poetry run pytest ...`
- Run Python scripts: `poetry run python ...`
- Install dependencies: `poetry install`
- Add dependencies: `poetry add <package>`
- Add dev dependencies: `poetry add --group dev <package>`

Do NOT use bare `python` or `python3` commands directly.

## Before Finishing Work

**Always run `poetry run poe check` before completing any task.**

This runs all quality checks:
- `black --check .` - Code formatting
- `mypy .` - Type checking
- `pytest` - Tests

If formatting fails, run `poetry run black .` to fix it.