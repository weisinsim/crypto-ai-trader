import json
import subprocess
import sys
from pathlib import Path


def test_indicator_benchmark_smoke_and_parity():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "validation/benchmark_replay_indicators.py", "--bars", "260"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = json.loads(result.stdout)
    assert report["benchmark"] == "indicator_microbenchmark_only"
    assert report["bars"] == 260
    assert report["decisions"] == 61
    assert report["parity"] == "passed"
    assert report["ema_precomputed_series_seconds"] >= 0
    assert report["rsi_precomputed_series_seconds"] >= 0
    assert "not end-to-end replay performance" in report["limitations"]
