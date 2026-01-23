"""
Configuration management for the application.

Priority:
1) Streamlit secrets (when running under Streamlit)
2) .env file (local/dev)
3) OS environment variables (fallback)

Notes:
- We do NOT set settings=None just because streamlit is importable.
- .env path is pinned to this file's directory to avoid CWD issues.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import os
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env", override=True) # override=True eski değerleri temizler





def _get_streamlit_secrets():
    """Return st.secrets if available, else None (no crash outside Streamlit)."""
    try:
        import streamlit as st
        try:
            # st.secrets may raise StreamlitSecretNotFoundError when no secrets.toml exists
            if hasattr(st, "secrets") and len(st.secrets) > 0:
                return st.secrets
        except Exception:
            return None
    except Exception:
        return None



class Settings(BaseSettings):
    # Required
    google_api_key: str
    google_cse_id: str

    # Optional
    google_gemini_api_key: Optional[str] = None

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def from_streamlit_secrets(cls) -> Optional["Settings"]:
        secrets = _get_streamlit_secrets()
        if not secrets:
            return None

        def get_value(key: str, default: str = "") -> str:
            try:
                if key in secrets:
                    val = secrets[key]
                    return str(val).strip() if val else default
            except Exception:
                pass
            return default

        google_api_key = get_value("GOOGLE_API_KEY", "")
        google_cse_id = get_value("GOOGLE_CSE_ID", "")

        if not google_api_key or not google_cse_id:
            return None

        gemini_key = get_value("GOOGLE_GEMINI_API_KEY", "") or None
        host = get_value("HOST", "0.0.0.0") or "0.0.0.0"
        try:
            port = int(get_value("PORT", "8000") or 8000)
        except (ValueError, TypeError):
            port = 8000

        return cls(
            google_api_key=google_api_key,
            google_cse_id=google_cse_id,
            google_gemini_api_key=gemini_key,
            host=host,
            port=port,
        )


def load_settings() -> Settings:
    # 1) Streamlit secrets (if available)
    s = Settings.from_streamlit_secrets()
    if s:
        return s

    # 2) .env / OS env via pydantic-settings
    return Settings()


# Global settings instance
settings = load_settings()
