"""Fresh-agent configuration. Secrets come ONLY from injected environment variables.
No implicit ~/.hermes/.env, uploader.env or scripts/.env lookup.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROVIDERS = {
    "custom": {"base_url": "", "key_var": "NOVEL_API_KEY", "stream": False},
    "go": {"base_url": "https://opencode.ai/zen/go/v1", "key_var": "OPENCODE_GO_API_KEY", "stream": False},
    "zen": {"base_url": "https://opencode.ai/zen/v1", "key_var": "OPENCODE_ZEN_API_KEY", "stream": False},
    "bai": {"base_url": "https://api.b.ai/v1", "key_var": "BAI_API_KEY", "stream": True},
}


def load_api_key(kind="llm"):
    if kind == "telegram":
        return os.environ.get("TELEGRAM_BOT_TOKEN")
    if kind == "llm":
        kind = os.environ.get("NOVEL_PROVIDER", "custom")
    if kind not in PROVIDERS:
        raise ValueError(f"Unknown provider: {kind}")
    return os.environ.get(PROVIDERS[kind]["key_var"])


def api_base(name=None):
    name = name or os.environ.get("NOVEL_PROVIDER", "custom")
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}")
    var = "NOVEL_API_BASE" if name == "custom" else f"{name.upper()}_API_BASE"
    return os.environ.get(var, PROVIDERS[name]["base_url"])


def channels():
    path = HERE.parent / "config/channels.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}