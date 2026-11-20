"""Settings, read from the environment at call time (a .env file is loaded if present)."""
import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name) or default


def mongodb_uri() -> str:
    return env("MONGODB_URI", "mongodb://localhost:27017")


def mongodb_db() -> str:
    return env("MONGODB_DB", "dotai_labs")


def public_base_url() -> Optional[str]:
    """Public https URL Twilio uses to reach this service (e.g. an ngrok URL)."""
    url = env("PUBLIC_BASE_URL")
    return url.rstrip("/") if url else None


PROVIDERS = {
    # provider: (API key setting, default model, default reasoning effort)
    "claude": ("ANTHROPIC_API_KEY", "claude-opus-5-5", "low"),
    # OpenAI's Chat Completions API rejects tool use with any effort other than "none" on this model.
    "openai": ("OPENAI_API_KEY", "gpt-6.1-terra", "none"),
}


def agent_provider() -> str:
    """Which company's model runs the phone conversation: "claude" or "openai"."""
    provider = env("FEEDBACK_AGENT_PROVIDER", "claude").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"FEEDBACK_AGENT_PROVIDER must be one of {sorted(PROVIDERS)}, not '{provider}'")
    return provider


def agent_model() -> str:
    return env("FEEDBACK_AGENT_MODEL", PROVIDERS[agent_provider()][1])


def agent_effort() -> str:
    return env("FEEDBACK_AGENT_EFFORT", PROVIDERS[agent_provider()][2])


def max_concurrent_calls() -> int:
    return int(env("CALL_MAX_CONCURRENT", "5"))


def missing_call_settings() -> List[str]:
    """Names of the settings that must be set before calls can be placed."""
    required = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PUBLIC_BASE_URL"]
    missing = [name for name in required if not env(name)]
    try:
        key = PROVIDERS[agent_provider()][0]
    except ValueError:
        return missing + ["FEEDBACK_AGENT_PROVIDER (must be claude or openai)"]
    if not env(key):
        missing.append(key)
    return missing
