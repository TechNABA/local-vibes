#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2", "openai>=1.40.0"]
# ///
"""Regression tests for the security-critical, model-independent invariants:
sandbox path confinement and the read_only tool schema. No network/model needed.

Run:  uv run test_server.py
"""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location("srv", pathlib.Path(__file__).with_name("server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

ROOT = pathlib.Path("/tmp/subagent-test-root").resolve()
ROOT.mkdir(parents=True, exist_ok=True)


def test_path_confinement_blocks_escapes():
    for bad in ["../../../etc/passwd", "/etc/passwd", "sub/../../escape", "..", "../sibling"]:
        try:
            srv._resolve(ROOT, bad)
        except ValueError:
            continue
        raise AssertionError(f"escape not blocked: {bad!r}")


def test_path_confinement_allows_in_sandbox():
    assert srv._resolve(ROOT, "calc.py") == ROOT / "calc.py"
    assert srv._resolve(ROOT, "sub/deep/x.py") == ROOT / "sub/deep/x.py"
    assert srv._resolve(ROOT, ".") == ROOT


def test_read_only_schema_has_no_mutators():
    ro = {t["function"]["name"] for t in srv._tool_schemas(read_only=True)}
    assert ro.isdisjoint({"write_file", "edit_file", "run_bash"}), ro
    assert {"read_file", "list_dir", "grep", "finish"} <= ro


def test_read_write_schema_has_mutators():
    rw = {t["function"]["name"] for t in srv._tool_schemas(read_only=False)}
    assert {"write_file", "edit_file"} <= rw


def test_result_cap_truncates():
    big = "x" * (srv.TOOL_RESULT_CAP + 500)
    out = srv._cap(big)
    assert len(out) < len(big) and "truncated" in out
    assert srv._cap("short") == "short"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
