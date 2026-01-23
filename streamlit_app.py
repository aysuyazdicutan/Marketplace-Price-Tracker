"""
Streamlit Web Arayüzü - Fiyat Karşılaştırma Aracı
Non-technical kullanıcılar için basit ve kullanıcı dostu arayüz
"""
import streamlit as st
import asyncio
import os
import tempfile
from pathlib import Path
import pandas as pd

import config
from config import Settings
import streamlit as st
import config, streamlit as st

import shutil
import streamlit as st

# ⚡ KRİTİK: UI'ı hemen render et (health check için)
st.set_page_config(
    page_title="Fiyat Karşılaştırma Aracı",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Başlık - hemen render olmalı
st.title("📊 Fiyat Karşılaştırma Aracı ")
st.markdown("Excel dosyanızı yükleyin ve marketplace'lerde fiyat karşılaştırması yapın.")

# ⚡ LAZY IMPORT: Ağır modülleri sadece gerektiğinde yükle
# process_excel import'u butona tıklandığında yapılacak

# Sidebar - Ayarlar
with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    marketplace_options = {
        "Tüm Marketplace'ler": None,
        "Hepsiburada": "Hepsiburada",
        "Teknosa": "Teknosa",
        "Trendyol": "Trendyol",
        "Amazon": "Amazon"
    }
    
    selected_marketplace = st.selectbox(
        "Marketplace Seçin:",
        options=list(marketplace_options.keys()),
        index=0
    )
    
    marketplace_value = marketplace_options[selected_marketplace]
    
    st.markdown("---")
    st.markdown("### 📝 Kullanım Kılavuzu")
    st.markdown("""
    1. Excel dosyanızı yükleyin
    2. Marketplace seçin
    3. "Başlat" butonuna tıklayın
    4. İşlem tamamlandığında sonuçları indirin
    """)

# Ana içerik
uploaded_file = st.file_uploader(
    "📁 Excel Dosyası Seçin",
    type=['xlsx', 'xls'],
    help="Ürün listesi içeren Excel dosyasını yükleyin"
)

if uploaded_file is not None:
    # Dosyayı geçici olarak kaydet
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
        tmp_file.write(uploaded_file.getvalue())
        tmp_path = tmp_file.name
    
    try:
        # Excel dosyasını kontrol et
        df = pd.read_excel(tmp_path, engine='openpyxl')
        st.success(f"✅ Dosya yüklendi: {len(df)} satır bulundu")
        
        # İlk birkaç satırı göster
        with st.expander("📋 Excel Dosyası Önizleme (İlk 5 satır)"):
            st.dataframe(df.head(), width='stretch')
        
        # Başlat butonu
        if st.button("🚀 İşlemi Başlat", type="primary", use_container_width=True):
            # ⚡ LAZY IMPORT: Sadece butona tıklandığında yükle
            try:
                from process_excel import process_excel_file, save_results_to_excel
                from config import settings
                import asyncio
                
                # Settings kontrolü
                if settings is None:
                    st.error("⚠️ **API Key'leri Yapılandırılmamış!**")
                    
                    # Debug: Secrets'ın yüklenip yüklenmediğini kontrol et
                    with st.expander("🔍 Debug Bilgisi - Secrets Kontrolü", expanded=True):
                        try:
                            if hasattr(st, 'secrets') and st.secrets:
                                st.success("✅ Streamlit secrets mevcut")
                                
                                # Secrets içeriğini göster
                                try:
                                    secrets_dict = {}
                                    # Dict-style erişim
                                    for key in ["GOOGLE_API_KEY", "GOOGLE_CSE_ID", "GOOGLE_GEMINI_API_KEY"]:
                                        try:
                                            if key in st.secrets:
                                                val = st.secrets[key]
                                                # İlk 10 karakteri göster, geri kalanını gizle
                                                if val and len(str(val)) > 10:
                                                    secrets_dict[key] = str(val)[:10] + "..." + " (gizli)"
                                                else:
                                                    secrets_dict[key] = str(val) if val else "❌ YOK"
                                            else:
                                                secrets_dict[key] = "❌ YOK"
                                        except:
                                            # Attribute-style erişim
                                            try:
                                                val = getattr(st.secrets, key, None)
                                                if val and len(str(val)) > 10:
                                                    secrets_dict[key] = str(val)[:10] + "..." + " (gizli)"
                                                else:
                                                    secrets_dict[key] = str(val) if val else "❌ YOK"
                                            except:
                                                secrets_dict[key] = "❌ YOK"
                                    
                                    st.json(secrets_dict)
                                    
                                    # Kontrol
                                    if secrets_dict.get("GOOGLE_API_KEY", "").startswith("❌"):
                                        st.error("❌ GOOGLE_API_KEY bulunamadı!")
                                    if secrets_dict.get("GOOGLE_CSE_ID", "").startswith("❌"):
                                        st.error("❌ GOOGLE_CSE_ID bulunamadı!")
                                        
                                except Exception as e:
                                    st.error(f"Secrets okunurken hata: {e}")
                                    st.exception(e)
                            else:
                                st.warning("❌ Streamlit secrets bulunamadı veya boş.")
                                st.info("Lütfen Streamlit Cloud'da Settings > Secrets bölümünden secrets ekleyin.")
                        except Exception as e:
                            st.error(f"Debug kontrolü sırasında hata: {e}")
                    
                    st.markdown("""
                    ### Streamlit Cloud Secrets Yapılandırması Gerekli
                    
                    Lütfen Streamlit Cloud'da **Settings > Secrets** bölümüne gidin ve şu bilgileri ekleyin:
                    
                    ```toml
                    GOOGLE_API_KEY = "your_google_api_key_here"
                    GOOGLE_CSE_ID = "your_custom_search_engine_id_here"
                    GOOGLE_GEMINI_API_KEY = "your_gemini_api_key_here"  # Opsiyonel
                    ```
                    
                    **Önemli:** 
                    - Değerler **tırnak içinde** olmalı (`"..."`)
                    - Eşittir işaretinin **her iki tarafında boşluk** olmalı (`KEY = "value"`)
                    - Secrets'ı ekledikten sonra uygulamayı **yeniden başlatın** (restart)
                    
                    Daha fazla bilgi için README.md dosyasına bakın.
                    """)
                    st.stop()
                    
            except ImportError as e:
                st.error(f"❌ Modül yüklenemedi: {str(e)}")
                st.stop()
            except Exception as e:
                st.error(f"❌ Beklenmeyen hata: {str(e)}")
                st.exception(e)
                st.stop()
            
            if marketplace_value is None:
                st.info("🔄 Tüm marketplace'ler için işlem başlatılıyor...")
            else:
                st.info(f"🔄 {marketplace_value} için işlem başlatılıyor...")
            
            # Progress bar ve durum gösterimi
            progress_bar = st.progress(0)
            status_text = st.empty()
            product_info = st.empty()
            
            # Toplam ürün sayısını öğrenmek için önce dosyayı oku
            try:
                preview_df = pd.read_excel(tmp_path, engine='openpyxl')
                total_products = len(preview_df)
            except:
                total_products = 0
            
            # Progress callback fonksiyonu
            def update_progress(current, total, product_name):
                """Her ürün işlendiğinde progress bar'ı güncelle"""
                progress = current / total if total > 0 else 0
                progress_bar.progress(progress)
                status_text.text(f"⏳ İşleniyor: {current}/{total} ürün tamamlandı")
                product_info.text(f"📦 Şu an işlenen: {product_name[:50]}...")
            
            try:
                # Async fonksiyonu çalıştır
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                status_text.text("⏳ İşlem başlatılıyor...")
                progress_bar.progress(0)
                
                # Excel dosyasını işle (progress_callback ile)
                results = loop.run_until_complete(
                    process_excel_file(tmp_path, marketplace_value, None, update_progress)
                )
                
                progress_bar.progress(0.9)
                status_text.text("💾 Sonuçlar kaydediliyor...")
                product_info.empty()
                
                # Sonuçları kaydet
                output_file = "results.xlsx"
                save_results_to_excel(results, output_file)
                
                progress_bar.progress(1.0)
                status_text.text(f"✅ İşlem tamamlandı! {len(results)} ürün işlendi.")
                
                # Sonuçları göster
                st.success(f"✅ {len(results)} ürün işlendi!")
                
                # Sonuçları DataFrame olarak göster
                if results:
                    results_df = pd.DataFrame(results)
                    
                    # Fiyat sütunlarını formatla (kuruşları kaldır)
                    price_columns = ['MM Price', 'teknosa fiyatı', 'hepsiburada fiyatı', 'trendyol fiyatı', 'amazon fiyatı']
                    for col in price_columns:
                        if col in results_df.columns:
                            results_df[col] = results_df[col].apply(lambda x: round(x) if pd.notna(x) and isinstance(x, (int, float)) else x)
                    
                    st.dataframe(results_df, width='stretch')
                    
                    # İndirme butonu
                    if os.path.exists(output_file):
                        with open(output_file, 'rb') as f:
                            st.download_button(
                                label="📥 Sonuçları İndir (Excel)",
                                data=f.read(),
                                file_name=output_file,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                
            except Exception as e:
                st.error(f"❌ Hata: {str(e)}")
                st.exception(e)
            finally:
                loop.close()
    
    except Exception as e:
        st.error(f"❌ Dosya okunamadı: {str(e)}")
    
    finally:
        # Geçici dosyayı temizle
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Lütfen bir Excel dosyası yükleyin")

# Footer
st.markdown("---")
st.markdown("💡 **İpucu:** Excel dosyanızın ilk sütununda ürün isimleri olmalıdır.")
