"""Futures learner in its own process: its CPU/GIL cost must not delay quotes or SL/TP."""

import logging
import signal
import threading

from backend.app.futures_learner import learning_tick
from backend.app.futures_symbols import SYMBOLS
from backend.app.futures_wakeup import listen
from backend.app.settings import validate_mode
from bot.worker import run_symbol_loop, stop


def main():
    validate_mode()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("Futures learning worker ready")
    threads = [threading.Thread(target=listen, args=(stop,), name="futures-wakeup")]
    threads.extend(
        threading.Thread(
            target=run_symbol_loop,
            args=(symbol, "futures-learning", learning_tick, 30, index * 0.4),
            name=f"{symbol}-futures-learning",
        )
        for index, symbol in enumerate(SYMBOLS)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
