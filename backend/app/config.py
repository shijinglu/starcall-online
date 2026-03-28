"""Environment-based configuration for the AI Conversation backend."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the backend directory (if present)
_backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(_backend_dir / ".env")

# --- External API keys ---
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# --- Session config ---
SESSION_TTL_SECONDS: float = float(os.getenv("SESSION_TTL_SECONDS", "7200"))
AUTH_TOKEN_TTL_SECONDS: float = float(os.getenv("AUTH_TOKEN_TTL_SECONDS", "300"))

# --- Agent task config ---
AGENT_TASK_TIMEOUT_SECONDS: float = float(os.getenv("AGENT_TASK_TIMEOUT_SECONDS", "30"))
THINKING_HEARTBEAT_INTERVAL_SECONDS: float = float(
    os.getenv("THINKING_HEARTBEAT_INTERVAL_SECONDS", "10")
)

# --- Server ---
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# --- Paths ---
PROMPTS_DIR: Path = _backend_dir / "prompts"

# --- Claude model ---
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# --- Gemini model ---
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")

# --- Cleanup interval ---
CLEANUP_INTERVAL_SECONDS: float = float(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))

# --- Max tool-use rounds per Claude invocation ---
MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", "10"))
