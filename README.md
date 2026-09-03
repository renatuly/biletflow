# BiletFlow

Initial FastAPI backend foundation for the BiletFlow academic MVP.

## Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`, with interactive
documentation at `/docs`.

## Test

```bash
pytest
```

## Current scope

This first increment contains only application configuration, versioned API
routing, and health endpoints. Domain features will be added incrementally.
