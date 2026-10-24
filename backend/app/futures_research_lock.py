"""Session advisory lock shared by manual replay and the draft consumer."""

import threading
import time

from sqlalchemy import select, text

from .db import FuturesLearningState, engine

_local = threading.Lock()
LOCK_ID = 728364902
WORKER_ID = 728364903


class ResearchLease:
    def __init__(self, connection=None, key=LOCK_ID):
        self.connection = connection
        self.key = key
        self.closed = False

    def check(self):
        if self.closed:
            raise RuntimeError("Futures replay lease closed")
        if self.connection is not None:
            self.connection.execute(text("SELECT 1"))
            self.connection.commit()

    def release(self):
        if self.closed:
            return
        self.closed = True
        if self.connection is not None:
            try:
                self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
                self.connection.commit()
            finally:
                self.connection.close()
        else:
            _local.release()


def acquire():
    if engine.dialect.name != "postgresql":
        return ResearchLease() if _local.acquire(blocking=False) else None
    connection = engine.connect()
    try:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID})
        connection.commit()
        if acquired:
            return ResearchLease(connection)
    except BaseException:
        connection.close()
        raise
    connection.close()
    return None


def worker_status(session):
    worker = session.scalar(select(FuturesLearningState.payload["draft_worker"]).where(
        FuturesLearningState.id == 1)) or {}
    return {**worker, "stale": time.time() - worker.get("heartbeat_at", 0) > 90}

def hold_worker():
    if engine.dialect.name != "postgresql":
        return None
    connection = engine.connect()
    if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_ID}):
        connection.close()
        raise RuntimeError("Another Futures draft worker is running")
    connection.commit()
    return ResearchLease(connection, WORKER_ID)
