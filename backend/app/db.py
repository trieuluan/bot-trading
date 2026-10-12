from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .settings import DATABASE_URL

def utcnow():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

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
    candle_time: Mapped[int] = mapped_column(Integer, unique=True)
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(10))
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(500))

engine = create_engine(DATABASE_URL, **({"connect_args": {"check_same_thread": False}} if DATABASE_URL.startswith("sqlite") else {}))
Session = sessionmaker(engine)
