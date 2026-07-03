from __future__ import annotations

from harness_core.context.compressor import ContextCompressor


def test_short_table_unchanged():
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    out = ContextCompressor(max_rows=100).compress(rows)
    assert out["compressed"] is False
    assert out["row_count"] == 2


def test_long_table_summarized():
    rows = [{"x": i} for i in range(500)]
    out = ContextCompressor(max_rows=20).compress(rows)
    assert out["compressed"] is True
    assert out["row_count"] == 500
    assert out["stats"]["x"]["min"] == 0
    assert out["stats"]["x"]["max"] == 499


def test_long_text_truncated():
    text = "x" * 10000
    out = ContextCompressor(max_text_chars=200).compress(text)
    assert "truncated" in out
    assert len(out) < 1000
