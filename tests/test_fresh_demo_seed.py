"""Exercise the destructive command only against disposable SQLite files."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def run_seed(target, *args, environment="development"):
    env = dict(
        os.environ,
        APP_ENV=environment,
        APP_DATABASE_URL=f"sqlite:///{target}",
        EMAIL_BACKEND="memory",
        PYTHONPATH=str(BACKEND),
    )
    return subprocess.run(
        [sys.executable, "-m", "app.dev_reset_seed", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("args,environment", [((), "development"), (("--replace-local",), "test")])
def test_reset_requires_explicit_flag_and_development_database(tmp_path, args, environment):
    target = tmp_path / "protected.db"
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE keep_me (value TEXT)")
        db.execute("INSERT INTO keep_me VALUES ('original')")
    before = target.read_bytes()
    result = run_seed(target, *args, environment=environment)
    assert result.returncode != 0
    assert (
        "Use --replace-local" in result.stderr
        if not args
        else "restricted to a development" in result.stderr
    )
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.bak"))


def test_replacement_is_fresh_backed_up_and_repeatable(tmp_path):
    target = tmp_path / "demo.db"
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE old_demo_marker (value TEXT)")
        db.execute("INSERT INTO old_demo_marker VALUES ('previous dataset')")
    first = run_seed(target, "--replace-local")
    assert first.returncode == 0, first.stdout + first.stderr
    manifest = json.loads((tmp_path / "demo_seed_manifest.json").read_text())
    with sqlite3.connect(manifest["backup"]) as backup:
        assert (
            backup.execute("SELECT value FROM old_demo_marker").fetchone()[0] == "previous dataset"
        )
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 25
        assert db.execute("SELECT count(*) FROM support_messages").fetchone()[0] >= 18
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='old_demo_marker'").fetchone()
            is None
        )
        ids = set(db.execute("SELECT id FROM events"))
    second = run_seed(target, "--replace-local")
    assert second.returncode == 0, second.stdout + second.stderr
    current = json.loads((tmp_path / "demo_seed_manifest.json").read_text())
    assert current["counts"] == manifest["counts"]
    assert len(list(tmp_path.glob("*.bak"))) == 2
    with sqlite3.connect(target) as db:
        assert not ids.intersection(db.execute("SELECT id FROM events"))
