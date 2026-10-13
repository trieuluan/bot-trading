from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .settings import DATABASE_URL


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass

class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    venue: Mapped[str] = mapped_column(String(30), unique=True)
    symbol: Mapped[str] = mapped_column(String(30))

class WorkspaceCandle(Base):
    __tablename__ = "workspace_candles"
    __table_args__ = (
        CheckConstraint("price_type IN ('bid', 'ask')", name="ck_workspace_candle_price_type"),
        CheckConstraint("timeframe IN ('1m', '15m', '1h', '4h')", name="ck_workspace_candle_timeframe"),
        CheckConstraint("open > 0 AND low > 0 AND low <= open AND low <= close AND high >= open AND high >= close", name="ck_workspace_candle_prices"),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(10), primary_key=True)
    price_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    tick_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

class WorkspaceFeedState(Base):
    __tablename__ = "workspace_feed_state"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(10), primary_key=True)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ForexObservation(Base):
    __tablename__ = "forex_observations"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    candle_time: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)

class BotState(Base):
    __tablename__ = "bot_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bar: Mapped[int] = mapped_column(Integer, default=60)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(500))


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    __table_args__ = (
        UniqueConstraint(
            "source", "symbol", "timeframe", "candle_time", name="uq_signal_market_time"
        ),
    )
    source: Mapped[str] = mapped_column(String(30), default="synthetic")
    timeframe: Mapped[str] = mapped_column(String(10), default="15m")
    candle_time: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(10))
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(500))


class MarketState(Base):
    __tablename__ = "market_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="synthetic")
    last_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MarketCandle(Base):
    __tablename__ = "market_candles"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(10), primary_key=True)
    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict] = mapped_column(JSON)


class MtfObservationState(Base):
    __tablename__ = "mtf_observation_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_candle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MtfObservationEvent(Base):
    __tablename__ = "mtf_observation_events"
    candle_time: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot: Mapped[dict] = mapped_column(JSON)

class MtfResearchSample(Base):
    __tablename__ = "mtf_research_samples"
    confirm_time: Mapped[int] = mapped_column(Integer, primary_key=True)
    features: Mapped[dict] = mapped_column(JSON)
    evaluation: Mapped[dict] = mapped_column(JSON)


class PaperWallet(Base):
    __tablename__ = "paper_wallet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initial_balance: Mapped[float] = mapped_column(Float, default=10000)
    balance: Mapped[float] = mapped_column(Float, default=10000)
    fee_bps: Mapped[float] = mapped_column(Float, default=5)
    slippage_bps: Mapped[float] = mapped_column(Float, default=2)
    quote: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    contract: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    funding_cursor: Mapped[int] = mapped_column(Integer)
    funding_synced_at: Mapped[int] = mapped_column(Integer, default=0)
    funding_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    monitoring: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(10), default="manual")
    signal_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    side: Mapped[str] = mapped_column(String(10))
    entry_time: Mapped[int] = mapped_column(Integer)
    entry: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    contracts: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    entry_fee: Mapped[float] = mapped_column(Float)
    exit_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_fee: Mapped[float] = mapped_column(Float, default=0)
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    funding_cost: Mapped[float] = mapped_column(Float, default=0)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)


class PaperCommand(Base):
    __tablename__ = "paper_commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class PaperFunding(Base):
    __tablename__ = "paper_funding"
    trade_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    rate: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)


class AutoPaperState(Base):
    __tablename__ = "auto_paper_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_percent: Mapped[float] = mapped_column(Float, default=1)
    max_daily_trades: Mapped[int] = mapped_column(Integer, default=5)
    daily_loss_percent: Mapped[float] = mapped_column(Float, default=3)
    last_candle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day: Mapped[str] = mapped_column(String(10), default="")
    day_start_equity: Mapped[float] = mapped_column(Float, default=10000)
    status: Mapped[str] = mapped_column(String(500), default="Auto Paper đang tắt")


class AutoPaperDecision(Base):
    __tablename__ = "auto_paper_decisions"
    candle_time: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(500))
    setup: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON)


engine = create_engine(
    DATABASE_URL,
    **({"connect_args": {"check_same_thread": False}} if DATABASE_URL.startswith("sqlite") else {}),
)
Session = sessionmaker(engine)
