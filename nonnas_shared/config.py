"""Shared config loading — env vars and the packaged JSON config files."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "data"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_json_config(filename: str) -> dict:
    with open(CONFIG_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def load_qbo_account_map() -> dict:
    return load_json_config("qbo_account_map.json")
