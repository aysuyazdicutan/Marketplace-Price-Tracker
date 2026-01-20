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
        # Streamlit secrets'a erişim
        if hasattr(st, 'secrets') and st.secrets:
            return st.secrets
    except (ImportError, AttributeError, RuntimeError):
        # RuntimeError: Streamlit context dışında çalışıyorsa
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
            # Streamlit secrets'a erişim - önce dict-style, sonra attribute-style
            def get_value(key, default=""):
                """Secrets'tan değer al"""
                # Dict-style
                try:
                    if key in secrets:
                        val = secrets[key]
                        return str(val).strip() if val else default
                except (KeyError, TypeError, AttributeError):
                    pass
                
                # Attribute-style
                try:
                    val = getattr(secrets, key, default)
                    return str(val).strip() if val else default
                except (AttributeError, TypeError):
                    pass
                
                return default
            
            google_api_key = get_value("GOOGLE_API_KEY", "")
            google_cse_id = get_value("GOOGLE_CSE_ID", "")
            
            # Zorunlu alanlar kontrolü
            if not google_api_key or not google_cse_id:
                return None
            
            # Opsiyonel alanlar
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
                port=port
            )
        except Exception as e:
            # Debug için exception'ı logla
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Streamlit secrets yüklenirken hata: {e}")
            import traceback
            logger.debug(traceback.format_exc())
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
# ⚡ Streamlit ortamında sys.exit() yapma - uygulama başlamadan ölmesin
try:
    settings = load_settings()
except FileNotFoundError as e:
    import sys
    # Streamlit ortamında ise None döndür, yoksa sys.exit()
    if 'streamlit' in sys.modules:
        # Streamlit ortamında - None olarak bırak, streamlit_app.py kontrol edecek
        settings = None
    else:
        # Normal script çalıştırılıyorsa hata ver
        print("=" * 60)
        print("⚠️  HATA: .env dosyası veya Streamlit secrets bulunamadı!")
        print("=" * 60)
        sys.exit(1)
except Exception as e:
    import sys
    # Diğer hatalar için de aynı mantık
    if 'streamlit' in sys.modules:
        settings = None
    else:
        raise

