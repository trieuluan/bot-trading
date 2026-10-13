"""Native Exness H4/H1/M15 observations; no execution or MEXC state."""

import math
import time

from sqlalchemy import select

from .db import ForexObservation, Session, Workspace, WorkspaceCandle, WorkspaceFeedState
from .exness_feed import FRAMES, WORKSPACE

ACTIVE = ("waiting_retest", "waiting_confirm")

def load_frames(session, now):
    return {
        frame: session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE,
            WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == frame,
            WorkspaceCandle.time + step <= now,
        ).order_by(WorkspaceCandle.time)).all()
        for frame, (_, step) in FRAMES.items()
    }

def audit_frames(frames):
    reports = []
    for frame, (_, step) in FRAMES.items():
        sides = {side: {row.time: row for row in frames[frame] if row.price_type == side}
                 for side in ("bid", "ask")}
        bids, asks = sides["bid"], sides["ask"]
        invalid = sum(
            not all(math.isfinite(getattr(row, field)) and getattr(row, field) > 0
                    for field in ("open", "high", "low", "close"))
            or row.low > min(row.open, row.close) or row.high < max(row.open, row.close)
            or row.time % step != 0
            for row in frames[frame]
        )
        paired = sorted(bids.keys() & asks.keys())
        crossed = sum(any(getattr(asks[stamp], field) < getattr(bids[stamp], field)
                          for field in ("open", "high", "low", "close")) for stamp in paired)
        spreads = [asks[stamp].close - bids[stamp].close for stamp in paired]
        gaps = [{"after": before, "before": after, "missing_intervals": (after - before) // step - 1}
                for before, after in zip(paired, paired[1:]) if after - before > step]
        reports.append({
            "timeframe": frame, "paired_bars": len(paired), "invalid_bars": invalid,
            "unpaired_bars": len(bids.keys() ^ asks.keys()), "crossed_bars": crossed,
            "close_spread_mean": sum(spreads) / len(spreads) if spreads else None,
            "close_spread_max": max(spreads) if spreads else None,
            "gap_count": len(gaps), "recent_gaps": gaps[-5:],
        })
    blocked = any(row["invalid_bars"] or row["unpaired_bars"] or row["crossed_bars"]
                  or not row["paired_bars"] for row in reports)
    return {"status": "blocked" if blocked else "checked", "frames": reports,
            "session_calendar_verified": False,
            "message": "Khoảng trống chưa đối chiếu lịch phiên; không tự coi là mất dữ liệu hay lấp nến. Spread là chênh lệch close ask/bid, không phải chi phí khớp lệnh."}

def observe_forex(candle, hours, higher, previous=None):
    available = candle.time + 900
    hours = [row for row in hours if row.time + 3600 <= available]
    higher = [row for row in higher if row.time + 14400 <= available]
    snapshot = {"strategy": "exness_mtf_v1", "price_type": "bid", "candle_time": candle.time,
                "available_at": available, "status": "warming_up", "trend": None,
                "reason": "Cần 50 nến 4h và 21 nến 1h đã đóng; chỉ quan sát",
                "parameters": {"ema_4h": 50, "lookback_1h": 20, "wait_hours": 12, "target_r": 2}}
    if len(higher) < 50 or len(hours) < 21:
        return snapshot
    ema = higher[0].close
    for row in higher[1:]:
        ema += 2 / 51 * (row.close - ema)
    direction = 1 if higher[-1].close > ema else -1 if higher[-1].close < ema else 0
    history = hours[-21:-1] if hours[-1].time + 3600 == available else hours[-20:]
    snapshot.update(status="waiting_breakout", trend="up" if direction == 1 else "down" if direction == -1 else "flat",
                    ema_4h=ema, closed_4h_time=higher[-1].time,
                    range_high=max(row.high for row in history), range_low=min(row.low for row in history),
                    range_start=history[0].time, range_end=history[-1].time + 3600,
                    reason="Chờ breakout 1h cùng chiều 4h; chỉ quan sát")
    if previous and previous["status"] in ACTIVE:
        snapshot = {**previous, **{key: snapshot[key] for key in (
            "candle_time", "available_at", "trend", "ema_4h", "closed_4h_time")}}
        side = 1 if snapshot["side"] == "long" else -1
        if candle.time != previous["candle_time"] + 900:
            snapshot.update(status="cancelled", reason="Có khoảng trống M15; hủy setup vì không quan sát được đường giá")
        elif available > snapshot["expires_at"]:
            snapshot.update(status="expired", reason="Quá 12 giờ chưa xác nhận")
        elif direction != side:
            snapshot.update(status="cancelled", reason="Xu hướng 4h không còn cùng chiều")
        elif side * (candle.close - snapshot["level"]) < 0:
            snapshot.update(status="cancelled", reason="15m đóng phá ngược vùng")
        else:
            extreme = candle.low if side == 1 else candle.high
            if snapshot["status"] == "waiting_confirm":
                stop = min(snapshot["retest_extreme"], extreme) if side == 1 else max(snapshot["retest_extreme"], extreme)
                snapshot["retest_extreme"] = stop
                risk = side * (candle.close - stop)
                target = candle.close + side * 2 * risk
                if side * (candle.close - candle.open) > 0 and side * (candle.close - snapshot["level"]) > 0:
                    if risk > 0 and target > 0:
                        snapshot.update(status="confirmed", confirm_time=candle.time, reference_entry=candle.close,
                                        stop=stop, target=target,
                                        reason="15m xác nhận giữ vùng; SL cấu trúc retest, TP tham chiếu 2R; không phải giá khớp lệnh")
                    else:
                        snapshot.update(status="cancelled", reason="SL/TP tham chiếu không hợp lệ")
            elif side * (snapshot["level"] - extreme) >= 0:
                snapshot.update(status="waiting_confirm", touch_time=candle.time, retest_extreme=extreme,
                                reason="Đã retest; chờ nến 15m tiếp theo xác nhận giữ vùng")
        return snapshot
    if direction and hours[-1].time + 3600 == available:
        level = snapshot["range_high"] if direction == 1 else snapshot["range_low"]
        if direction * (hours[-1].close - level) > 0:
            snapshot.update(status="waiting_retest", side="long" if direction == 1 else "short",
                            level=level, breakout_time=hours[-1].time, expires_at=available + 12 * 3600,
                            touch_time=None, confirm_time=None, stop=None, target=None,
                            reason="1h breakout cùng chiều 4h; chờ retest từ nến 15m kế tiếp")
    return snapshot

def latest_observation(session):
    return session.scalar(select(ForexObservation).where(
        ForexObservation.workspace_id == WORKSPACE,
    ).order_by(ForexObservation.candle_time.desc()).limit(1))

def observation_tick(now=None):
    now = int(time.time()) if now is None else now
    with Session.begin() as session:
        session.scalar(select(Workspace).where(Workspace.id == WORKSPACE).with_for_update())
        frames = load_frames(session, now)
        quality = audit_frames(frames)
        feeds = [session.get(WorkspaceFeedState, (WORKSPACE, frame)) for frame in FRAMES]
        if quality["status"] == "blocked" or any(
            state is None or state.error or state.checked_until is None
            or state.checked_until < now // FRAMES[frame][1] * FRAMES[frame][1]
            for frame, state in zip(FRAMES, feeds)
        ):
            return {"status": "blocked", "reason": "Chờ dữ liệu hợp lệ và cả 3 khung đồng bộ xong"}
        bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
        latest = latest_observation(session)
        previous = latest.snapshot if latest else None
        pending = [row for row in bids["15m"] if latest is None or row.time > latest.candle_time]
        pending = pending[:96] if latest else pending[-1:]
        for candle in pending:
            previous = observe_forex(candle, bids["1h"], bids["4h"], previous)
            previous["provenance"] = "catch_up" if candle.time + 1800 <= now else "live"
            session.add(ForexObservation(workspace_id=WORKSPACE, candle_time=candle.time, snapshot=previous))
        return previous

def observation_summary(now=None):
    now = int(time.time()) if now is None else now
    with Session() as session:
        quality = audit_frames(load_frames(session, now))
        latest = latest_observation(session)
        events = session.scalars(select(ForexObservation).where(
            ForexObservation.workspace_id == WORKSPACE,
        ).order_by(ForexObservation.candle_time.desc()).limit(20)).all()
        snapshot = latest.snapshot if latest else None
        feeds = [session.get(WorkspaceFeedState, (WORKSPACE, frame)) for frame in FRAMES]
        paused = quality["status"] == "blocked" or any(
            state is None or state.error or state.checked_until is None
            or state.checked_until < now // FRAMES[frame][1] * FRAMES[frame][1]
            for frame, state in zip(FRAMES, feeds)
        )
        status = "data_paused" if paused else snapshot["status"] if snapshot else "not_started"
        message = "Dữ liệu chưa sẵn sàng; snapshot bên dưới là lần quan sát đã lưu" if paused else snapshot["reason"] if snapshot else "Chờ worker khởi tạo MTF Exness"
        return {"status": status, "message": message,
                "snapshot": snapshot, "recent": [row.snapshot for row in events], "quality": quality}
