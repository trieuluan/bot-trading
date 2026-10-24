"""Read-only benchmark: one draft-style replay (90d) on real data. Never writes learner/wallet state.

Run inside the replay container: python -m scripts.futures_draft_benchmark BTC_USDT
"""
import resource
import sys
import time

from backend.app.futures_draft_replay import run_screen
from backend.app.futures_drafts import gate
from backend.app.futures_strategies import RULE_SEEDS, genome_fingerprint, genome_name


def main(symbol):
    genome = RULE_SEEDS[0]
    end = int(time.time()) // 900 * 900
    draft = {"strategy": genome_name(genome), "genome": genome, "fingerprint": genome_fingerprint(genome),
             "window": {"start": end - 90 * 86400, "end": end, "warmup_bars": 1600}}
    started, phases = time.monotonic(), {}
    def emit(**changes):
        phases.setdefault(changes.get("phase"), round(time.monotonic() - started, 1))
    report = run_screen(symbol, draft, emit)
    status, result = gate(report)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 1024 * 1024)
    print({"symbol": symbol, "seconds": round(time.monotonic() - started, 1), "peak_rss_mb": round(peak_mb),
           "phase_starts_s": phases, "gate": status, "total": result["total_samples"],
           "validation": result["validation_samples"]})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "BTC_USDT")
