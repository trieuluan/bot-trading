"""Bounded API rechecks; empty responses are evidence of absent bars, not proof of closure."""

import time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import ForexGapAudit, Session, WorkspaceCandle
from .exness import ExnessError
from .exness_feed import WORKSPACE, normalize_rows, record_connection_failure
from .exness_sessions import regular_session_gap


def recheck_gap(api, now=None):
    now = int(time.time()) if now is None else now
    with Session() as session:
        stamps = session.scalars(select(WorkspaceCandle.time).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "15m", WorkspaceCandle.price_type == "bid",
            WorkspaceCandle.time + 900 <= now,
        ).order_by(WorkspaceCandle.time)).all()
        candidate = next(((after, before) for after, before in zip(stamps, stamps[1:])
                          if before - after > 900 and not regular_session_gap(after, before)
                          and ((audit := session.get(ForexGapAudit, (WORKSPACE, after, before))) is None
                               or audit.retry_at <= now)), None)
    if candidate is None:
        return {"status": "current"}
    after, before = candidate
    start = after + 900
    end = min(before, start + api.candle_limit * 900)
    snapshot = {"after": after, "before": before, "queried_from": start, "queried_to": end}
    entries = []
    retry_at = now + 86400
    try:
        raw = api.candles("XAUUSD", "M15", start, end, now=now)
        if len(raw) > api.candle_limit:
            raise ExnessError("Candle response exceeds cap")
        entries = normalize_rows(raw, "15m", start, end)
        snapshot.update(status="backfilled" if entries else "no_bars_returned", m15_bars=len(entries) // 2)
        if not entries and (end - start) // 60 <= api.candle_limit:
            minute_rows = api.candles("XAUUSD", "M1", start, end, now=now)
            if len(minute_rows) > api.candle_limit:
                raise ExnessError("Minute response exceeds cap")
            snapshot["m1_bars"] = len(minute_rows)
            if minute_rows:
                snapshot["status"] = "inconsistent_timeframes"
        if end < before:
            snapshot["status"] = "partial_check"
    except ExnessError as exc:
        entries = []
        snapshot["status"] = "api_error"
        retry_at = now + max(60, exc.retry_after)
        if exc.rate_limited:
            record_connection_failure(exc.retry_after)
    with Session.begin() as session:
        insert = sqlite_insert if session.bind.dialect.name == "sqlite" else postgres_insert
        for offset in range(0, len(entries), 500):
            statement = insert(WorkspaceCandle).values(entries[offset:offset + 500])
            session.execute(statement.on_conflict_do_nothing())
        audit = session.get(ForexGapAudit, (WORKSPACE, after, before))
        if audit is None:
            audit = ForexGapAudit(workspace_id=WORKSPACE, after=after, before=before)
            session.add(audit)
        audit.checked_at, audit.retry_at, audit.snapshot = now, retry_at, snapshot
    return snapshot


def gap_audits(session):
    return [{**row.snapshot, "checked_at": row.checked_at, "retry_at": row.retry_at}
            for row in session.scalars(select(ForexGapAudit).where(
                ForexGapAudit.workspace_id == WORKSPACE,
            ).order_by(ForexGapAudit.after.desc()).limit(100))]
