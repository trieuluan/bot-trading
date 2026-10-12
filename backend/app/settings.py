import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading.db")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

def validate_mode():
    if TRADING_MODE != "paper":
        raise RuntimeError("Only paper signal monitoring is implemented. Live trading is disabled.")
