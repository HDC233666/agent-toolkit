"""调用记录 JSONL 降级模式测试：不依赖数据库，CI 上也能跑。"""

from __future__ import annotations

from app.recorder import JSONLRecorder


def test_jsonl_recorder_roundtrip(tmp_path):
    path = tmp_path / "logs.jsonl"
    recorder = JSONLRecorder(path)

    assert recorder.backend == "jsonl"
    run_id = recorder.record(
        "现在几点",
        "现在是下午三点",
        [{"tool": "now", "args": {}, "result": {"ok": True, "time": "15:00"}}],
        tokens=12,
        latency_ms=345,
    )
    assert run_id == 0

    runs = recorder.list_runs(5)
    assert len(runs) == 1
    assert runs[0]["question"] == "现在几点"
    assert runs[0]["steps"][0]["tool"] == "now"
    assert runs[0]["steps"][0]["ok"] is True
    assert runs[0]["latency_ms"] == 345

    assert recorder.totals() == {"runs": 1, "tool_calls": 1}


def test_jsonl_recorder_missing_file_is_empty(tmp_path):
    recorder = JSONLRecorder(tmp_path / "nope.jsonl")
    assert recorder.list_runs() == []
    assert recorder.totals() == {"runs": 0, "tool_calls": 0}
