import importlib.util
import json
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("acceptance_audit.py")
SPEC = importlib.util.spec_from_file_location("hormozi_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_sqlite_embedding_index_ready_requires_openai_metadata_and_chunks(tmp_path):
    index = tmp_path / "index.db"
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [("embedding_model", "openai/text-embedding-3-small"), ("embedding_dimension", "1536"), ("chunk_count", "12")],
        )
    assert AUDIT.sqlite_embedding_index_ready(index)


def test_sqlite_embedding_index_ready_reads_production_index_meta_schema(tmp_path):
    index = tmp_path / "production-index.db"
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO index_meta(key, value) VALUES (?, ?)",
            [
                ("embedding_model", "openai/text-embedding-3-small"),
                ("embedding_dimension", "1536"),
                ("chunk_count", "12"),
            ],
        )
    assert AUDIT.sqlite_embedding_index_ready(index)


def test_sqlite_embedding_index_ready_rejects_missing_or_wrong_metadata(tmp_path):
    assert not AUDIT.sqlite_embedding_index_ready(tmp_path / "missing.db")
    index = tmp_path / "wrong.db"
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO meta(key, value) VALUES ('embedding_model', 'gemini/embedding')")
        connection.execute("INSERT INTO meta(key, value) VALUES ('embedding_dimension', '3072')")
        connection.execute("INSERT INTO meta(key, value) VALUES ('chunk_count', '12')")
    assert not AUDIT.sqlite_embedding_index_ready(index)


def test_completion_matrix_preserves_pending_requirement_statuses(tmp_path):
    pack = tmp_path / "pack"
    (pack / "meta").mkdir(parents=True)
    matrix = AUDIT.write_completion_matrix(
        pack,
        {
            "inventory_ledger": {"status": "pass", "detail": "ok"},
            "supplied_transcripts": {"status": "pass", "detail": "ok"},
        },
    )
    assert matrix["overall_status"] == "pending_external_gates"
    assert "restricted_playbooks" in matrix["pending_requirements"]
    report = json.loads((pack / "meta" / "completion-matrix.json").read_text(encoding="utf-8"))
    assert report["overall_status"] == matrix["overall_status"]
    markdown = (pack / "meta" / "completion-matrix.md").read_text(encoding="utf-8")
    assert markdown.startswith("---\n")
    assert "id: alex-hormozi-brain/meta/completion-matrix" in markdown
