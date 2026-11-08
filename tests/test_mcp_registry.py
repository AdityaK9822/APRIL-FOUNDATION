"""P8 wiring — MCP server registry, logic-level pass.

Pass 1 of the two-verification contract: direct method calls against a real
file on disk. Nothing here touches HTTP; the endpoint-level pass lives in
test_web_extensions.py (in-process ASGI) plus its --live guard (real TCP).

Run: .venv/bin/python -m pytest tests/test_mcp_registry.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mcp_registry import (
    DuplicateName,
    McpRegistry,
    RegistryError,
    UnknownServer,
    demo,
    slugify,
    validate_server,
)

ALWAYS_ERR = "registry record corrupt-free base"
VALID = {"name": "Local Shell", "transport": "stdio", "command": "python -m server"}
VALID_HTTP = {"name": "remote", "transport": "http", "url": "https://mcp.example/x"}


# --- pure validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        ({"name": "x", "transport": "tcp"}, "transport must be one of"),
        ({"name": "", "transport": "http", "url": "http://x"}, "name is required"),
        ({"name": "x", "transport": "stdio"}, "requires a command"),
        ({"name": "x", "transport": "http"}, "requires a url"),
        ({"name": "x", "transport": "http", "url": "ftp://y"}, "http:// or https://"),
        ({"name": "a/b", "transport": "stdio", "command": "run"}, "path separators"),
        ({"name": "x", "transport": "http", "url": "http://y", "wat": 1}, "unknown field"),
        (
            {"name": "x", "transport": "stdio", "command": "go", "args": ["ok", 3]},
            "list of strings",
        ),
        (
            {"name": "x", "transport": "stdio", "command": "go", "env": {"": "v"}},
            "non-empty string keys",
        ),
        (
            {"name": "x", "transport": "stdio", "command": "go", "env": {"A": 3}},
            "mapping of non-empty string keys",
        ),
        ({"name": "x", "transport": "http", "url": "http://y", "enabled": "yes"}, "boolean"),
    ],
)
def test_validate_server_refuses_loudly(record, fragment):
    errors = validate_server(record)
    assert errors, f"expected a refusal for {record}"
    assert any(fragment in e for e in errors), errors


def test_validate_server_accepts_both_transport_shapes():
    assert validate_server(dict(VALID)) == []
    assert validate_server(dict(VALID_HTTP)) == []


def test_slugify_rules():
    assert slugify("Local Shell") == "local-shell"
    assert slugify("My -- Tool!") == "my-tool"
    assert slugify("///") == ""
    assert slugify("a.b_c-d") == "a.b_c-d"


# --- CRUD against the file -------------------------------------------------


def _fresh(tmp_path: Path) -> McpRegistry:
    return McpRegistry(tmp_path / "mcp_servers.json")


def test_fresh_registry_is_empty(tmp_path):
    reg = _fresh(tmp_path)
    assert reg.list() == []
    # the file materialises on the first mutation, not on construction
    reg.add(name="bootstrap", transport="http", url="http://x")
    doc = json.loads((tmp_path / "mcp_servers.json").read_text())
    assert doc["version"] == 1 and len(doc["servers"]) == 1


def test_add_slugs_and_timestamps_and_defaults(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    assert rec["id"] == "local-shell"
    assert rec["enabled"] is True
    assert rec["created_at"] == rec["updated_at"]
    assert "T" in rec["created_at"]  # UTC isoformat


def test_add_rejects_bad_records_without_writing(tmp_path):
    reg = _fresh(tmp_path)
    for bad in ({"name": "x", "transport": "tcp"}, {"name": "y"}):
        with pytest.raises(RegistryError):
            reg.add(**bad)  # type: ignore[arg-type]
    # refusals never materialise the registry file
    assert not (tmp_path / "mcp_servers.json").exists()


def test_update_partial_merge_keeps_identity_and_stamps_updated(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    original_created = rec["created_at"]
    updated = reg.update(rec["id"], {"enabled": False})
    assert updated["enabled"] is False
    assert updated["name"] == rec["name"]
    assert updated["id"] == rec["id"]
    assert updated["created_at"] == original_created
    assert updated["updated_at"] >= updated["created_at"]


def test_rename_reslugs_the_id(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    renamed = reg.update(rec["id"], {"name": "Renamed Thing"})
    assert renamed["id"] == "renamed-thing"
    assert [s["id"] for s in reg.list()] == ["renamed-thing"]


def test_update_validates_the_merged_record(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    with pytest.raises(RegistryError):
        reg.update(rec["id"], {"command": "   "})  # validation on merged record
    assert reg.get(rec["id"])["command"] == VALID["command"]


def test_update_unknown_fields_and_unknown_id_refused(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    with pytest.raises(RegistryError):
        reg.update(rec["id"], {"nonsense": 1})
    with pytest.raises(UnknownServer):
        reg.update("does-not-exist", {"enabled": False})


def test_duplicate_names_and_slug_collisions(tmp_path):
    reg = _fresh(tmp_path)
    reg.add(**VALID)
    with pytest.raises(DuplicateName):
        reg.add(name="local shell", transport="stdio", command="x")
    with pytest.raises(DuplicateName):
        reg.add(name="Local Shell", transport="http", url="http://y")


def test_delete_roundtrip_and_unknown_refusal(tmp_path):
    reg = _fresh(tmp_path)
    rec = reg.add(**VALID)
    with pytest.raises(UnknownServer):
        reg.delete("nope")
    assert reg.delete(rec["id"]) is True
    assert reg.list() == []
    with pytest.raises(UnknownServer):
        reg.get(rec["id"])


def test_persistence_across_instances(tmp_path):
    reg = _fresh(tmp_path)
    reg.add(**VALID)
    reg.add(**VALID_HTTP)
    reg.update("local-shell", {"enabled": False})
    reg2 = McpRegistry(reg.path)
    assert [s["name"] for s in reg2.list()] == ["Local Shell", "remote"]
    assert reg2.get("local-shell")["enabled"] is False


def test_list_sorted_case_insensitively_by_name(tmp_path):
    reg = _fresh(tmp_path)
    reg.add(name="zeta", transport="http", url="http://z")
    reg.add(name="Alpha", transport="stdio", command="a")
    assert [s["name"] for s in reg.list()] == ["Alpha", "zeta"]


def test_corrupt_and_missing_documents_are_loud(tmp_path):
    reg = McpRegistry(tmp_path / "mcp_servers.json")
    (tmp_path / "mcp_servers.json").write_text("{not json")
    with pytest.raises(RegistryError):
        reg.list()
    (tmp_path / "mcp_servers.json").write_text(json.dumps({"servers": "nope"}))
    with pytest.raises(RegistryError):
        reg.list()


def test_entries_must_be_objects(tmp_path):
    p = tmp_path / "mcp_servers.json"
    p.write_text(json.dumps({"servers": ["a string"]}))
    with pytest.raises(RegistryError):
        McpRegistry(p).list()


def test_no_temp_files_left_behind(tmp_path):
    reg = _fresh(tmp_path)
    base = tmp_path / "mcp_servers.json"
    rec = reg.add(**VALID)
    reg.update(rec["id"], {"enabled": False})
    reg.delete(rec["id"])
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(base.read_text())["version"] == 1


def test_test_reports_validation_and_pending_client(tmp_path):
    reg = _fresh(tmp_path)
    reg.add(**VALID)
    report = reg.test("local-shell")
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["client"] == "not-connected"
    assert "not implemented" in report["reason"]
    with pytest.raises(UnknownServer):
        reg.test("ghost")


def test_hand_written_records_are_validated_on_demand(tmp_path):
    """The registry is tolerant at read; correctness is checked on demand via
    test()/validate_server, so drift never 500s the list endpoint."""
    p = tmp_path / "mcp_servers.json"
    p.write_text(
        json.dumps(
            {"version": 1, "servers": [{"name": "Broken", "transport": "stdio"}]}
        )
    )
    reg = McpRegistry(p)
    assert reg.list() == [{"name": "Broken", "transport": "stdio"}]  # tolerant read
    assert validate_server({"name": "Broken", "transport": "stdio"}) == [
        "stdio transport requires a command"
    ]


def test_demo_runs_clean():
    demo()  # asserts internally; raises on failure


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
