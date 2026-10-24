"""Extend the MEXC replay pickle with newly closed candles/fair/funding. Research only."""
import pickle
import sys
from pathlib import Path

from backend.app.futures_history import load_history
from backend.app.futures_symbols import SYMBOLS
from backend.app.mexc import MexcFuturesAdapter, validate_candles

def main(source, destination):
    data = pickle.loads(Path(source).read_bytes())
    for symbol in SYMBOLS:
        start, _, candles, fair, funding = data[symbol]
        adapter = MexcFuturesAdapter(symbol=symbol)
        try:
            now = adapter.server_time()
            end = now // 900 * 900
            candles += load_history(adapter, candles[-1].time + 900, end, now)
            fair += load_history(adapter, fair[-1].time + 900, end, now, fair=True)
            funding = adapter.funding_history(funding[0]["time"], end)
        finally:
            adapter.close()
        validate_candles(candles)
        validate_candles(fair)
        data[symbol] = (start, end, candles, fair, funding)
        print(symbol, len(candles), len(fair), candles[-1].time, funding[-1]["time"], flush=True)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(pickle.dumps(data))

if __name__ == "__main__":
    main(*sys.argv[1:])
