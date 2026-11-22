"""The repo is public: .env.example must never carry a real key (keys go only in the gitignored .env)."""
from __future__ import annotations

import re
from pathlib import Path


def test_env_example_has_no_secrets():
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    for name in ("CRUSTDATA_API_KEY", "ANTHROPIC_API_KEY", "SMTP_PASSWORD"):
        m = re.search(rf"^{name}=(.*)$", text, re.M)
        assert m is None or not m.group(1).strip(), f"{name} has a value in .env.example; put it in .env instead"
