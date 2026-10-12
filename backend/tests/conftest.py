import os
import tempfile
from pathlib import Path

# Isolated database: never touches user's development data.
_test_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_test_dir.name) / "test.db")
os.environ["TRADING_MODE"] = "paper"
