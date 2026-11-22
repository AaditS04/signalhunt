"""Runtime configuration. Everything that can change during the event lives in .env or client_profile.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except ValueError:
        return default


@dataclass
class Settings:
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    model: str = field(default_factory=lambda: os.getenv("SIGNALHUNT_MODEL", "claude-opus-5-5").strip())
    effort: str = field(default_factory=lambda: os.getenv("SIGNALHUNT_EFFORT", "low").strip())
    crustdata_key: str = field(default_factory=lambda: os.getenv("CRUSTDATA_API_KEY", "").strip())
    crustdata_usd_per_credit: float = field(default_factory=lambda: _f("CRUSTDATA_USD_PER_CREDIT", 0.01))
    max_run_cost_usd: float = field(default_factory=lambda: _f("MAX_RUN_COST_USD", 1.50))
    max_run_seconds: int = field(default_factory=lambda: _i("MAX_RUN_SECONDS", 600))
    max_articles: int = field(default_factory=lambda: _i("MAX_ARTICLES_PER_RUN", 60))
    max_llm_calls: int = field(default_factory=lambda: _i("MAX_LLM_CALLS_PER_RUN", 40))
    usd_inr: float = field(default_factory=lambda: _f("USD_INR", 88.0))
    outreach_mode: str = field(default_factory=lambda: os.getenv("OUTREACH_MODE", "sandbox").strip())
    db_path: str = field(default_factory=lambda: os.getenv("SIGNALHUNT_DB", str(ROOT / "data" / "signalhunt.db")))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_key)

    @property
    def crustdata_enabled(self) -> bool:
        return bool(self.crustdata_key)


# Model prices in USD per million tokens (input, output). Source: Anthropic pricing table, Nov 2026.
MODEL_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
}


def settings() -> Settings:
    return Settings()


def client_profile() -> dict:
    path = ROOT / "client_profile.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
