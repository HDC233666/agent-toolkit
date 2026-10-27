"""工具层测试：不联网、不碰真实文件系统之外的东西。"""

from __future__ import annotations

from app import tools


def test_calculator_basic() -> None:
    assert tools.calculator("12 + 3 * 4")["result"] == 24
    assert tools.calculator("2 ** 10")["result"] == 1024
    assert tools.calculator("(1 + 2) * (3 + 4)")["result"] == 21


def test_calculator_rejects_code_injection() -> None:
    result = tools.calculator("__import__('os').system('dir')")
    assert result["ok"] is False


def test_calculator_division_by_zero() -> None:
    assert tools.calculator("1 / 0")["ok"] is False


def test_now_returns_date_and_weekday() -> None:
    result = tools.now()
    assert result["ok"] is True
    assert len(result["date"]) == 10
    assert result["weekday"] in "一二三四五六日"


def test_file_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path)

    assert tools.write_file("notes/a.txt", "hello 你好")["ok"] is True
    read = tools.read_file("notes/a.txt")
    assert read["content"] == "hello 你好"
    assert "notes/a.txt" in tools.list_files()["files"]


def test_path_traversal_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path)

    assert tools.write_file("../evil.txt", "x")["ok"] is False
    assert tools.read_file("../../etc/passwd")["ok"] is False


def test_http_get_rejects_non_http() -> None:
    assert tools.http_get("file:///etc/passwd")["ok"] is False


def test_run_tool_unknown() -> None:
    assert tools.run_tool("no_such_tool")["ok"] is False
