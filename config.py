"""
Configuration management for the application.
Uses environment variables for sensitive credentials.
Supports both .env file and Streamlit secrets.
"""
from pydantic_settings import BaseSettings
from typing import Optional
import os


def _get_streamlit_secrets():
    """Try to get secrets from Streamlit if available."""
    try:
        import streamlit as st
        if hasattr(st, 'secrets'):
            return st.secrets
    except (ImportError, AttributeError):
        pass
    return None


class Settings(BaseSettings):
    """Application settings loaded from environment variables or Streamlit secrets."""
    
    # Google Custom Search API credentials
    google_api_key: str
    google_cse_id: str
    
    # Google Gemini API key (optional - for product matching)
    google_gemini_api_key: Optional[str] = None
    
    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8000
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    @classmethod
    def from_streamlit_secrets(cls):
        """Load settings from Streamlit secrets."""
        secrets = _get_streamlit_secrets()
        if not secrets:
            return None
        
        try:
            return cls(
                google_api_key=secrets.get("GOOGLE_API_KEY", ""),
                google_cse_id=secrets.get("GOOGLE_CSE_ID", ""),
                google_gemini_api_key=secrets.get("GOOGLE_GEMINI_API_KEY"),
                host=secrets.get("HOST", "0.0.0.0"),
                port=int(secrets.get("PORT", 8000))
            )
        except Exception:
            return None


def load_settings():
    """Load settings with better error handling.
    Tries Streamlit secrets first, then .env file.
    """
    # Önce Streamlit secrets'ı dene
    streamlit_settings = Settings.from_streamlit_secrets()
    if streamlit_settings and streamlit_settings.google_api_key and streamlit_settings.google_cse_id:
        return streamlit_settings
    
    # Streamlit secrets yoksa .env dosyasını kullan
    env_file = ".env"
    
    if not os.path.exists(env_file):
        print("=" * 60)
        print("⚠️  HATA: .env dosyası veya Streamlit secrets bulunamadı!")
        print("=" * 60)
        print(f"\nLütfen aşağıdaki yöntemlerden birini kullanın:")
        print("\n1. .env dosyası oluşturun:")
        print(f"   Proje dizininde '{env_file}' dosyası oluşturun.")
        print("\n2. VEYA Streamlit secrets kullanın:")
        print("   .streamlit/secrets.toml dosyası oluşturun.")
        print("\nÖrnek .env dosyası içeriği:")
        print("-" * 60)
        print("GOOGLE_API_KEY=your_google_api_key_here")
        print("GOOGLE_CSE_ID=your_custom_search_engine_id_here")
        print("GOOGLE_GEMINI_API_KEY=your_gemini_api_key_here (optional)")
        print("HOST=0.0.0.0")
        print("PORT=8000")
        print("-" * 60)
        print("\nDetaylı bilgi için README.md dosyasına bakın.")
        print("=" * 60)
        raise FileNotFoundError(
            f".env dosyası veya Streamlit secrets bulunamadı. "
            f"Lütfen '{env_file}' dosyası veya .streamlit/secrets.toml oluşturun. "
            "Örnek için README.md dosyasına bakın."
        )
    
    try:
        return Settings()
    except Exception as e:
        print("=" * 60)
        print("⚠️  HATA: .env dosyası yüklenirken hata oluştu!")
        print("=" * 60)
        print(f"\nHata: {str(e)}")
        print("\nLütfen .env dosyanızın doğru formatta olduğundan emin olun.")
        print("\nGerekli değişkenler:")
        print("- GOOGLE_API_KEY (zorunlu)")
        print("- GOOGLE_CSE_ID (zorunlu)")
        print("- GOOGLE_GEMINI_API_KEY (opsiyonel - ürün eşleştirme için)")
        print("- HOST (opsiyonel, varsayılan: 0.0.0.0)")
        print("- PORT (opsiyonel, varsayılan: 8000)")
        print("=" * 60)
        raise


# Global settings instance
try:
    settings = load_settings()
except FileNotFoundError:
    # Script çalıştırılıyorsa hata mesajı gösterilir
    # Ancak settings'i None olarak bırakmayalım, yoksa import hataları olur
    import sys
    sys.exit(1)

