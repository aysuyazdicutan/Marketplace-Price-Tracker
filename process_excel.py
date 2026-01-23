"""
Excel dosyasından ürün isimlerini okuyup Google'da arayan script.
file.xlsx dosyasındaki ilk sütundan (Product Name) satır satır okur ve her ürün için
marketplace'de arama yapar ve fiyat bilgisini çeker.
"""
import pandas as pd
import httpx
import asyncio
import logging
from typing import List, Dict, Tuple, Optional
import os
import json
import re
import random
from urllib.parse import quote, urlparse, parse_qs, unquote
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from config import settings

# Selenium için import'lar (Hepsiburada için gerekli - JavaScript yüklenmesi için)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    USE_SELENIUM = True
except ImportError:
    USE_SELENIUM = False
    logger = logging.getLogger(__name__)
    logger.warning("⚠️  Selenium yüklü değil. Hepsiburada için JavaScript yüklenmesi gerekiyor.")

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # INFO seviyesinde loglar (WARNING ve ERROR da gösterilir)
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Selenium için global driver pool (performans için)
_selenium_driver_pool = None
_selenium_lock = asyncio.Lock()

def get_selenium_driver():
    """Selenium WebDriver oluşturur veya pool'dan alır"""
    global _selenium_driver_pool
    
    if _selenium_driver_pool is None and USE_SELENIUM:
        try:
            chrome_options = Options()
            chrome_options.binary_location = "/usr/bin/chromium"  # Linux ortamında Chromium binary yolu
            chrome_options.add_argument("--headless")  # Arka planda çalış
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), 
                options=chrome_options
            )
            
            # WebDriver özelliğini gizle
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                '''
            })
            
            _selenium_driver_pool = driver
            logger.info("✅ Selenium WebDriver oluşturuldu")
        except Exception as e:
            logger.warning(f"⚠️  Selenium WebDriver oluşturulamadı: {e}")
            return None
    
    return _selenium_driver_pool

def close_selenium_driver():
    """Selenium WebDriver'ı kapatır"""
    global _selenium_driver_pool
    if _selenium_driver_pool:
        try:
            _selenium_driver_pool.quit()
            _selenium_driver_pool = None
            logger.info("✅ Selenium WebDriver kapatıldı")
        except:
            pass

# Bot koruması için curl_cffi veya cloudscraper kullan
USE_CURL_CFFI = False
USE_CLOUDSCRAPER = False
curl_requests = None
cloudscraper = None

try:
    from curl_cffi import requests as curl_requests
    USE_CURL_CFFI = True
    USE_CLOUDSCRAPER = False
    logger.info("✅ curl_cffi yüklü - Bot koruması aşılabilir")
except ImportError as e:
    logger.debug(f"curl_cffi import hatası: {e}")
    try:
        import cloudscraper
        USE_CLOUDSCRAPER = True
        USE_CURL_CFFI = False
        logger.info("✅ cloudscraper yüklü - Bot koruması aşılabilir")
    except ImportError as e2:
        logger.debug(f"cloudscraper import hatası: {e2}")
        USE_CLOUDSCRAPER = False
        USE_CURL_CFFI = False
        logger.warning("⚠️  UYARI: curl_cffi veya cloudscraper yüklü değil. Teknosa için bot koruması aşılamayabilir.")
        logger.warning("⚠️  Yüklemek için: pip install curl-cffi veya pip install cloudscraper")
        logger.warning(f"⚠️  curl_cffi hatası: {type(e).__name__}: {e}")
        logger.warning(f"⚠️  cloudscraper hatası: {type(e2).__name__}: {e2}")
except Exception as e:
    logger.warning(f"curl_cffi import sırasında beklenmeyen hata: {type(e).__name__}: {e}")
    try:
        import cloudscraper
        USE_CLOUDSCRAPER = True
        USE_CURL_CFFI = False
        logger.info("✅ cloudscraper yüklü - Bot koruması aşılabilir")
    except Exception as e2:
        logger.warning(f"cloudscraper import sırasında beklenmeyen hata: {type(e2).__name__}: {e2}")
        USE_CLOUDSCRAPER = False
        USE_CURL_CFFI = False

# Google Custom Search API endpoint
GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# Excel dosya yolu
EXCEL_FILE = "file.xlsx"


async def extract_price_from_trendyol(url: str, max_retries: int = 2) -> Dict[str, any]:
    """
    Trendyol URL'inden fiyat bilgisini çeker. Retry mekanizması ile.
    
    Args:
        url: Trendyol ürün sayfası URL'i
        max_retries: Maksimum deneme sayısı (varsayılan: 2)
    
    Returns:
        Dict containing: price, currency, success, error
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    for attempt in range(max_retries + 1):
        try:
            # Rate limiting için bekleme
            if attempt > 0:
                await asyncio.sleep(1.0)  # Retry'ler arasında daha uzun bekleme
            else:
                await asyncio.sleep(0.3)
            
            # Timeout'u artır (son denemede daha uzun)
            timeout_duration = 25.0 if attempt == max_retries else 15.0
            
            async with httpx.AsyncClient(timeout=timeout_duration, headers=headers, follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=3, max_connections=5)) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                price = None
                currency = 'TRY'
                title = None
                
                # Başlık çekme (fiyat bulunduğunda kullanılacak)
                # Yöntem 1: h1 tag'inden
                h1_tag = soup.find('h1')
                if h1_tag:
                    title = h1_tag.get_text(strip=True)
                
                # Yöntem 2: JSON-LD'den
                if not title:
                    scripts = soup.find_all('script', type='application/ld+json')
                    for script in scripts:
                        try:
                            if script.string:
                                data = json.loads(script.string)
                                if isinstance(data, dict) and 'name' in data:
                                    title = data['name']
                                    break
                        except:
                            continue
                
                # Yöntem 3: Meta tag'lerden
                if not title:
                    meta_title = soup.find('meta', property='og:title')
                    if meta_title:
                        title = meta_title.get('content', '').strip()
                
                # Yöntem 4: Title tag'inden
                if not title:
                    title_tag = soup.find('title')
                    if title_tag:
                        title = title_tag.get_text(strip=True)
                
                # Yöntem 0: Tüm script tag'lerinde window.__INITIAL_STATE__ veya benzeri global değişkenlerde ara
                all_scripts = soup.find_all('script')
                for script in all_scripts:
                    if not script.string:
                        continue
                    script_text = script.string
                    
                    # Trendyol özel: window.__PRODUCT_DETAIL_APP_INITIAL_STATE__ veya benzeri
                    patterns_js = [
                        r'window\.__PRODUCT_DETAIL_APP_INITIAL_STATE__\s*=\s*({[^}]*"price"[^}]*})',
                        r'"price"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"sellingPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"discountedPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"finalPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"currentPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'price["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                        r'sellingPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                        r'discountedPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    ]
                    for pattern in patterns_js:
                        matches = re.finditer(pattern, script_text, re.IGNORECASE)
                        for match in matches:
                            try:
                                price_str = match.group(1).replace(',', '.').replace('.', '', match.group(1).count('.') - 1) if '.' in match.group(1) else match.group(1).replace(',', '.')
                                # Binlik ayırıcıları kaldır
                                if '.' in price_str and ',' in price_str:
                                    # Format: 12.499,25 -> 12499.25
                                    price_str = price_str.replace('.', '').replace(',', '.')
                                elif ',' in price_str:
                                    # Format: 12499,25 -> 12499.25
                                    price_str = price_str.replace(',', '.')
                                price_val = float(price_str)
                                if 1 <= price_val <= 1000000:  # Makul fiyat aralığı
                                    price = price_val
                                    logger.debug(f"Trendyol: JavaScript'ten fiyat bulundu: {price}")
                                    return {
                                        'price': price,
                                        'currency': 'TRY',
                                        'title': title,
                                        'success': True,
                                        'error': None
                                    }
                            except (ValueError, IndexError):
                                continue
            
                # Yöntem 1: Script tag'lerinden JSON-LD veya product data
                scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    if script.string:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            # Schema.org Product formatı
                            if 'offers' in data:
                                offers = data['offers']
                                if isinstance(offers, dict):
                                    if 'price' in offers:
                                        price = float(offers['price'])
                                        currency = offers.get('priceCurrency', 'TRY')
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                                elif isinstance(offers, list) and len(offers) > 0:
                                    if 'price' in offers[0]:
                                        price = float(offers[0]['price'])
                                        currency = offers[0].get('priceCurrency', 'TRY')
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
            
            # Yöntem 2: HTML içinde fiyat class'larını ara (daha kapsamlı)
            # Trendyol'un yaygın fiyat class'ları ve data attribute'ları
            price_selectors = [
                {'class': 'pr-new-br'},  # En yaygın Trendyol fiyat class'ı
                {'class': 'pr-bx-w-dscntd'},
                {'class': 'prc-org'},
                {'class': 'prc-dsc'},
                {'class': 'product-price-container'},
                {'class': re.compile(r'.*price.*', re.I)},
                {'class': re.compile(r'.*prc.*', re.I)},
                {'data-test': re.compile(r'.*price.*', re.I)},
                {'id': re.compile(r'.*price.*', re.I)},
                {'data-testid': re.compile(r'.*price.*', re.I)},
            ]
            
            # Tüm fiyat elementlerini bul (sadece ilkini değil)
            for selector in price_selectors:
                try:
                    price_elements = soup.find_all(**selector)
                    for price_element in price_elements:
                        if not price_element:
                            continue
                        
                        # Fiyat metnini temizle
                        price_text = price_element.get_text(strip=True)
                        if not price_text or len(price_text) < 3:
                            continue
                        
                        # Sadece rakamları al (nokta ve virgül ile)
                        # Türk Lirası formatı: 1.234,56 veya 1234,56 veya 12.499 TL
                        # Önce noktaları kaldır (binlik ayırıcı), virgülü noktaya çevir
                        price_text_clean = price_text.replace('TL', '').replace('₺', '').strip()
                        # Türk formatı: 12.499,25 -> 12499.25
                        price_text_clean = price_text_clean.replace('.', '').replace(',', '.')
                        # Sadece rakam ve nokta bırak
                        price_text_clean = re.sub(r'[^\d.]', '', price_text_clean)
                        
                        # Fiyat pattern'lerini ara (virgüllü veya noktalı)
                        price_patterns = [
                            r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)',  # 12.499,25
                            r'(\d{1,3}(?:\.\d{3})+)',  # 12.499
                            r'(\d+,\d{2})',  # 12499,25
                            r'(\d+\.\d{2})',  # 12499.25
                            r'(\d+)',  # 12499
                        ]
                        
                        for pattern in price_patterns:
                            price_match = re.search(pattern, price_text_clean)
                            if price_match:
                                try:
                                    price_str = price_match.group(1).replace('.', '').replace(',', '.')
                                    price_val = float(price_str)
                                    # Geçerli fiyat aralığı kontrolü
                                    if 1 <= price_val <= 1000000:
                                        logger.debug(f"Trendyol: HTML'den fiyat bulundu: {price_val} (selector: {selector})")
                                        return {
                                            'price': price_val,
                                            'currency': 'TRY',
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                                except (ValueError, AttributeError):
                                    continue
                except Exception as e:
                    logger.debug(f"Selector hatası: {e}")
                    continue
            
            # Yöntem 3: JavaScript içinde daha detaylı fiyat ara (tekrar, ama daha kapsamlı)
            for script in all_scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Daha spesifik pattern'ler
                patterns = [
                    r'"price"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',  # "price":"12.499,25",
                    r'"sellingPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"discountedPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"finalPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"salePrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"currentPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'price["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                    r'sellingPrice["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                ]
                for pattern in patterns:
                    matches = re.finditer(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        try:
                            price_str = match.group(1).replace('.', '').replace(',', '.')
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Trendyol: JS pattern'den fiyat bulundu: {price_val}")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, IndexError):
                            continue
                
                # Fiyat bulunamadıysa retry yap
                if price is None:
                    if attempt < max_retries:
                        logger.warning(f"Fiyat bulunamadı (deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                        continue
                    else:
                        return {
                            'price': None,
                            'currency': None,
                            'success': False,
                            'error': 'Price not found on page'
                        }
                else:
                    # Fiyat bulundu, başarılı dönüş
                    return {
                        'price': price,
                        'currency': currency,
                        'title': title,
                        'success': True,
                        'error': None
                    }
                    
        except httpx.TimeoutException:
            if attempt < max_retries:
                logger.warning(f"Timeout (deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                continue
            else:
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': 'Request timeout after retries'
                }
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Hata (deneme {attempt + 1}/{max_retries + 1}): {str(e)[:50]}, tekrar denenecek...")
                continue
            else:
                logger.warning(f"Fiyat çekme hatası: {str(e)}")
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': str(e)
                }
    
    # Tüm denemeler başarısız
    return {
        'price': None,
        'currency': None,
        'success': False,
        'error': 'All retry attempts failed'
    }


async def extract_price_from_hepsiburada(url: str, max_retries: int = 0) -> Dict[str, any]:
    """
    Hepsiburada URL'inden fiyat bilgisini çeker. 
    JavaScript yüklenmesi gerektiği için Selenium kullanılıyor.
    İlk denemede başarısız olursa hemen geçer (retry yok).
    
    Args:
        url: Hepsiburada ürün sayfası URL'i
        max_retries: Maksimum deneme sayısı (varsayılan: 0 - retry yok)
    
    Returns:
        Dict containing: price, currency, success, error
    """
    # Önce Selenium ile dene (JavaScript yüklenmesi için)
    if USE_SELENIUM:
        try:
            # Selenium'u async wrapper ile kullan
            loop = asyncio.get_event_loop()
            
            def selenium_extract():
                """Selenium ile fiyat çeken sync fonksiyon"""
                driver = get_selenium_driver()
                if not driver:
                    return None, "Selenium driver oluşturulamadı"
                
                try:
                    import time
                    import random
                    from urllib.parse import quote
                    
                    # Timeout'u çok azalt (5 saniye - ilk denemede başarısız olursa hemen geçsin)
                    wait = WebDriverWait(driver, 5)
                    
                    # Sayfaya git
                    driver.get(url)
                    
                    # Sayfanın yüklenmesini bekle (timeout çok kısa - hızlı geçiş için)
                    try:
                        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                    except:
                        # Timeout olursa hemen geç
                        return None, "Sayfa yüklenemedi (timeout)"
                    
                    time.sleep(1)  # Bekleme süresini çok azalt (1 saniye)
                    
                    # Popup'ları kapat
                    try:
                        cookie_selectors = [
                            "button[id*='cookie']",
                            "button[class*='cookie']",
                            "a[class*='cookie']",
                            ".cookie-accept",
                            "#onetrust-accept-btn-handler",
                        ]
                        for selector in cookie_selectors:
                            try:
                                cookie_btn = driver.find_elements(By.CSS_SELECTOR, selector)
                                if cookie_btn and cookie_btn[0].is_displayed():
                                    driver.execute_script("arguments[0].click();", cookie_btn[0])
                                    time.sleep(1)
                                    break
                            except:
                                continue
                    except:
                        pass
                    
                    time.sleep(0.5)  # Beklemeyi azalt
                    driver.execute_script("window.scrollTo(0, 300);")
                    time.sleep(0.5)  # Beklemeyi azalt
                    
                    # Fiyat geçerliliği kontrolü (kullanıcının kodundan)
                    def fiyat_gecerli_mi(text):
                        """Fiyatın geçerli olup olmadığını kontrol et"""
                        if not text or len(text.strip()) < 3:
                            return False
                        
                        # Sadece sayı, nokta, virgül ve TL/₺ içermeli
                        cleaned = re.sub(r'[^\d.,]', '', text)
                        if not cleaned or len(cleaned) < 3:  # En az 3 karakter olmalı (örn: 100)
                            return False
                        
                        # Sadece virgül veya nokta içeriyorsa geçersiz (örn: ",12" veya ".12")
                        if cleaned.replace(',', '').replace('.', '') == '':
                            return False
                        
                        # En az bir rakam olmalı
                        if not any(char.isdigit() for char in cleaned):
                            return False
                        
                        # Fiyat formatını kontrol et (örn: 1.234,56 veya 1234,56 veya 1234)
                        # Eğer sadece virgülle başlıyorsa geçersiz
                        if cleaned.startswith(',') or cleaned.startswith('.'):
                            return False
                        
                        return True
                    
                    # Fiyat selector'ları (kullanıcının kodundan - öncelikli)
                    fiyat_selectors = [
                        "[data-test-id='price-current-price']",
                        "span[data-test-id='price-current-price']",
                        "div[data-test-id='price-current-price']",
                        "[data-test-id='price']",
                        "span[class*='price'][class*='current']",
                        "div[class*='price'][class*='current']",
                        "span[class*='current-price']",
                        "div[class*='current-price']",
                    ]
                    
                    # Önce spesifik fiyat seçicilerini dene
                    for selector in fiyat_selectors:
                        try:
                            fiyat_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            for elem in fiyat_elements:
                                text = elem.text.strip()
                                if fiyat_gecerli_mi(text):
                                    # Fiyatı temizle ve standardize et
                                    price_cleaned = text.replace('TL', '').replace('₺', '').strip()
                                    if '.' in price_cleaned and ',' in price_cleaned:
                                        # Format: 12.499,25 -> 12499.25
                                        price_cleaned = price_cleaned.replace('.', '').replace(',', '.')
                                    elif ',' in price_cleaned:
                                        # Format: 12499,25 -> 12499.25
                                        price_cleaned = price_cleaned.replace(',', '.')
                                    price_cleaned = re.sub(r'[^\d.]', '', price_cleaned)
                                    try:
                                        price_val = float(price_cleaned)
                                        if 1 <= price_val <= 1000000:
                                            return price_val, None
                                    except:
                                        continue
                        except:
                            continue
                    
                    # Eğer hala bulunamadıysa genel seçicileri dene
                    genel_fiyat_selectors = [
                        "span[class*='price']",
                        "div[class*='price']",
                        ".price",
                        ".product-price",
                    ]
                    for selector in genel_fiyat_selectors:
                        try:
                            fiyat_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            for elem in fiyat_elements:
                                text = elem.text.strip()
                                # TL veya ₺ içeriyorsa ve geçerliyse
                                if ('tl' in text.lower() or '₺' in text) and len(text) < 50:
                                    if fiyat_gecerli_mi(text):
                                        price_cleaned = text.replace('TL', '').replace('₺', '').strip()
                                        if '.' in price_cleaned and ',' in price_cleaned:
                                            price_cleaned = price_cleaned.replace('.', '').replace(',', '.')
                                        elif ',' in price_cleaned:
                                            price_cleaned = price_cleaned.replace(',', '.')
                                        price_cleaned = re.sub(r'[^\d.]', '', price_cleaned)
                                        try:
                                            price_val = float(price_cleaned)
                                            if 1 <= price_val <= 1000000:
                                                return price_val, None
                                        except:
                                            continue
                        except:
                            continue
                    
                    # Son çare: regex ile sayfa metninden fiyat çıkar (kullanıcının kodundan)
                    try:
                        page_text = driver.page_source
                        # Fiyat desenini ara (örn: 1.234,56 TL veya 1234 TL veya 1.234,56₺)
                        price_patterns = [
                            r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*(?:TL|₺|tl)',
                            r'(?:TL|₺|tl)\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)',
                            r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*₺',
                        ]
                        for pattern in price_patterns:
                            matches = re.findall(pattern, page_text)
                            if matches:
                                # Geçerli fiyatları filtrele (en az 3 rakam içermeli)
                                valid_prices = []
                                for m in matches:
                                    digits_only = m.replace('.', '').replace(',', '')
                                    if len(digits_only) >= 3 and not m.startswith(',') and not m.startswith('.'):
                                        valid_prices.append(m)
                                
                                if valid_prices:
                                    # En büyük sayıyı al (genelde fiyat en büyük sayıdır)
                                    def get_numeric_value(price_str):
                                        return float(price_str.replace('.', '').replace(',', '.'))
                                    
                                    try:
                                        valid_prices.sort(key=get_numeric_value, reverse=True)
                                        price_val = get_numeric_value(valid_prices[0])
                                        if 1 <= price_val <= 1000000:
                                            return price_val, None
                                    except:
                                        price_val = get_numeric_value(valid_prices[0])
                                        if 1 <= price_val <= 1000000:
                                            return price_val, None
                    except Exception as e:
                        pass  # Son çare yöntemi başarısız olursa sessizce devam et
                    
                    return None, "Fiyat bulunamadı"
                    
                except Exception as e:
                    return None, f"Hata: {str(e)[:50]}"
            
            # Selenium'u async executor'da çalıştır
            price, error = await loop.run_in_executor(None, selenium_extract)
            
            if price:
                logger.debug(f"Hepsiburada: Selenium ile fiyat bulundu: {price}")
                return {
                    'price': price,
                    'currency': 'TRY',
                    'success': True,
                    'error': None
                }
            else:
                logger.warning(f"Hepsiburada: Selenium ile fiyat bulunamadı: {error}, geçiliyor...")
                # İlk denemede başarısız olursa hemen geç (retry yok)
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': f'Selenium ile fiyat bulunamadı: {error}'
                }
        
        except Exception as e:
            logger.warning(f"Hepsiburada: Selenium hatası: {str(e)[:50]}, geçiliyor...")
            # Selenium hatası olursa hemen geç (retry yok)
            return {
                'price': None,
                'currency': None,
                'success': False,
                'error': f'Selenium hatası: {str(e)[:50]}'
            }
    
    # Selenium yoksa veya başarısız olduysa, httpx ile tek deneme yap (retry yok - hızlı geçiş için)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    # Sadece tek deneme (retry yok - ilk denemede başarısız olursa hemen geç)
    try:
        timeout_duration = 15.0  # Timeout'u azalt (hızlı geçiş için)
        
        async with httpx.AsyncClient(
            timeout=timeout_duration, 
            headers=headers, 
            follow_redirects=True, 
            limits=httpx.Limits(max_keepalive_connections=1, max_connections=2)
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            price = None
            currency = 'TRY'
            
            # Yöntem 0: Tüm script tag'lerinde JavaScript global değişkenlerinde ara (Trendyol gibi)
            all_scripts = soup.find_all('script')
            for script in all_scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Hepsiburada özel pattern'ler
                patterns_js = [
                    r'"price"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"finalPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"salePrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"discountedPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"currentPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"offeringPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"listPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'price["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    r'finalPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    r'offeringPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                ]
                for pattern in patterns_js:
                    matches = re.finditer(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        try:
                            price_str = match.group(1)
                            # Binlik ayırıcıları kaldır
                            if '.' in price_str and ',' in price_str:
                                # Format: 12.499,25 -> 12499.25
                                price_str = price_str.replace('.', '').replace(',', '.')
                            elif ',' in price_str:
                                # Format: 12499,25 -> 12499.25
                                price_str = price_str.replace(',', '.')
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                price = price_val
                                logger.debug(f"Hepsiburada: JavaScript'ten fiyat bulundu: {price}")
                                return {
                                    'price': price,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, IndexError):
                            continue
            
            # Yöntem 1: Script tag'lerinden JSON-LD veya product data
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    if script.string:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            # Schema.org Product formatı
                            if 'offers' in data:
                                offers = data['offers']
                                if isinstance(offers, dict):
                                    if 'price' in offers:
                                        price = float(offers['price'])
                                        currency = offers.get('priceCurrency', 'TRY')
                                        logger.debug(f"Hepsiburada: JSON-LD'den fiyat bulundu: {price}")
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                                elif isinstance(offers, list) and len(offers) > 0:
                                    if 'price' in offers[0]:
                                        price = float(offers[0]['price'])
                                        currency = offers[0].get('priceCurrency', 'TRY')
                                        logger.debug(f"Hepsiburada: JSON-LD listesinden fiyat bulundu: {price}")
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
            
            # Yöntem 2: HTML selector'ları (Selenium kodundan gelen selector'lar - öncelikli)
            # Önce spesifik data-test-id selector'larını dene (Hepsiburada'nın kullandığı)
            specific_price_selectors = [
                "[data-test-id='price-current-price']",
                "span[data-test-id='price-current-price']",
                "div[data-test-id='price-current-price']",
                "[data-test-id='price']",
                "span[data-test-id='price']",
                "div[data-test-id='price']",
            ]
            
            # Fiyat geçerliliği kontrolü (Selenium kodundan)
            def is_valid_price(text):
                """Fiyatın geçerli olup olmadığını kontrol et"""
                if not text or len(text.strip()) < 3:
                    return False
                
                # Sadece sayı, nokta, virgül ve TL/₺ içermeli
                cleaned = re.sub(r'[^\d.,]', '', text)
                if not cleaned or len(cleaned) < 3:
                    return False
                
                # Sadece virgül veya nokta içeriyorsa geçersiz
                if cleaned.replace(',', '').replace('.', '') == '':
                    return False
                
                # En az bir rakam olmalı
                if not any(char.isdigit() for char in cleaned):
                    return False
                
                # Virgülle veya noktayla başlıyorsa geçersiz
                if cleaned.startswith(',') or cleaned.startswith('.'):
                    return False
                
                return True
            
            for selector in specific_price_selectors:
                try:
                    price_elements = soup.select(selector)
                    for price_element in price_elements:
                        if not price_element:
                            continue
                        
                        price_text = price_element.get_text(strip=True)
                        if not is_valid_price(price_text):
                            continue
                        
                        # Fiyat temizleme ve parse etme
                        price_cleaned = price_text.replace('TL', '').replace('₺', '').strip()
                        
                        # Türk formatı: 1.234,56 veya 1234,56 veya 12.499 TL
                        if '.' in price_cleaned and ',' in price_cleaned:
                            # Format: 12.499,25 -> 12499.25
                            price_cleaned = price_cleaned.replace('.', '').replace(',', '.')
                        elif ',' in price_cleaned:
                            # Format: 12499,25 -> 12499.25
                            price_cleaned = price_cleaned.replace(',', '.')
                        
                        # Sadece rakam ve nokta bırak
                        price_cleaned = re.sub(r'[^\d.]', '', price_cleaned)
                        
                        try:
                            price_val = float(price_cleaned)
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Hepsiburada: HTML'den fiyat bulundu (data-test-id): {price_val} (selector: {selector})")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, AttributeError):
                            continue
                except Exception as e:
                    logger.debug(f"Selector hatası ({selector}): {e}")
                    continue
            
            # Eğer spesifik selector'lardan bulunamadıysa, genel selector'ları dene
            general_price_selectors = [
                "span[class*='price'][class*='current']",
                "div[class*='price'][class*='current']",
                "span[class*='current-price']",
                "div[class*='current-price']",
                {'id': 'offering-price'},
                {'class': 'product-price'},
                {'class': 'price'},
                {'class': 'price-value'},
                {'class': 'priceNew'},
                {'class': re.compile(r'.*price.*', re.I)},
                {'data-test': re.compile(r'.*price.*', re.I)},
                {'id': re.compile(r'.*price.*', re.I)},
            ]
            
            for selector in general_price_selectors:
                try:
                    if isinstance(selector, str):
                        price_elements = soup.select(selector)
                    else:
                        price_elements = soup.find_all(**selector)
                    
                    for price_element in price_elements:
                        if not price_element:
                            continue
                        
                        price_text = price_element.get_text(strip=True)
                        if not price_text or len(price_text) < 3:
                            continue
                        
                        # TL veya ₺ içermeli ve uzunluğu makul olmalı
                        if ('tl' not in price_text.lower() and '₺' not in price_text) or len(price_text) > 50:
                            continue
                        
                        if not is_valid_price(price_text):
                            continue
                        
                        # Fiyat temizleme
                        price_cleaned = price_text.replace('TL', '').replace('₺', '').strip()
                        
                        # Türk formatı dönüşümü
                        if '.' in price_cleaned and ',' in price_cleaned:
                            price_cleaned = price_cleaned.replace('.', '').replace(',', '.')
                        elif ',' in price_cleaned:
                            price_cleaned = price_cleaned.replace(',', '.')
                        
                        price_cleaned = re.sub(r'[^\d.]', '', price_cleaned)
                        
                        try:
                            price_val = float(price_cleaned)
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Hepsiburada: HTML'den fiyat bulundu (genel): {price_val}")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, AttributeError):
                            continue
                except Exception as e:
                    logger.debug(f"Selector hatası: {e}")
                    continue
            
            # Son çare: Sayfa metninden regex ile fiyat çıkar (Selenium kodundan)
            try:
                page_text = soup.get_text(" ")
                # Fiyat desenini ara (örn: 1.234,56 TL veya 1234 TL veya 1.234,56₺)
                price_patterns = [
                    r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*(?:TL|₺|tl)',
                    r'(?:TL|₺|tl)\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)',
                    r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*₺',
                ]
                
                valid_prices = []
                for pattern in price_patterns:
                    matches = re.findall(pattern, page_text)
                    for m in matches:
                        digits_only = m.replace('.', '').replace(',', '')
                        # En az 3 rakam içermeli ve virgül/noktayla başlamamalı
                        if len(digits_only) >= 3 and not m.startswith(',') and not m.startswith('.'):
                            try:
                                # En büyük sayıyı al (genelde fiyat en büyük sayıdır)
                                numeric_val = float(m.replace('.', '').replace(',', '.'))
                                if 1 <= numeric_val <= 1000000:
                                    valid_prices.append((numeric_val, m))
                            except (ValueError, AttributeError):
                                continue
                
                if valid_prices:
                    # En büyük sayıyı al (fiyat genelde en büyük sayıdır)
                    valid_prices.sort(key=lambda x: x[0], reverse=True)
                    best_price = valid_prices[0][0]
                    logger.debug(f"Hepsiburada: Regex ile fiyat bulundu: {best_price}")
                    return {
                        'price': best_price,
                        'currency': 'TRY',
                        'success': True,
                        'error': None
                    }
            except Exception as e:
                logger.debug(f"Regex fiyat arama hatası: {e}")
            
            # Yöntem 3: JavaScript içinde daha detaylı fiyat ara (tekrar, ama daha kapsamlı)
            for script in all_scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Daha spesifik pattern'ler
                patterns = [
                    r'"price"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',  # "price":"12.499,25",
                    r'"finalPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"salePrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"currentPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"offeringPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'price["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                    r'finalPrice["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                    r'offeringPrice["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                ]
                for pattern in patterns:
                    matches = re.finditer(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        try:
                            price_str = match.group(1).replace('.', '').replace(',', '.')
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Hepsiburada: JS pattern'den fiyat bulundu: {price_val}")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, IndexError):
                            continue
            
            # Fiyat bulunamadıysa hemen geç (retry yok)
            if price is None:
                logger.warning(f"Hepsiburada: Fiyat bulunamadı, geçiliyor...")
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': 'Price not found on page'
                }
    
    except httpx.TimeoutException:
        logger.warning(f"Hepsiburada: Timeout, geçiliyor...")
        return {
            'price': None,
            'currency': None,
            'success': False,
            'error': 'Request timeout'
        }
    except Exception as e:
        logger.warning(f"Hepsiburada: Hata: {str(e)[:50]}, geçiliyor...")
        return {
            'price': None,
            'currency': None,
            'success': False,
            'error': str(e)[:100]
        }


def extract_price(price_text: str):
    """
    Fiyat metnini temizleyip float'a çevirir.
    Türk formatı: 12.499,25 TL -> 12499.25
    """
    if not price_text:
        return None
    
    # Para birimi işaretlerini kaldır
    price_text = price_text.replace('TL', '').replace('₺', '').replace('TRY', '').strip()
    
    # Türk formatı: 12.499,25 -> 12499.25
    # Önce noktaları kaldır (binlik ayırıcı), sonra virgülü noktaya çevir
    if '.' in price_text and ',' in price_text:
        # Format: 12.499,25
        price_text = price_text.replace('.', '').replace(',', '.')
    elif ',' in price_text:
        # Format: 12499,25
        price_text = price_text.replace(',', '.')
    
    # Sadece rakam ve nokta bırak
    price_text = re.sub(r'[^\d.]', '', price_text)
    
    try:
        price = float(price_text)
        # Geçerli fiyat aralığı kontrolü
        if 1 <= price <= 1000000:
            return price
    except (ValueError, AttributeError):
        pass
    
    return None


async def extract_price_from_teknosa(url: str, max_retries: int = 3, proxy: Optional[str] = None) -> Dict[str, any]:
    """
    Teknosa URL'inden fiyat bilgisini çeker. Retry mekanizması ile.
    Öncelik sırası:
      1) HTML price selector'ları
      2) JSON-LD / script içi json
      3) JavaScript pattern'leri
    
    Args:
        url: Teknosa ürün URL'i
        max_retries: Maksimum deneme sayısı (varsayılan: 3)
        proxy: Proxy URL'i (opsiyonel, format: "http://user:pass@host:port" veya "http://host:port")
    """
    # Headers sadece cloudscraper için kullanılacak, curl_cffi impersonate kullanacak
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.teknosa.com/',
    }
    
    # Streamlit Cloud IP engellemesini aşmak için daha agresif ayarlar
    for attempt in range(max_retries + 1):
        try:
            # Her denemede farklı bir süre bekle (1-4 saniye arası)
            if attempt > 0:
                await asyncio.sleep(random.uniform(1.0, 4.0))
            else:
                await asyncio.sleep(random.uniform(0.3, 1.0))
            
            timeout_duration = 25.0 if attempt == max_retries else 15.0
            
            # curl_cffi veya cloudscraper kullan (bot korumasını aşmak için)
            if USE_CURL_CFFI or USE_CLOUDSCRAPER:
                # Sync kütüphaneleri async wrapper ile kullan
                loop = asyncio.get_event_loop()
                
                if USE_CURL_CFFI and curl_requests is not None:
                    # curl_cffi ile browser fingerprint simülasyonu
                    # ÖNEMLİ: Headers'ı curl_cffi'nin kendisine bırakıyoruz (impersonate="chrome110")
                    # Manuel header eklemek bazen parmak izi uyuşmazlığına (403) sebep olur.
                    try:
                        # Proxy desteği için altyapı hazırla
                        request_kwargs = {
                            'url': url,
                            'timeout': int(timeout_duration),
                            'impersonate': 'chrome110'  # Güncel bir browser taklidi
                        }
                        
                        # Proxy varsa ekle
                        if proxy:
                            request_kwargs['proxies'] = {
                                'http': proxy,
                                'https': proxy
                            }
                        
                        response = await loop.run_in_executor(
                            None,
                            lambda: curl_requests.get(**request_kwargs)
                        )
                    except Exception as e:
                        logger.warning(f"curl_cffi hatası: {str(e)[:50]}")
                        if attempt < max_retries:
                            continue
                        else:
                            return {
                                'price': None,
                                'currency': None,
                                'success': False,
                                'error': f'curl_cffi error: {str(e)[:50]}'
                            }
                elif USE_CLOUDSCRAPER and cloudscraper is not None:
                    # cloudscraper ile Cloudflare bypass
                    try:
                        scraper_kwargs = {
                            'browser': {'browser': 'chrome', 'platform': 'darwin', 'desktop': True}
                        }
                        # Proxy desteği
                        if proxy:
                            scraper_kwargs['proxies'] = {
                                'http': proxy,
                                'https': proxy
                            }
                        
                        scraper = cloudscraper.create_scraper(**scraper_kwargs)
                        response = await loop.run_in_executor(
                            None,
                            lambda: scraper.get(url, headers=headers, timeout=int(timeout_duration))
                        )
                    except Exception as e:
                        logger.warning(f"cloudscraper hatası: {str(e)[:50]}")
                        if attempt < max_retries:
                            continue
                        else:
                            return {
                                'price': None,
                                'currency': None,
                                'success': False,
                                'error': f'cloudscraper error: {str(e)[:50]}'
                            }
                
                # Response'u kontrol et
                if hasattr(response, 'status_code'):
                    if response.status_code == 403:
                        if attempt < max_retries:
                            logger.warning(f"Teknosa hala 403 veriyor (Deneme {attempt+1}/{max_retries + 1})")
                            # 403 hatası için daha uzun rastgele bekleme
                            await asyncio.sleep(random.uniform(2.0, 5.0))
                            continue
                        else:
                            return {
                                'price': None,
                                'currency': None,
                                'success': False,
                                'error': '403 Forbidden - Bot protection'
                            }
                    elif response.status_code != 200:
                        if attempt < max_retries:
                            logger.warning(f"HTTP {response.status_code} (deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                            continue
                        else:
                            return {
                                'price': None,
                                'currency': None,
                                'success': False,
                                'error': f'HTTP {response.status_code}'
                            }
                
                # HTML içeriğini al
                if hasattr(response, 'text'):
                    html_content = response.text
                elif hasattr(response, 'content'):
                    html_content = response.content.decode('utf-8', errors='ignore')
                else:
                    if attempt < max_retries:
                        continue
                    else:
                        return {
                            'price': None,
                            'currency': None,
                            'success': False,
                            'error': 'No content in response'
                        }
                
                soup = BeautifulSoup(html_content, 'html.parser')
            else:
                # Normal httpx (403 hatası alabilir)
                async with httpx.AsyncClient(
                    timeout=timeout_duration, 
                    headers=headers, 
                    follow_redirects=True, 
                    limits=httpx.Limits(max_keepalive_connections=3, max_connections=5)
                ) as client:
                    # Asıl ürün sayfasına git (ana sayfayı atla - 403 veriyor)
                    response = await client.get(url)
                    
                    # 403 hatası alırsak, hemen çık (zaman kaybetme)
                    if response.status_code == 403:
                        logger.warning(f"403 Forbidden - Bot koruması aktif, atlanıyor...")
                        return {
                            'price': None,
                            'currency': None,
                            'success': False,
                            'error': '403 Forbidden - Bot protection (curl_cffi veya cloudscraper yükleyin)'
                        }
                    
                    response.raise_for_status()
                    html_content = response.text
                    soup = BeautifulSoup(html_content, 'html.parser')
            
            # Fiyat çekme işlemleri (her iki yöntem için ortak)
            price = None
            
            # Yöntem 0: Attribute'lardan direkt fiyat çek (en güvenilir - ÖNCE BUNU DENE)
            # Teknosa data-product-price, data-price-with-discount kullanıyor
            price_attrs = ['data-product-price', 'data-price-with-discount', 'data-price-without-discount']
            for attr in price_attrs:
                price_elem = soup.find(attrs={attr: True})
                if price_elem:
                    price_str = price_elem.get(attr, '')
                    if price_str:
                        try:
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                price = price_val
                                logger.debug(f"Teknosa: Attribute'dan fiyat bulundu: {price} ({attr})")
                                return {
                                    'price': price,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, TypeError):
                            continue
            
            # Yöntem 0b: Tüm script tag'lerinde JavaScript global değişkenlerinde ara (Trendyol gibi)
            all_scripts = soup.find_all('script')
            for script in all_scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Teknosa özel pattern'ler (Trendyol mantığı ile)
                patterns_js = [
                    r'"price"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"finalPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"salePrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"discountedPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"currentPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"productPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'"sellingPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                    r'price["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    r'finalPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    r'currentPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    r'productPrice["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                ]
                for pattern in patterns_js:
                    matches = re.finditer(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        try:
                            price_str = match.group(1)
                            # Binlik ayırıcıları kaldır
                            if '.' in price_str and ',' in price_str:
                                # Format: 12.499,25 -> 12499.25
                                price_str = price_str.replace('.', '').replace(',', '.')
                            elif ',' in price_str:
                                # Format: 12499,25 -> 12499.25
                                price_str = price_str.replace(',', '.')
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                price = price_val
                                logger.debug(f"Teknosa: JavaScript'ten fiyat bulundu: {price}")
                                return {
                                    'price': price,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, IndexError):
                            continue
            
            # 1) HTML üstünden dene (CSS selector'lar)
            candidate_selectors = [
                '[data-testid*="price"]',
                '.price',
                '.product-price',
                '.prc',
                '.current-price',
                '.sale-price',
                'span[class*="price"]',
                'div[class*="price"]',
                '[class*="product-price"]',
                '[class*="price-current"]',
            ]
            
            for sel in candidate_selectors:
                try:
                    el = soup.select_one(sel)
                    if el:
                        txt = el.get_text(" ", strip=True)
                        price = extract_price(txt)
                        if price:
                            logger.debug(f"Teknosa: HTML'den fiyat bulundu: {price} (selector: {sel})")
                            return {
                                'price': price,
                                'currency': 'TRY',
                                'success': True,
                                'error': None
                            }
                except Exception as e:
                    logger.debug(f"Selector hatası ({sel}): {e}")
                    continue
            
            # 2) Script içindeki JSON'ları tara (Trendyol mantığı ile)
            # 2a) JSON-LD (schema.org) içinde price var mı?
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    if script.string:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            # Schema.org Product formatı
                            if 'offers' in data:
                                offers = data['offers']
                                if isinstance(offers, dict):
                                    if 'price' in offers:
                                        price = float(offers['price'])
                                        currency = offers.get('priceCurrency', 'TRY')
                                        logger.debug(f"Teknosa: JSON-LD'den fiyat bulundu: {price}")
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                                elif isinstance(offers, list) and len(offers) > 0:
                                    if 'price' in offers[0]:
                                        price = float(offers[0]['price'])
                                        currency = offers[0].get('priceCurrency', 'TRY')
                                        logger.debug(f"Teknosa: JSON-LD listesinden fiyat bulundu: {price}")
                                        return {
                                            'price': price,
                                            'currency': currency,
                                            'title': title,
                                            'success': True,
                                            'error': None
                                        }
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
            
            # 2b) Büyük script blob içinde daha kapsamlı fiyat araması (Trendyol gibi)
            for script in all_scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Daha spesifik pattern'ler (Trendyol gibi)
                patterns = [
                    r'"price"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',  # "price":"12.499,25",
                    r'"finalPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"salePrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"currentPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"productPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'"discountedPrice"\s*:\s*"?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)"?,',
                    r'price["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                    r'finalPrice["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                    r'productPrice["\']?\s*:\s*["\']?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)["\']?',
                ]
                for pattern in patterns:
                    matches = re.finditer(pattern, script_text, re.IGNORECASE)
                    for match in matches:
                        try:
                            price_str = match.group(1).replace('.', '').replace(',', '.')
                            price_val = float(price_str)
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Teknosa: JS pattern'den fiyat bulundu: {price_val}")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, IndexError):
                            continue
            
            # Fiyat bulunamadıysa retry yap
            if price is None:
                if attempt < max_retries:
                    logger.warning(f"Fiyat bulunamadı (Teknosa, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                    continue
                else:
                    return {
                        'price': None,
                        'currency': None,
                        'success': False,
                        'error': 'Price not found on page'
                    }
                    
        except httpx.TimeoutException:
            if attempt < max_retries:
                logger.warning(f"Timeout (Teknosa, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                continue
            else:
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': 'Request timeout after retries'
                }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                if attempt < max_retries:
                    logger.warning(f"403 Forbidden (Teknosa, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                    await asyncio.sleep(2.0)  # Daha uzun bekleme
                    continue
                else:
                    return {
                        'price': None,
                        'currency': None,
                        'success': False,
                        'error': '403 Forbidden - Bot protection'
                    }
            else:
                if attempt < max_retries:
                    logger.warning(f"HTTP {e.response.status_code} (Teknosa, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek...")
                    continue
                else:
                    return {
                        'price': None,
                        'currency': None,
                        'success': False,
                        'error': f'HTTP {e.response.status_code}'
                    }
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Hata (Teknosa, deneme {attempt + 1}/{max_retries + 1}): {str(e)[:50]}, tekrar denenecek...")
                continue
            else:
                logger.warning(f"Fiyat çekme hatası (Teknosa): {str(e)}")
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': str(e)
                }
    
    # Tüm denemeler başarısız
    return {
        'price': None,
        'currency': None,
        'success': False,
        'error': 'All retry attempts failed'
    }


async def extract_price_from_amazon(url: str, max_retries: int = 2) -> Dict[str, any]:
    """
    Amazon URL'inden fiyat bilgisini çeker. Retry mekanizması ile.
    Önce Trendyol mantığı gibi HTTP ile deneyecek, başarısız olursa Selenium dener.
    
    Args:
        url: Amazon ürün sayfası URL'i
        max_retries: Maksimum deneme sayısı (varsayılan: 2)
    
    Returns:
        Dict containing: price, currency, success, error
    """
    logger.info(f"📦 Amazon fiyat çekme başladı - URL: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.amazon.com.tr/',
    }
    
    for attempt in range(max_retries + 1):
        try:
            # Rate limiting için bekleme
            if attempt > 0:
                await asyncio.sleep(1.0)
                logger.info(f"🔄 Amazon deneme {attempt + 1}/{max_retries + 1} - URL: {url}")
            else:
                await asyncio.sleep(0.3)
                logger.info(f"🌐 Amazon HTTP isteği gönderiliyor - URL: {url}")
            
            timeout_duration = 25.0 if attempt == max_retries else 15.0
            
            async with httpx.AsyncClient(
                timeout=timeout_duration, 
                headers=headers, 
                follow_redirects=True, 
                limits=httpx.Limits(max_keepalive_connections=3, max_connections=5)
            ) as client:
                response = await client.get(url)
                logger.info(f"📥 Amazon HTTP yanıtı alındı - Status: {response.status_code}, URL: {response.url}")
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                logger.debug(f"📄 Amazon HTML parse edildi - Sayfa başlığı: {soup.title.string if soup.title else 'N/A'}")
                price = None
                currency = 'TRY'
                product_title = None
                
                # Ürün başlığını çıkar
                title_selectors = [
                    '#productTitle',
                    'h1.a-size-large',
                    'h1#title',
                    'span#productTitle',
                    'h1 span',
                ]
                for selector in title_selectors:
                    try:
                        title_elem = soup.select_one(selector)
                        if title_elem:
                            product_title = title_elem.get_text(strip=True)
                            if product_title:
                                logger.debug(f"📦 Amazon ürün başlığı bulundu: {product_title[:80]}...")
                                break
                    except:
                        continue
                
                # Yöntem 0: JavaScript global değişkenlerinden fiyat çek (Trendyol mantığı)
                all_scripts = soup.find_all('script')
                for script in all_scripts:
                    if not script.string:
                        continue
                    script_text = script.string
                    
                    # Amazon özel pattern'ler
                    patterns_js = [
                        r'"price"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"priceAmount"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"displayPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"finalPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"salePrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"currentPrice"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'"amount"\s*:\s*"?(\d+[.,]\d+)"?',
                        r'data-asin-price=["\'](\d+[.,]\d+)["\']',
                        r'price["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                        r'priceAmount["\']?\s*:\s*["\']?(\d+[.,]\d+)',
                    ]
                    for pattern in patterns_js:
                        matches = re.finditer(pattern, script_text, re.IGNORECASE)
                        for match in matches:
                            try:
                                price_str = match.group(1)
                                # Binlik ayırıcıları kaldır
                                if '.' in price_str and ',' in price_str:
                                    price_str = price_str.replace('.', '').replace(',', '.')
                                elif ',' in price_str:
                                    price_str = price_str.replace(',', '.')
                                price_val = float(price_str)
                                if 1 <= price_val <= 1000000:
                                    price = price_val
                                    logger.info(f"✅ Amazon: JavaScript'ten fiyat bulundu: {price} TRY - URL: {url}")
                                    return {
                                        'price': price,
                                        'currency': 'TRY',
                                        'title': product_title,
                                        'success': True,
                                        'error': None
                                    }
                            except (ValueError, IndexError):
                                continue
                
                # Yöntem 1: JSON-LD formatından fiyat çek
                scripts = soup.find_all('script', type='application/ld+json')
                for script in scripts:
                    try:
                        if script.string:
                            data = json.loads(script.string)
                            if isinstance(data, dict):
                                # Schema.org Product formatı
                                if 'offers' in data:
                                    offers = data['offers']
                                    if isinstance(offers, dict):
                                        if 'price' in offers:
                                            price = float(offers['price'])
                                            currency = offers.get('priceCurrency', 'TRY')
                                            logger.debug(f"Amazon: JSON-LD'den fiyat bulundu: {price}")
                                            return {
                                                'price': price,
                                                'currency': currency,
                                                'title': product_title,
                                                'success': True,
                                                'error': None
                                            }
                                    elif isinstance(offers, list) and len(offers) > 0:
                                        if 'price' in offers[0]:
                                            price = float(offers[0]['price'])
                                            currency = offers[0].get('priceCurrency', 'TRY')
                                            logger.debug(f"Amazon: JSON-LD listesinden fiyat bulundu: {price}")
                                            return {
                                                'price': price,
                                                'currency': currency,
                                                'title': product_title,
                                                'success': True,
                                                'error': None
                                            }
                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue
                
                # Yöntem 2: HTML selector'larından fiyat çek (Amazon'un özel selector'ları)
                # Amazon fiyat selector'ları (öncelik sırasına göre)
                amazon_price_selectors = [
                    '#priceblock_ourprice',  # Normal fiyat
                    '#priceblock_dealprice',  # İndirimli fiyat
                    '#priceblock_saleprice',  # Satış fiyatı
                    'span.a-price-whole',  # Tam fiyat kısmı (örn: "1.234")
                    'span.a-price[data-a-color="base"] span.a-offscreen',  # Gizli fiyat
                    '.a-price .a-offscreen',  # Genel gizli fiyat
                    'span[data-asin-price]',  # Data attribute
                ]
                
                # Önce spesifik selector'ları dene
                for selector in amazon_price_selectors:
                    try:
                        price_elements = soup.select(selector)
                        for price_element in price_elements:
                            if not price_element:
                                continue
                            
                            # Data attribute varsa onu al
                            if 'data-asin-price' in price_element.attrs:
                                try:
                                    price_val = float(price_element['data-asin-price'])
                                    if 1 <= price_val <= 1000000:
                                        logger.debug(f"Amazon: Data attribute'dan fiyat bulundu: {price_val} (selector: {selector})")
                                        return {
                                            'price': price_val,
                                            'currency': 'TRY',
                                            'title': product_title,
                                            'success': True,
                                            'error': None
                                        }
                                except (ValueError, KeyError):
                                    pass
                            
                            # Text içeriğinden fiyat çıkar
                            price_text = price_element.get_text(strip=True)
                            if not price_text or len(price_text) < 3:
                                continue
                            
                            # Fiyat temizleme (TL, ₺, TL sembolleri vb. kaldır)
                            price_cleaned = price_text.replace('TL', '').replace('₺', '').replace('TRY', '').replace('$', '').strip()
                            
                            # Türk formatı: 1.234,56 veya 1234,56 veya 12.499 TL
                            if '.' in price_cleaned and ',' in price_cleaned:
                                # Format: 12.499,25 -> 12499.25
                                price_cleaned = price_cleaned.replace('.', '').replace(',', '.')
                            elif ',' in price_cleaned:
                                # Format: 12499,25 -> 12499.25
                                price_cleaned = price_cleaned.replace(',', '.')
                            
                            # Sadece rakam ve nokta bırak
                            price_cleaned = re.sub(r'[^\d.]', '', price_cleaned)
                            
                            try:
                                price_val = float(price_cleaned)
                                if 1 <= price_val <= 1000000:
                                    logger.debug(f"Amazon: HTML'den fiyat bulundu: {price_val} (selector: {selector})")
                                    return {
                                        'price': price_val,
                                        'currency': 'TRY',
                                        'title': product_title,
                                        'success': True,
                                        'error': None
                                    }
                            except (ValueError, AttributeError):
                                continue
                    except Exception as e:
                        logger.debug(f"Selector hatası ({selector}): {e}")
                        continue
                
                # Yöntem 3: .a-price-whole ve .a-price-fraction kombinasyonu (Amazon özel)
                try:
                    price_whole_elem = soup.select_one('span.a-price-whole')
                    price_fraction_elem = soup.select_one('span.a-price-fraction')
                    
                    if price_whole_elem and price_fraction_elem:
                        whole_text = price_whole_elem.get_text(strip=True).replace('.', '').replace(',', '')
                        fraction_text = price_fraction_elem.get_text(strip=True)
                        
                        try:
                            whole_val = float(whole_text) if whole_text else 0
                            fraction_val = float(fraction_text) / (10 ** len(fraction_text)) if fraction_text else 0
                            price_val = whole_val + fraction_val
                            
                            if 1 <= price_val <= 1000000:
                                logger.debug(f"Amazon: Whole+Fraction'dan fiyat bulundu: {price_val}")
                                return {
                                    'price': price_val,
                                    'currency': 'TRY',
                                    'title': product_title,
                                    'success': True,
                                    'error': None
                                }
                        except (ValueError, AttributeError):
                            pass
                except Exception as e:
                    logger.debug(f"Whole+Fraction hatası: {e}")
                
                # Yöntem 4: Genel fiyat selector'ları
                general_selectors = [
                    '.a-price',
                    '.a-color-price',
                    '[class*="price"]',
                    '[id*="price"]',
                ]
                
                for selector in general_selectors:
                    try:
                        price_elements = soup.select(selector)
                        for price_element in price_elements:
                            price_text = price_element.get_text(strip=True)
                            if not price_text or len(price_text) < 3:
                                continue
                            
                            # TL veya ₺ içermeli
                            if 'tl' not in price_text.lower() and '₺' not in price_text and '$' not in price_text:
                                continue
                            
                            # Fiyat temizleme
                            price_cleaned = extract_price(price_text)
                            if price_cleaned:
                                logger.debug(f"Amazon: Genel selector'dan fiyat bulundu: {price_cleaned} (selector: {selector})")
                                return {
                                    'price': price_cleaned,
                                    'currency': 'TRY',
                                    'title': product_title,
                                    'success': True,
                                    'error': None
                                }
                    except Exception as e:
                        logger.debug(f"Genel selector hatası ({selector}): {e}")
                        continue
                
                # HTTP ile fiyat bulunamadıysa, Selenium ile deneyelim
                if attempt == max_retries and USE_SELENIUM:
                    logger.info("Amazon: HTTP ile fiyat bulunamadı, Selenium ile deneniyor...")
                    try:
                        loop = asyncio.get_event_loop()
                        
                        def selenium_extract_amazon():
                            """Selenium ile Amazon fiyat çeken sync fonksiyon"""
                            driver = get_selenium_driver()
                            if not driver:
                                return None, None, "Selenium driver oluşturulamadı"
                            
                            selenium_title = None
                            try:
                                import time
                                import random
                                
                                wait = WebDriverWait(driver, 20)
                                
                                # Sayfaya git
                                driver.get(url)
                                
                                # Sayfanın yüklenmesini bekle
                                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                                time.sleep(3 + random.uniform(1, 2))
                                
                                # Popup'ları kapat
                                try:
                                    cookie_selectors = [
                                        "button[id*='cookie']",
                                        "button[class*='cookie']",
                                        "#sp-cc-accept",
                                        "#accept",
                                    ]
                                    for selector in cookie_selectors:
                                        try:
                                            cookie_btn = driver.find_elements(By.CSS_SELECTOR, selector)
                                            if cookie_btn and cookie_btn[0].is_displayed():
                                                driver.execute_script("arguments[0].click();", cookie_btn[0])
                                                time.sleep(1)
                                                break
                                        except:
                                            continue
                                except:
                                    pass
                                
                                time.sleep(1)
                                driver.execute_script("window.scrollTo(0, 300);")
                                time.sleep(1)
                                
                                # Amazon fiyat selector'larını dene
                                selenium_selectors = [
                                    "#priceblock_ourprice",
                                    "#priceblock_dealprice",
                                    "#priceblock_saleprice",
                                    "span.a-price-whole",
                                    "span[data-asin-price]",
                                    ".a-price .a-offscreen",
                                ]
                                
                                for selector in selenium_selectors:
                                    try:
                                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                                        for elem in elements:
                                            # Data attribute varsa onu al
                                            try:
                                                if 'data-asin-price' in elem.get_attribute('outerHTML'):
                                                    price_attr = elem.get_attribute('data-asin-price')
                                                    if price_attr:
                                                        try:
                                                            price_val = float(price_attr.replace(',', '.'))
                                                            if 1 <= price_val <= 1000000:
                                                                # Başlık çıkar
                                                                try:
                                                                    selenium_title = driver.find_element(By.CSS_SELECTOR, "#productTitle, h1#title, span#productTitle").text.strip()
                                                                except:
                                                                    pass
                                                                return price_val, selenium_title, None
                                                        except:
                                                            pass
                                            except:
                                                pass
                                            
                                            # Text içeriğinden fiyat çıkar
                                            text = elem.text.strip()
                                            if text:
                                                price_cleaned = extract_price(text)
                                                if price_cleaned:
                                                    # Başlık çıkar
                                                    try:
                                                        selenium_title = driver.find_element(By.CSS_SELECTOR, "#productTitle, h1#title, span#productTitle").text.strip()
                                                    except:
                                                        pass
                                                    return price_cleaned, selenium_title, None
                                    except:
                                        continue
                                
                                # Whole + Fraction kombinasyonu
                                try:
                                    whole_elem = driver.find_element(By.CSS_SELECTOR, "span.a-price-whole")
                                    fraction_elem = driver.find_element(By.CSS_SELECTOR, "span.a-price-fraction")
                                    if whole_elem and fraction_elem:
                                        whole_text = whole_elem.text.strip().replace('.', '').replace(',', '')
                                        fraction_text = fraction_elem.text.strip()
                                        try:
                                            whole_val = float(whole_text) if whole_text else 0
                                            fraction_val = float(fraction_text) / (10 ** len(fraction_text)) if fraction_text else 0
                                            price_val = whole_val + fraction_val
                                            if 1 <= price_val <= 1000000:
                                                # Başlık çıkar
                                                try:
                                                    selenium_title = driver.find_element(By.CSS_SELECTOR, "#productTitle, h1#title, span#productTitle").text.strip()
                                                except:
                                                    pass
                                                return price_val, selenium_title, None
                                        except:
                                            pass
                                except:
                                    pass
                                
                                # Başlık çıkar (fiyat bulunamasa bile)
                                try:
                                    selenium_title = driver.find_element(By.CSS_SELECTOR, "#productTitle, h1#title, span#productTitle").text.strip()
                                except:
                                    pass
                                return None, selenium_title, "Selenium ile fiyat bulunamadı"
                                
                            except Exception as e:
                                return None, None, f"Selenium hatası: {str(e)[:50]}"
                        
                        # Selenium'u async executor'da çalıştır
                        selenium_price, selenium_title, selenium_error = await loop.run_in_executor(None, selenium_extract_amazon)
                        
                        if selenium_price:
                            logger.info(f"Amazon: Selenium ile fiyat bulundu: {selenium_price}")
                            if selenium_title:
                                product_title = selenium_title
                            return {
                                'price': selenium_price,
                                'currency': 'TRY',
                                'title': product_title,
                                'success': True,
                                'error': None
                            }
                        else:
                            logger.warning(f"Amazon: Selenium ile de fiyat bulunamadı: {selenium_error}")
                    except Exception as e:
                        logger.warning(f"Amazon: Selenium denemesi başarısız: {str(e)[:50]}")
                
                # Fiyat bulunamadıysa retry yap
                if price is None:
                    if attempt < max_retries:
                        logger.warning(f"⚠️ Fiyat bulunamadı (Amazon, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek... - URL: {url}")
                        continue
                    else:
                        logger.warning(f"❌ Amazon: Tüm yöntemler denendi, fiyat bulunamadı - URL: {url}")
                        logger.warning(f"❌ Amazon: Sayfa başlığı: {soup.title.string if soup.title else 'N/A'}")
                        return {
                            'price': None,
                            'currency': None,
                            'success': False,
                            'error': 'Price not found on page'
                        }
                
        except httpx.TimeoutException:
            if attempt < max_retries:
                logger.warning(f"⏱️ Timeout (Amazon, deneme {attempt + 1}/{max_retries + 1}), tekrar denenecek... - URL: {url}")
                continue
            else:
                logger.error(f"❌ Amazon: Timeout hatası - URL: {url}")
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': 'Request timeout after retries'
                }
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"⚠️ Hata (Amazon, deneme {attempt + 1}/{max_retries + 1}): {str(e)[:50]}, tekrar denenecek... - URL: {url}")
                continue
            else:
                logger.error(f"❌ Amazon: Fiyat çekme hatası: {str(e)} - URL: {url}")
                import traceback
                logger.error(f"❌ Amazon: Traceback: {traceback.format_exc()}")
                return {
                    'price': None,
                    'currency': None,
                    'success': False,
                    'error': str(e)
                }
    
    # Tüm denemeler başarısız
    return {
        'price': None,
        'currency': None,
        'success': False,
        'error': 'All retry attempts failed'
    }


def calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate similarity between two product names focusing on:
    - Brand (marka)
    - Model number (model numarası)
    - Product type/series (ürün tipi/serisi)
    - Color (renk)
    
    Filters out technical details and descriptions that can cause confusion.
    
    Args:
        text1: First text (product name from Excel)
        text2: Second text (product title from Amazon)
    
    Returns:
        Similarity score between 0 and 1
    """
    if not text1 or not text2:
        return 0.0
    
    # Normalize texts: lowercase, remove extra spaces
    text1_norm = re.sub(r'\s+', ' ', text1.lower().strip())
    text2_norm = re.sub(r'\s+', ' ', text2.lower().strip())
    
    # Gereksiz kelimeleri filtrele (teknik detaylar, açıklamalar)
    # Bu kelimeler benzerlik hesaplamasına dahil edilmeyecek
    filter_words = {
        'ips', 'intel', 'core', 'amd', 'ryzen', 'nvidia', 'geforce', 'ram', 'gb', 'tb',
        'ssd', 'hdd', 'wifi', 'bluetooth', 'usb', 'hdmi', 'vga', 'dvi', 'displayport',
        'garantili', 'ithalatçı', 'ithalatci', 'garanti', 'yeni', 'orijinal', 'orijinal',
        'faturalı', 'faturasız', 'kutusunda', 'kutusuz', 'açık', 'kapalı',
        'the', 'and', 'or', 'for', 'with', 've', 'ile', 'veya', 'için',
        'akıllı', 'smart', 'otomatik', 'automatic', 'yüksek', 'high', 'düşük', 'low',
        'güçlü', 'powerful', 'hızlı', 'fast', 'yavaş', 'slow'
    }
    
    # Kelimeleri ayır ve filtrele
    words1_all = text1_norm.split()
    words2_all = text2_norm.split()
    
    # Sadece önemli kelimeleri al (filtre kelimelerini çıkar)
    words1_important = {w for w in words1_all if w not in filter_words and len(w) >= 2}
    words2_important = {w for w in words2_all if w not in filter_words and len(w) >= 2}
    
    if not words1_important or not words2_important:
        # Eğer tüm kelimeler filtrelendiyse, filtrelemeden hesapla
        words1_important = set(words1_all)
        words2_important = set(words2_all)
    
    # 1. MARKA (Brand) - İlk kelime genelde marka
    brand1 = words1_all[0] if words1_all else ""
    brand2 = words2_all[0] if words2_all else ""
    brand_match = 1.0 if brand1 == brand2 else 0.0
    if not brand_match and brand1 and brand2:
        # Marka benzerliği kontrolü (ör: acer vs acer)
        brand_similarity = SequenceMatcher(None, brand1, brand2).ratio()
        brand_match = brand_similarity if brand_similarity > 0.8 else 0.0
    
    # 2. MODEL NUMARASI (Model Number) - Nokta, tire, harf-sayı kombinasyonları
    # Pattern: NX.J23EY.001, AL16-52P-55S2, B07V3NBJC3, G7X Mark III
    model_pattern = r'\b([A-Z0-9]+[.-][A-Z0-9]+[.-]?[A-Z0-9]*|[A-Z][0-9]+[A-Z]+[0-9]*|[A-Z]{2,}[0-9]+[A-Z0-9-]*)\b'
    models1_raw = re.findall(model_pattern, text1, re.IGNORECASE)
    models2_raw = re.findall(model_pattern, text2, re.IGNORECASE)
    
    # Model numaralarını normalize et ve filtrele
    models1 = {re.sub(r'[^A-Z0-9]', '', m.upper()) for m in models1_raw if len(m) >= 3}
    models2 = {re.sub(r'[^A-Z0-9]', '', m.upper()) for m in models2_raw if len(m) >= 3}
    
    # Tam eşleşme
    common_models = models1.intersection(models2)
    missing_models = models1 - models2
    
    # Kısmi model eşleşmesi (örn: NXJ23EY001 vs NXJ23EY001A002)
    partial_model_match = False
    partial_model_score = 0.0
    if models1 and models2:
        for model1 in models1:
            for model2 in models2:
                # Bir model diğerinin içinde geçiyorsa
                if model1 in model2 or model2 in model1:
                    match_length = min(len(model1), len(model2))
                    longer_length = max(len(model1), len(model2))
                    if longer_length > 0:
                        match_ratio = match_length / longer_length
                        if match_ratio >= 0.6:  # %60+ eşleşme
                            partial_model_match = True
                            partial_model_score = max(partial_model_score, match_ratio)
    
    model_score = 0.0
    if common_models:
        # Tam eşleşme varsa yüksek skor
        model_score = 0.5 + (len(common_models) * 0.2)
        if not missing_models:
            model_score = 1.0  # Tüm modeller eşleşiyorsa maksimum
    elif partial_model_match:
        # Kısmi eşleşme varsa orta skor
        model_score = 0.3 + (partial_model_score * 0.3)
    elif missing_models:
        # Model yoksa veya farklıysa ceza
        model_score = -0.2
    
    # 3. RENK (Color) - Renk kelimeleri
    colors = {'beyaz', 'white', 'siyah', 'black', 'kırmızı', 'red', 'mavi', 'blue',
              'yeşil', 'green', 'sarı', 'yellow', 'gri', 'gray', 'grey', 'pembe', 'pink',
              'mor', 'purple', 'turuncu', 'orange', 'kahverengi', 'brown', 'altın', 'gold',
              'gümüş', 'silver', 'platin', 'platinum'}
    
    colors1 = {w for w in words1_important if w in colors}
    colors2 = {w for w in words2_important if w in colors}
    color_match = 1.0 if colors1 == colors2 else (0.5 if colors1 and colors2 and colors1.intersection(colors2) else 0.0)
    if not colors1 and not colors2:
        color_match = 1.0  # Renk belirtilmemişse eşleşme sayılır
    
    # 4. ÜRÜN TİPİ/SERİSİ (Product Type/Series) - Markadan sonraki önemli kelimeler
    # İlk 2-4 kelimeyi al (marka + seri/tip)
    series1 = ' '.join(words1_all[:min(4, len(words1_all))])
    series2 = ' '.join(words2_all[:min(4, len(words2_all))])
    series_similarity = SequenceMatcher(None, series1, series2).ratio()
    
    # Önemli kelimelerin eşleşmesi (filtrelenmiş kelimelerden)
    common_important = words1_important.intersection(words2_important)
    important_ratio = len(common_important) / max(len(words1_important), len(words2_important)) if words1_important or words2_important else 0.0
    
    # Skor hesaplama - önemli bilgilere ağırlık ver
    similarity = (
        brand_match * 0.30 +           # Marka: %30
        model_score * 0.40 +           # Model: %40 (en önemli)
        color_match * 0.10 +           # Renk: %10
        series_similarity * 0.15 +     # Seri/Tip: %15
        important_ratio * 0.05          # Diğer önemli kelimeler: %5
    )
    
    # Model eşleşmesi varsa minimum skor garantisi
    if common_models or partial_model_match:
        similarity = max(similarity, 0.5)  # En az %50
    
    # Marka eşleşiyorsa ve model eşleşiyorsa yüksek skor garantisi
    if brand_match > 0.8 and (common_models or partial_model_match):
        similarity = max(similarity, 0.7)  # En az %70
    
    return max(0.0, min(1.0, similarity))


async def get_amazon_price_and_title_by_ean(ean: str, country: str = "tr") -> Tuple[Optional[str], Optional[str]]:
    """
    Search Amazon for a product by EAN code and extract price and title.
    
    Args:
        ean: EAN code to search for
        country: Amazon country code (default: "tr")
    
    Returns:
        Tuple of (price, title) or (None, None) if not found
    """
    if pd.isna(ean):
        return None, None
    
    ean = str(ean).strip()
    if '.' in ean:
        ean = ean.split('.')[0]
    
    if not ean or ean.lower() in ['nan', 'none', '']:
        return None, None
    
    base_urls = {
        "tr": "https://www.amazon.com.tr",
        "com": "https://www.amazon.com",
        "de": "https://www.amazon.de",
        "uk": "https://www.amazon.co.uk",
        "fr": "https://www.amazon.fr",
    }
    
    base_url = base_urls.get(country, "https://www.amazon.com.tr")
    search_url = f"{base_url}/s?k={ean}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the first product result
            product_selectors = [
                'div[data-component-type="s-search-result"]',
                'div.s-result-item',
                'div[data-asin]',
            ]
            
            product_div = None
            for selector in product_selectors:
                products = soup.select(selector)
                if products:
                    product_div = products[0]
                    break
            
            if not product_div:
                # Try alternative approach
                product_links = soup.select('h2 a.a-link-normal, h2 a.a-text-normal')
                if product_links:
                    product_title = product_links[0].get_text(strip=True)
                    # Try to get price from product page
                    product_link = product_links[0].get('href', '')
                    if product_link and not product_link.startswith('http'):
                        product_link = base_url + product_link
                    
                    # Get price from product page
                    if product_link:
                        try:
                            product_response = await client.get(product_link)
                            product_soup = BeautifulSoup(product_response.text, 'html.parser')
                            
                            # Try to get price
                            price_elem = product_soup.select_one('#priceblock_dealprice, #priceblock_ourprice, .a-price .a-offscreen')
                            if price_elem:
                                price_text = price_elem.get_text(strip=True)
                                price_text = re.sub(r'[^\d,.]', '', price_text)
                                if price_text:
                                    return price_text, product_title
                        except:
                            pass
                    return None, product_title
            
            # Extract title
            title_selectors = [
                'h2 a.a-link-normal span',
                'h2 a.a-text-normal span',
                'h2 span',
                'h2 a',
            ]
            
            product_title = None
            for selector in title_selectors:
                title_elem = product_div.select_one(selector)
                if title_elem:
                    product_title = title_elem.get_text(strip=True)
                    break
            
            # Extract price using extract_price helper function
            price_selectors = [
                'span.a-price-whole',
                'span.a-price .a-offscreen',
                'span.a-price span[aria-hidden="true"]',
                '.a-price .a-offscreen',
                'span[data-a-color="price"]',
            ]
            
            product_price = None
            for selector in price_selectors:
                price_elem = product_div.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    # Use extract_price helper to properly parse Turkish format
                    price_float = extract_price(price_text)
                    if price_float:
                        # Convert back to string format for return (Turkish format: 60.999,00)
                        # But return as float for now, will be handled in search_amazon_direct
                        product_price = str(price_float)
                        break
            
            # If no price found, try product page
            if product_title and not product_price:
                link_elem = product_div.select_one('h2 a')
                if link_elem:
                    product_link = link_elem.get('href', '')
                    if product_link and not product_link.startswith('http'):
                        product_link = base_url + product_link
                    
                    try:
                        product_response = await client.get(product_link)
                        product_soup = BeautifulSoup(product_response.text, 'html.parser')
                        
                        price_elem = product_soup.select_one('#priceblock_dealprice, #priceblock_ourprice, .a-price .a-offscreen')
                        if price_elem:
                            price_text = price_elem.get_text(strip=True)
                            price_float = extract_price(price_text)
                            if price_float:
                                product_price = str(price_float)
                    except:
                        pass
            
            return product_price, product_title
            
    except Exception as e:
        logger.debug(f"Error fetching data for EAN {ean}: {e}")
        return None, None


async def get_amazon_search_results_by_name(product_name: str, country: str = "tr", max_results: int = 20) -> List[Tuple[Optional[str], str]]:
    """
    Search Amazon by product name and return list of (price, title) tuples.
    
    Args:
        product_name: Product name to search for
        country: Amazon country code
        max_results: Maximum number of results to return
    
    Returns:
        List of tuples (price, title) for each search result
    """
    if not product_name or pd.isna(product_name):
        return []
    
    product_name = str(product_name).strip()
    if not product_name:
        return []
    
    base_urls = {
        "tr": "https://www.amazon.com.tr",
        "com": "https://www.amazon.com",
        "de": "https://www.amazon.de",
        "uk": "https://www.amazon.co.uk",
        "fr": "https://www.amazon.fr",
    }
    
    base_url = base_urls.get(country, "https://www.amazon.com.tr")
    search_query = quote(product_name)
    search_url = f"{base_url}/s?k={search_query}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    results = []
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            product_selectors = [
                'div[data-component-type="s-search-result"]',
                'div.s-result-item',
                'div[data-asin]',
            ]
            
            product_divs = []
            for selector in product_selectors:
                products = soup.select(selector)
                if products:
                    product_divs = products[:max_results]
                    break
            
            for product_div in product_divs:
                product_title = None
                product_price = None
                
                # Extract title
                title_selectors = [
                    'h2 a.a-link-normal span',
                    'h2 a.a-text-normal span',
                    'h2 span',
                    'h2 a',
                ]
                for selector in title_selectors:
                    title_elem = product_div.select_one(selector)
                    if title_elem:
                        product_title = title_elem.get_text(strip=True)
                        break
                
                # Extract price
                price_selectors = [
                    'span.a-price-whole',
                    'span.a-price .a-offscreen',
                    'span.a-price span[aria-hidden="true"]',
                    '.a-price .a-offscreen',
                    'span[data-a-color="price"]',
                    '.a-price',
                ]
                
                for selector in price_selectors:
                    price_elem = product_div.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text(strip=True)
                        # Use extract_price helper to properly parse Turkish format (60.999,00 -> 60999.0)
                        price_float = extract_price(price_text)
                        if price_float:
                            product_price = str(price_float)
                            break
                        # Fallback: try regex pattern match for Turkish format
                        price_match = re.search(r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)', price_text)
                        if price_match:
                            match_text = price_match.group(1)
                            price_float = extract_price(match_text)
                            if price_float:
                                product_price = str(price_float)
                                break
                
                # If no price, try entire div
                if not product_price:
                    all_text = product_div.get_text()
                    # First try with extract_price on all text
                    price_float = extract_price(all_text)
                    if price_float:
                        product_price = str(price_float)
                    else:
                        # Fallback: try regex pattern match
                        price_matches = re.findall(r'(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)', all_text)
                        if price_matches:
                            valid_prices = []
                            for match in price_matches:
                                price_float = extract_price(match)
                                if price_float and price_float >= 100:
                                    valid_prices.append((price_float, match))
                            if valid_prices:
                                valid_prices.sort(reverse=True, key=lambda x: x[0])
                                product_price = str(valid_prices[0][0])
                
                if product_title:
                    results.append((product_price, product_title))
        
        return results
        
    except Exception as e:
        logger.debug(f"Error searching by name '{product_name}': {e}")
        return []


async def find_best_match_by_name(product_name: str, country: str = "tr") -> Tuple[Optional[str], Optional[str]]:
    """
    Search Amazon by product name and return the best matching product's price and title.
    Uses similarity scoring to find the best match among all results.
    
    Args:
        product_name: Product name to search for
        country: Amazon country code
    
    Returns:
        Tuple of (price, title) for the best matching product
    """
    if not product_name or pd.isna(product_name):
        return None, None
    
    # Get search results
    results = await get_amazon_search_results_by_name(product_name, country, 20)
    
    if not results:
        return None, None
    
    if len(results) == 1:
        return results[0]
    
    # Find the best match by comparing similarity scores
    scored_candidates = []
    for price, title in results:
        similarity_score = calculate_similarity(product_name, title)
        scored_candidates.append((similarity_score, price, title))
    
    # Sort by score descending
    scored_candidates.sort(reverse=True, key=lambda x: x[0])
    
    if scored_candidates:
        best_score, best_price, best_title = scored_candidates[0]
        logger.debug(f"✅ En iyi eşleşme bulundu (similarity: {best_score:.2f}): {best_title[:60]}...")
        return (best_price, best_title)
    
    return None, None


async def search_amazon_direct(product_name: str, ean: str = None) -> Dict[str, any]:
    """
    Amazon'un kendi arama sayfasında direkt arama yapar.
    Önce EAN ile (varsa), sonra ürün adı ile dener ve benzerlik skoruna göre en iyi eşleşmeyi bulur.
    
    Args:
        product_name: Ürün adı
        ean: EAN kodu (opsiyonel)
    
    Returns:
        Dict containing: url, price, title, success, error
    """
    # Önce EAN ile ara (varsa)
    if ean and pd.notna(ean):
        logger.info(f"🔍 Amazon direkt arama (EAN): {ean}")
        price, title = await get_amazon_price_and_title_by_ean(ean, "tr")
        
        # EAN araması başarılıysa (title var ve "sonuç bulunamadı" içermiyorsa)
        if title and "arama sorgunuz için sonuç bulunamadı" not in title.lower() and "no results found" not in title.lower() and "sonuç bulunamadı" not in title.lower():
            # Fiyatı extract_price ile parse et (Türk formatını doğru işler: 60.999,00 -> 60999.0)
            price_float = None
            if price:
                # price zaten extract_price ile parse edilmiş string formatında olabilir
                try:
                    price_float = float(price)
                except (ValueError, TypeError):
                    # Eğer string ise extract_price ile parse et
                    price_float = extract_price(str(price))
                
                if price_float:
                    logger.debug(f"✅ Amazon'da bulundu (EAN): {title[:60]}... - {price_float:.2f} TRY")
                    
                    # URL'i bulmak için ürün adı ile tekrar ara
                    base_url = "https://www.amazon.com.tr"
                    search_query = quote(title[:100])  # Title'un ilk 100 karakteri
                    search_url = f"{base_url}/s?k={search_query}"
                    
                    # Basit bir URL bulma denemesi
                    try:
                        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                            response = await client.get(search_url)
                            soup = BeautifulSoup(response.text, 'html.parser')
                            link_elem = soup.select_one('h2 a.a-link-normal, h2 a.a-text-normal')
                            if link_elem:
                                product_url = link_elem.get('href', '')
                                if product_url and not product_url.startswith('http'):
                                    product_url = base_url + product_url
                            else:
                                product_url = None
                    except:
                        product_url = None
                    
                    return {
                        'url': product_url,
                        'price': price_float,
                        'currency': 'TRY',
                        'title': title,
                        'success': True,
                        'error': None
                    }
    
    # EAN ile bulunamadıysa veya EAN yoksa, ürün adı ile ara ve en iyi eşleşmeyi bul
    if product_name:
        logger.info(f"🔍 Amazon direkt arama (İsim): {product_name}")
        price, title = await find_best_match_by_name(product_name, "tr")
        
        if title:
            # Fiyatı extract_price ile parse et (Türk formatını doğru işler: 60.999,00 -> 60999.0)
            price_float = None
            if price:
                # price zaten extract_price ile parse edilmiş string formatında olabilir
                try:
                    price_float = float(price)
                except (ValueError, TypeError):
                    # Eğer string ise extract_price ile parse et
                    price_float = extract_price(str(price))
            
            logger.debug(f"✅ Amazon'da bulundu (İsim): {title[:60]}... - {price_float:.2f} TRY" if price_float else f"✅ Amazon'da bulundu (İsim, fiyat yok): {title[:60]}...")
            
            # URL'i bulmak için ürün adı ile tekrar ara
            base_url = "https://www.amazon.com.tr"
            search_query = quote(product_name)
            search_url = f"{base_url}/s?k={search_query}"
            
            product_url = None
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    response = await client.get(search_url)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    link_elem = soup.select_one('h2 a.a-link-normal, h2 a.a-text-normal')
                    if link_elem:
                        product_url = link_elem.get('href', '')
                        if product_url and not product_url.startswith('http'):
                            product_url = base_url + product_url
            except:
                pass
            
            return {
                'url': product_url,
                'price': price_float,
                'currency': 'TRY',
                'title': title,
                'success': True,
                'error': None
            }
    
    return {
        'url': None,
        'price': None,
        'currency': None,
        'title': None,
        'success': False,
        'error': 'Amazon direkt arama başarısız'
    }


async def search_marketplace_direct(product_name: str, marketplace: str, ean: str = None) -> Dict[str, any]:
    """
    Marketplace içinde direkt arama yapar (mediamarktproj klasöründeki kodları kullanarak).
    
    Args:
        product_name: Ürün adı
        marketplace: Marketplace adı
        ean: EAN kodu (opsiyonel)
    
    Returns:
        Dict containing: url, price, currency, success, error
    """
    marketplace_lower = marketplace.lower()
    
    try:
        if marketplace_lower == "trendyol":
            # Trendyol direkt arama
            import sys
            import os
            trendyol_path = os.path.join(os.path.dirname(__file__), "mediamarktproj", "trendyol", "trendyol_scraper.py")
            if os.path.exists(trendyol_path):
                # TrendyolScraper'ı import et ve kullan
                sys.path.insert(0, os.path.dirname(trendyol_path))
                from trendyol_scraper import TrendyolScraper
                
                scraper = TrendyolScraper()
                # EAN varsa önce EAN ile ara
                if ean:
                    price = scraper.get_price_by_ean(str(ean), product_name)
                else:
                    price = scraper.get_price_by_product_name(product_name)
                
                if price:
                    return {
                        'url': None,  # URL bulunamıyor ama fiyat var
                        'price': float(price),
                        'currency': 'TRY',
                        'title': None,  # Trendyol scraper title döndürmüyor
                        'success': True,
                        'error': None
                    }
        elif marketplace_lower == "hepsiburada":
            # Hepsiburada direkt arama - mediamarktproj kodlarını kullan
            import sys
            import os
            hepsiburada_path = os.path.join(os.path.dirname(__file__), "mediamarktproj", "hepsiburada", "hepsiburada_fiyat_cek.py")
            if os.path.exists(hepsiburada_path):
                sys.path.insert(0, os.path.dirname(hepsiburada_path))
                from hepsiburada_fiyat_cek import hepsiburada_fiyat_cek, tarayiciyi_baslat
                
                # Selenium driver'ı async wrapper ile kullan
                loop = asyncio.get_event_loop()
                
                def hepsiburada_sync_search():
                    """Selenium ile Hepsiburada'da arama yapan sync fonksiyon"""
                    driver = None
                    try:
                        driver = tarayiciyi_baslat()
                        fiyat = None
                        durum = None
                        
                        # EAN varsa önce EAN ile ara
                        if ean:
                            logger.debug(f"Hepsiburada: EAN ile aranıyor: {ean}")
                            fiyat, durum = hepsiburada_fiyat_cek(
                                driver, 
                                int(ean) if str(ean).replace('.', '').isdigit() else str(ean).strip(),
                                urun_adi=product_name,
                                debug=False
                            )
                            
                            # EAN ile bulunamazsa ürün adı ile ara
                            if not fiyat:
                                logger.debug(f"Hepsiburada: EAN ile bulunamadı ({durum}), ürün adı ile aranıyor: {product_name}")
                                if product_name:
                                    fiyat, durum = hepsiburada_fiyat_cek(
                                        driver, 
                                        str(product_name).strip(),
                                        urun_adi=str(product_name).strip(),
                                        debug=False
                                    )
                        else:
                            # EAN yoksa direkt ürün adı ile ara
                            if product_name:
                                logger.debug(f"Hepsiburada: Ürün adı ile aranıyor: {product_name}")
                                fiyat, durum = hepsiburada_fiyat_cek(
                                    driver, 
                                    str(product_name).strip(),
                                    urun_adi=str(product_name).strip(),
                                    debug=False
                                )
                        
                        if fiyat:
                            # Fiyat string formatında, float'a çevir
                            price_clean = re.sub(r'[^\d.,]', '', str(fiyat))
                            if '.' in price_clean and ',' in price_clean:
                                price_clean = price_clean.replace('.', '').replace(',', '.')
                            elif ',' in price_clean:
                                price_clean = price_clean.replace(',', '.')
                            try:
                                return float(price_clean), None  # title yok
                            except ValueError:
                                return None, None
                        return None, None
                    except Exception as e:
                        logger.debug(f"Hepsiburada direkt arama hatası: {e}")
                        return None, None
                    finally:
                        if driver:
                            try:
                                driver.quit()
                            except:
                                pass
                
                # Async wrapper ile çalıştır
                price, title = await asyncio.to_thread(hepsiburada_sync_search)
                
                if price:
                    return {
                        'url': None,  # URL bulunamıyor ama fiyat var
                        'price': price,
                        'currency': 'TRY',
                        'title': title,  # Hepsiburada scraper title döndürmüyor
                        'success': True,
                        'error': None
                    }
        elif marketplace_lower == "teknosa":
            # Teknosa direkt arama
            import sys
            import os
            teknosa_path = os.path.join(os.path.dirname(__file__), "mediamarktproj", "mediamarktteknosanalazi", "teknosa_scraper.py")
            if os.path.exists(teknosa_path):
                sys.path.insert(0, os.path.dirname(teknosa_path))
                from teknosa_scraper import find_best_match_by_name
                
                price, title, link = find_best_match_by_name(product_name)
                if price:
                    # Fiyat string formatında, float'a çevir
                    price_clean = re.sub(r'[^\d.,]', '', str(price))
                    if '.' in price_clean and ',' in price_clean:
                        price_clean = price_clean.replace('.', '').replace(',', '.')
                    elif ',' in price_clean:
                        price_clean = price_clean.replace(',', '.')
                    try:
                        price_float = float(price_clean)
                        return {
                            'url': link,
                            'price': price_float,
                            'currency': 'TRY',
                            'title': title,  # Teknosa scraper title döndürüyor
                            'success': True,
                            'error': None
                        }
                    except ValueError:
                        pass
        elif marketplace_lower == "amazon":
            # Amazon direkt arama - yeni gelişmiş yöntemi kullan
            logger.info(f"🔍 Amazon direkt arama yapılıyor: {product_name}")
            result = await search_amazon_direct(product_name, ean)
            return result
    
    except Exception as e:
        logger.debug(f"Marketplace direkt arama hatası ({marketplace}): {e}")
    
    return {
        'url': None,
        'price': None,
        'currency': None,
        'title': None,
        'success': False,
        'error': f'Marketplace direkt arama başarısız: {marketplace}'
    }


async def search_product(product_name: str, marketplace: str, mm_price: float = None, ean: str = None) -> Dict[str, any]:
    """
    Belirli bir ürün için Google'da arama yapar ve sonucu döndürür.
    
    Args:
        product_name: Ürün adı
        marketplace: Marketplace adı (örn: "Trendyol")
    
    Returns:
        Dict containing: product_name, marketplace, url, success, error
    """
    try:
        search_query = f"{product_name} {marketplace}"
        
        logger.debug(f"Aranıyor: '{product_name}' -> {marketplace}")
        
        # Google Custom Search API maksimum 10 sonuç döndürebilir, ama sayfalama ile 15 sonuç alabiliriz
        # İlk 10 sonuç için bir istek, sonraki 5 sonuç için ikinci istek
        all_items = []
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # İlk 10 sonuç
            params1 = {
                "key": settings.google_api_key,
                "cx": settings.google_cse_id,
                "q": search_query,
                "num": 10,
                "start": 1
            }
            
            response1 = await client.get(GOOGLE_SEARCH_URL, params=params1)
            response1.raise_for_status()
            data1 = response1.json()
            
            if "items" in data1 and len(data1["items"]) > 0:
                all_items.extend(data1["items"])
                logger.info(f"📊 İlk 10 sonuç alındı: {len(data1['items'])} sonuç")
            
            # Sonraki 5 sonuç (eğer ilk istekte 10 sonuç varsa)
            if "items" in data1 and len(data1["items"]) == 10:
                params2 = {
                    "key": settings.google_api_key,
                    "cx": settings.google_cse_id,
                    "q": search_query,
                    "num": 5,
                    "start": 11
                }
                
                try:
                    response2 = await client.get(GOOGLE_SEARCH_URL, params=params2)
                    response2.raise_for_status()
                    data2 = response2.json()
                    
                    if "items" in data2 and len(data2["items"]) > 0:
                        all_items.extend(data2["items"])
                        logger.info(f"📊 Sonraki 5 sonuç alındı: {len(data2['items'])} sonuç")
                except Exception as e:
                    logger.debug(f"İkinci sayfa alınamadı (normal olabilir): {e}")
            
            # Tüm sonuçları birleştir
            data = {"items": all_items}
            
            if "items" not in data or len(data["items"]) == 0:
                # Google'da sonuç yoksa, Amazon için direkt arama yap
                if marketplace.lower() == "amazon":
                    logger.debug("🔍 Google'da sonuç yok, Amazon direkt arama yapılıyor...")
                    direct_result = await search_amazon_direct(product_name)
                    if direct_result.get("success"):
                        return {
                            "product_name": product_name,
                            "marketplace": marketplace,
                            "url": direct_result.get("url"),
                            "price": direct_result.get("price"),
                            "currency": direct_result.get("currency", "TRY"),
                            "success": True,
                            "error": None
                        }
                    else:
                        return {
                            "product_name": product_name,
                            "marketplace": marketplace,
                            "url": None,
                            "price": None,
                            "currency": None,
                            "success": False,
                            "error": "No search results found and Amazon direct search failed"
                        }
                
                logger.warning(f"Sonuç bulunamadı: '{product_name}' -> {marketplace}")
                return {
                    "product_name": product_name,
                    "marketplace": marketplace,
                    "url": None,
                    "price": None,
                    "currency": None,
                    "success": False,
                    "error": "No search results found"
                }
            
            # Tüm sonuçlarda marketplace linklerini bul
            marketplace_lower = marketplace.lower()
            marketplace_urls = []
            product_page_urls = []  # Ürün sayfaları için ayrı liste (Amazon ve Teknosa için)
            category_page_urls = []  # Kategori sayfaları için ayrı liste (Amazon ve Teknosa için)
            
            # Tüm sonuçları logla (INFO seviyesinde - kullanıcı görebilsin)
            logger.info(f"🔍 Google'dan {len(data['items'])} sonuç alındı, {marketplace} linkleri aranıyor...")
            logger.info(f"📋 Arama sorgusu: '{search_query}'")
            
            for idx, item in enumerate(data["items"], 1):
                original_link = item.get("link", "")
                title = item.get("title", "")
                snippet = item.get("snippet", "")[:100]
                
                # Redirect URL'den gerçek URL'i çıkar (önce URL'i parse et)
                real_link = extract_real_url(original_link)
                link_lower = real_link.lower()
                
                # Sponsorlu/reklam linki kontrolü
                is_sponsored = is_sponsored_link(item)
                
                # Tüm sonuçları INFO seviyesinde logla
                sponsored_tag = " [SPONSORLU]" if is_sponsored else ""
                logger.info(f"  📌 Sonuç {idx}{sponsored_tag}: {title[:80]}...")
                logger.info(f"     Link: {real_link[:100]}...")
                
                if marketplace_lower == "amazon":
                    # Daha kapsamlı Amazon kontrolü (gerçek URL'de)
                    is_amazon = any(domain in link_lower for domain in [
                        "amazon.com", 
                        "amazon.com.tr", 
                        "amazon.co.uk", 
                        "amazon.de", 
                        "amazon.fr",
                        "amazon.it",
                        "amazon.es"
                    ])
                    
                    if is_amazon:
                        # Amazon ürün sayfası mı kategori sayfası mı kontrol et
                        # Ürün sayfaları: /dp/, /gp/product/, /product/
                        # Kategori sayfaları: /s?, /s/, /gp/browse/, /b/, /s?k=, /s?rh=
                        is_product_page = any(pattern in link_lower for pattern in [
                            "/dp/",
                            "/gp/product/",
                            "/product/"
                        ])
                        is_category_page = any(pattern in link_lower for pattern in [
                            "/s?",
                            "/s/",
                            "/gp/browse/",
                            "/b/",
                            "/s?k=",
                            "/s?rh=",
                            "/s?ie=",
                            "/s?node="
                        ])
                        
                        # Sponsorlu ama doğru marketplace + ürün sayfasıysa kabul et
                        if is_sponsored and is_product_page:
                            # Sponsorlu ama ürün sayfası - kabul et
                            product_page_urls.append(real_link)
                            logger.info(f"  ✅ Amazon ÜRÜN SAYFASI bulundu (sıra {idx}) [SPONSORLU ama kabul edildi]")
                        elif is_sponsored and is_category_page:
                            # Sponsorlu ve kategori sayfası - atla
                            logger.info(f"  ⚠️ Amazon kategori sayfası (sıra {idx}, sponsorlu - atlanacak)")
                            continue
                        elif is_sponsored:
                            # Sponsorlu ama ne ürün ne kategori - atla (güvenli tarafta kal)
                            logger.info(f"  ⚠️ Amazon linki sponsorlu ama belirsiz format (sıra {idx}, atlanacak)")
                            continue
                        elif is_product_page:
                            # Ürün sayfası - en yüksek öncelik
                            product_page_urls.append(real_link)
                            logger.info(f"  ✅ Amazon ÜRÜN SAYFASI bulundu (sıra {idx})")
                        elif is_category_page:
                            # Kategori sayfası - en düşük öncelik (atlanacak)
                            category_page_urls.append(real_link)
                            logger.info(f"  ⚠️ Amazon kategori sayfası (sıra {idx}, atlanacak)")
                        else:
                            # Ne ürün ne kategori sayfası - normal öncelik
                            marketplace_urls.append(real_link)
                            logger.info(f"  ✅ Amazon link bulundu (sıra {idx})")
                    else:
                        # Amazon linki değil - sponsorluysa atla
                        if is_sponsored:
                            logger.info(f"  ⚠️ Sonuç {idx} sponsorlu ve Amazon linki değil - atlanıyor")
                            continue
                        logger.info(f"  ❌ Sonuç {idx} Amazon linki değil")
                elif marketplace_lower == "trendyol" and "trendyol.com" in link_lower:
                    # Trendyol ürün sayfası kontrolü (genelde /p/ veya /brand/ içerir)
                    is_product_page = "/p/" in link_lower or "/brand/" in link_lower
                    is_category_page = "/sr" in link_lower or "/kategori" in link_lower or "/arama" in link_lower
                    
                    if is_sponsored and is_product_page:
                        # Sponsorlu ama ürün sayfası - kabul et
                        marketplace_urls.append(real_link)
                        logger.info(f"  ✅ Trendyol link bulundu (sıra {idx}) [SPONSORLU ama kabul edildi]")
                    elif is_sponsored and is_category_page:
                        # Sponsorlu ve kategori sayfası - atla
                        logger.info(f"  ⚠️ Trendyol kategori sayfası (sıra {idx}, sponsorlu - atlanacak)")
                        continue
                    elif is_sponsored:
                        # Sponsorlu ama belirsiz - atla
                        logger.info(f"  ⚠️ Trendyol linki sponsorlu ama belirsiz format (sıra {idx}, atlanacak)")
                        continue
                    elif is_category_page:
                        # Kategori sayfası - atla
                        logger.info(f"  ⚠️ Trendyol kategori sayfası (sıra {idx}, atlanacak)")
                        continue
                    else:
                        # Ürün sayfası veya normal link
                        marketplace_urls.append(real_link)
                        logger.info(f"  ✅ Trendyol link bulundu (sıra {idx})")
                elif marketplace_lower == "hepsiburada" and "hepsiburada.com" in link_lower:
                    # Hepsiburada ürün sayfası kontrolü (genişletilmiş)
                    # Ürün sayfaları: /p/, /urun/, -pm-, -p-, -HB ile biten, veya uzun slug formatı
                    is_product_page = (
                        "/p/" in link_lower or 
                        "/urun/" in link_lower or
                        "-pm-" in link_lower or  # product model (örn: -pm-HBC000005ELGI)
                        "-p-" in link_lower or   # product
                        "-HB" in link_lower.upper() or  # HBC000005ELGI gibi
                        (link_lower.count("-") >= 5 and "?sayfa=" not in link_lower)  # Uzun slug formatı
                    )
                    
                    # Kategori sayfaları: /liste, /kategori, /arama, -x-s, -xc-, /c-, ?sayfa=
                    is_category_page = (
                        "/liste" in link_lower or 
                        "/kategori" in link_lower or 
                        "/arama" in link_lower or
                        "-x-s" in link_lower or  # search results (örn: -x-s57124)
                        "-xc-" in link_lower or  # category
                        "/c-" in link_lower or   # category
                        "?sayfa=" in link_lower  # pagination
                    )
                    
                    if is_sponsored and is_product_page:
                        # Sponsorlu ama ürün sayfası - kabul et
                        marketplace_urls.append(real_link)
                        logger.info(f"  ✅ Hepsiburada link bulundu (sıra {idx}) [SPONSORLU ama kabul edildi]")
                    elif is_sponsored and is_category_page:
                        # Sponsorlu ve kategori sayfası - atla
                        logger.info(f"  ⚠️ Hepsiburada kategori sayfası (sıra {idx}, sponsorlu - atlanacak)")
                        continue
                    elif is_sponsored:
                        # Sponsorlu ama belirsiz - ürün sayfası gibi görünüyorsa kabul et
                        if not is_category_page and (link_lower.count("-") >= 3):
                            marketplace_urls.append(real_link)
                            logger.info(f"  ✅ Hepsiburada link bulundu (sıra {idx}) [SPONSORLU ama ürün sayfası gibi görünüyor]")
                        else:
                            logger.info(f"  ⚠️ Hepsiburada linki sponsorlu ama belirsiz format (sıra {idx}, atlanacak)")
                            continue
                    elif is_category_page:
                        # Kategori sayfası - atla
                        logger.info(f"  ⚠️ Hepsiburada kategori sayfası (sıra {idx}, atlanacak)")
                        continue
                    else:
                        # Ürün sayfası veya normal link
                        marketplace_urls.append(real_link)
                        logger.info(f"  ✅ Hepsiburada link bulundu (sıra {idx})")
                elif marketplace_lower == "teknosa" and "teknosa.com" in link_lower:
                    # Teknosa linkini ekle, ama ürün sayfası mı kategori sayfası mı kontrol et
                    # Ürün sayfaları genellikle "-p-" içerir
                    # Kategori sayfaları "-bc-" veya "/magaza/" içerir
                    is_product_page = "-p-" in link_lower
                    is_category_page = "-bc-" in link_lower or "/magaza/" in link_lower or "/kategori/" in link_lower
                    
                    # Ürün sayfalarını, kategori sayfalarını ve diğerlerini ayrı listelerde tut
                    if is_sponsored and is_product_page:
                        # Sponsorlu ama ürün sayfası - kabul et
                        product_page_urls.append(real_link)
                        logger.info(f"  ✅ Teknosa ÜRÜN SAYFASI bulundu (sıra {idx}) [SPONSORLU ama kabul edildi]")
                    elif is_sponsored and is_category_page:
                        # Sponsorlu ve kategori sayfası - atla
                        logger.info(f"  ⚠️ Teknosa kategori sayfası (sıra {idx}, sponsorlu - atlanacak)")
                        continue
                    elif is_sponsored:
                        # Sponsorlu ama belirsiz - atla
                        logger.info(f"  ⚠️ Teknosa linki sponsorlu ama belirsiz format (sıra {idx}, atlanacak)")
                        continue
                    elif is_product_page:
                        # Ürün sayfası - en yüksek öncelik
                        product_page_urls.append(real_link)
                        logger.info(f"  ✅ Teknosa ÜRÜN SAYFASI bulundu (sıra {idx})")
                    elif is_category_page:
                        # Kategori sayfası - en düşük öncelik
                        category_page_urls.append(real_link)
                        logger.info(f"  ⚠️ Teknosa kategori sayfası (sıra {idx}, atlanacak)")
                    else:
                        # Ne ürün ne kategori sayfası - normal öncelik
                        marketplace_urls.append(real_link)
                        logger.info(f"  ✅ Teknosa link bulundu (sıra {idx})")
                else:
                    # Diğer marketplace'ler veya eşleşmeyen linkler
                    if is_sponsored:
                        logger.info(f"  ⚠️ Sonuç {idx} sponsorlu ve {marketplace} linki değil - atlanıyor")
                        continue
            
            # Amazon ve Teknosa için: Önce ürün sayfaları, sonra diğer linkler, en son kategori sayfaları
            if marketplace_lower == "amazon":
                # Önce ürün sayfalarını ekle (öncelikli), kategori sayfalarını atla
                marketplace_urls = product_page_urls + marketplace_urls
                logger.info(f"✅ Amazon: {len(product_page_urls)} ürün sayfası, {len(marketplace_urls) - len(product_page_urls)} normal link bulundu, {len(category_page_urls)} kategori sayfası atlandı")
                # Bulunan ürün sayfalarını listele
                for i, url in enumerate(product_page_urls, 1):
                    logger.info(f"   Ürün sayfası {i}: {url[:100]}...")
            elif marketplace_lower == "teknosa":
                # Önce ürün sayfalarını ekle (öncelikli)
                marketplace_urls = product_page_urls + marketplace_urls + category_page_urls
                logger.debug(f"✅ Teknosa: {len(product_page_urls)} ürün sayfası, {len(marketplace_urls) - len(product_page_urls) - len(category_page_urls)} normal link, {len(category_page_urls)} kategori sayfası bulundu")
            
            # Tüm linkleri kontrol et (10 limiti kaldırıldı - tüm sonuçları dene)
            # marketplace_urls = marketplace_urls[:10]  # Limit kaldırıldı - tüm sonuçları kontrol et
            
            if marketplace_urls:
                logger.info(f"✅ Toplam {len(marketplace_urls)} {marketplace} linki bulundu - sırayla kontrol ediliyor...")
            else:
                logger.warning(f"⚠️ Google sonuçlarında {marketplace} linki bulunamadı")
            
            if not marketplace_urls:
                # Google sonuçlarında marketplace linki yok, direkt marketplace'de ara
                logger.debug(f"🔍 Google sonuçlarında {marketplace} linki yok, direkt arama yapılıyor...")
                direct_result = await search_marketplace_direct(product_name, marketplace, ean)
                if direct_result.get("success"):
                    price_value = direct_result.get("price")
                    
                    # MM Price kontrolü (%35 tolerans)
                    if mm_price and not is_price_valid(price_value, mm_price):
                        logger.warning(f"⚠️ Direkt aramada bulunan fiyat geçersiz (MM Price kontrolü): {price_value:.2f}")
                        return {
                            "product_name": product_name,
                            "marketplace": marketplace,
                            "url": None,
                            "price": None,
                            "currency": None,
                            "success": False,
                            "error": "Price validation failed (MM Price check)"
                        }
                    return {
                        "product_name": product_name,
                        "marketplace": marketplace,
                        "url": direct_result.get("url"),
                        "price": price_value,
                        "currency": direct_result.get("currency", "TRY"),
                        "success": True,
                        "error": None
                    }
                else:
                    return {
                        "product_name": product_name,
                        "marketplace": marketplace,
                        "url": None,
                        "price": None,
                        "currency": None,
                        "success": False,
                        "error": f"No {marketplace} links found in Google results and direct search failed"
                    }
            
            # Marketplace linkleri bulundu, sırayla fiyat çek ve MM Price kontrolü yap
            price_value = None
            currency_value = None
            valid_url = None
            
            logger.info(f"🔍 {len(marketplace_urls)} link sırayla kontrol ediliyor...")
            for url_idx, url in enumerate(marketplace_urls, 1):
                # Teknosa için: Kategori sayfalarını atla (sadece ürün sayfalarını kullan)
                if marketplace_lower == "teknosa":
                    url_lower = url.lower()
                    is_category_page = "-bc-" in url_lower or "/magaza/" in url_lower or "/kategori/" in url_lower
                    if is_category_page:
                        logger.info(f"  ⏭️ Link {url_idx}/{len(marketplace_urls)}: Teknosa kategori sayfası atlanıyor")
                        continue  # Kategori sayfasını atla
                
                logger.info(f"  🔍 Link {url_idx}/{len(marketplace_urls)} kontrol ediliyor: {url[:100]}...")
                
                # URL'den marketplace'i belirle ve fiyat çek
                price_info = None
                if "trendyol.com" in url.lower():
                    logger.info(f"    💰 Fiyat çekiliyor (Trendyol)...")
                    price_info = await extract_price_from_trendyol(url)
                elif "hepsiburada.com" in url.lower():
                    logger.info(f"    💰 Fiyat çekiliyor (Hepsiburada)...")
                    price_info = await extract_price_from_hepsiburada(url)
                elif "teknosa.com" in url.lower():
                    logger.info(f"    💰 Fiyat çekiliyor (Teknosa)...")
                    price_info = await extract_price_from_teknosa(url)
                elif "amazon.com" in url.lower() or "amazon.com.tr" in url.lower():
                    logger.info(f"    💰 Fiyat çekiliyor (Amazon)...")
                    price_info = await extract_price_from_amazon(url)
                
                if price_info and price_info.get("success"):
                    found_price = price_info.get("price")
                    found_currency = price_info.get("currency")
                    found_title = price_info.get("title")  # Başlık bilgisi
                    
                    logger.info(f"    ✅ Fiyat bulundu: {found_price:.2f} {found_currency}")
                    if found_title:
                        logger.info(f"    📦 Ürün başlığı: {found_title[:80]}...")
                    
                    # MM Price kontrolü (%35 tolerans)
                    if mm_price and not is_price_valid(found_price, mm_price):
                        logger.warning(f"    ⚠️ Fiyat geçersiz (MM Price kontrolü): {found_price:.2f} (MM Price: {mm_price:.2f})")
                        continue  # Bir sonraki linke geç
                    
                    # Ürün başlığı benzerlik kontrolü (Amazon için özellikle önemli)
                    if found_title and marketplace_lower == "amazon":
                        similarity = calculate_similarity(product_name, found_title)
                        logger.info(f"    🔍 Ürün benzerlik skoru: {similarity:.2%}")
                        logger.info(f"       Aranan: '{product_name[:70]}...'")
                        logger.info(f"       Bulunan: '{found_title[:70]}...'")
                        
                        # Benzerlik %40'ın altındaysa atla
                        if similarity < 0.40:
                            logger.warning(f"    ⚠️ Ürün benzerliği düşük ({similarity:.2%} < %40), bir sonraki linke geçiliyor...")
                            continue  # Bir sonraki linke geç
                        elif similarity < 0.60:
                            logger.warning(f"    ⚠️ Ürün benzerliği orta ({similarity:.2%}), dikkatli kontrol ediliyor...")
                    
                    # Tüm kontroller geçti, fiyatı kullan
                    price_value = found_price
                    currency_value = found_currency
                    valid_url = url
                    logger.warning(f"✅ {marketplace}: {product_name[:50]}... - {price_value:.2f} {currency_value}")
                    break
                else:
                    error_msg = price_info.get("error", "Unknown error") if price_info else "Price extraction failed"
                    logger.debug(f"⚠️ Fiyat çekilemedi: {error_msg} (URL: {url})")
            
            # Hiçbir linkte geçerli fiyat bulunamadıysa, direkt marketplace'de ara
            if price_value is None:
                logger.debug(f"🔍 Google sonuçlarında geçerli fiyat bulunamadı, direkt arama yapılıyor...")
                direct_result = await search_marketplace_direct(product_name, marketplace, ean)
                if direct_result.get("success"):
                    price_value = direct_result.get("price")
                    found_title = direct_result.get("title")
                    
                    # MM Price kontrolü (%35 tolerans)
                    if mm_price and not is_price_valid(price_value, mm_price):
                        logger.warning(f"⚠️ Direkt aramada bulunan fiyat geçersiz (MM Price kontrolü): {price_value:.2f}")
                        return {
                            "product_name": product_name,
                            "marketplace": marketplace,
                            "url": None,
                            "price": None,
                            "currency": None,
                            "success": False,
                            "error": "Price validation failed (MM Price check)"
                        }
                    
                    # Ürün başlığı benzerlik kontrolü (Amazon için özellikle önemli)
                    if found_title and marketplace_lower == "amazon":
                        similarity = calculate_similarity(product_name, found_title)
                        logger.info(f"🔍 Direkt arama - Ürün benzerlik skoru: {similarity:.2%} - Aranan: '{product_name[:50]}...' vs Bulunan: '{found_title[:50]}...'")
                        
                        # Benzerlik %40'ın altındaysa reddet
                        if similarity < 0.40:
                            logger.warning(f"⚠️ Direkt aramada ürün benzerliği düşük ({similarity:.2%} < %40), fiyat yazılmayacak")
                            return {
                                "product_name": product_name,
                                "marketplace": marketplace,
                                "url": None,
                                "price": None,
                                "currency": None,
                                "success": False,
                                "error": f"Product similarity too low ({similarity:.2%} < 40%)"
                            }
                        elif similarity < 0.60:
                            logger.warning(f"⚠️ Direkt aramada ürün benzerliği orta ({similarity:.2%}), dikkatli kontrol ediliyor...")
                    
                    return {
                        "product_name": product_name,
                        "marketplace": marketplace,
                        "url": direct_result.get("url"),
                        "price": price_value,
                        "currency": direct_result.get("currency", "TRY"),
                        "success": True,
                        "error": None
                    }
                else:
                    return {
                        "product_name": product_name,
                        "marketplace": marketplace,
                        "url": None,
                        "price": None,
                        "currency": None,
                        "success": False,
                        "error": "No valid price found in Google results and direct search failed"
                    }
            
            return {
                "product_name": product_name,
                "marketplace": marketplace,
                "url": valid_url,
                "price": price_value,
                "currency": currency_value,
                "success": True,
                "error": None
            }
            
    except httpx.HTTPStatusError as e:
        error_msg = f"Google API error: {e.response.status_code}"
        logger.error(f"❌ {error_msg} - Ürün: {product_name}")
        return {
            "product_name": product_name,
            "marketplace": marketplace,
            "url": None,
            "price": None,
            "currency": None,
            "success": False,
            "error": error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"❌ {error_msg} - Ürün: {product_name}")
        return {
            "product_name": product_name,
            "marketplace": marketplace,
            "url": None,
            "price": None,
            "currency": None,
            "success": False,
            "error": error_msg
        }


def read_excel_products(excel_file: str) -> List[Dict]:
    """
    Excel dosyasından ürün isimlerini ve MM Price'ı okur.
    
    Args:
        excel_file: Excel dosya yolu
    
    Returns:
        Ürün bilgileri listesi (her biri dict: {'product_name': str, 'mm_price': float or None})
    """
    try:
        # Excel dosyasını oku
        df = pd.read_excel(excel_file, engine='openpyxl')
        
        # İlk sütunu al (Product Name)
        first_column = df.iloc[:, 0]
        
        # MM Price sütununu bul
        mm_price_column = None
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if 'mm price' in col_lower or 'mm_price' in col_lower or 'mmprice' in col_lower:
                mm_price_column = col
                break
        
        if mm_price_column:
            logger.info(f"MM Price sütunu bulundu: {mm_price_column}")
        else:
            logger.warning("MM Price sütunu bulunamadı, fiyat kontrolü yapılmayacak")
        
        # EAN/SKU sütununu bul
        ean_column = None
        df_columns_lower = [col.lower().strip() for col in df.columns]
        if 'product sku' in df_columns_lower:
            ean_column = df.columns[df_columns_lower.index('product sku')]
        elif 'sku' in df_columns_lower:
            ean_column = df.columns[df_columns_lower.index('sku')]
        else:
            # Kısmi eşleşmeler
            ean_keywords = ['ean', 'barkod', 'barcode', 'gtin']
            for keyword in ean_keywords:
                for i, col_lower in enumerate(df_columns_lower):
                    if keyword in col_lower:
                        ean_column = df.columns[i]
                        break
                if ean_column:
                    break
        
        if ean_column:
            logger.info(f"EAN/SKU sütunu bulundu: {ean_column}")
        else:
            logger.warning("EAN/SKU sütunu bulunamadı, sadece ürün adı ile arama yapılacak")
        
        # Boş olmayan değerleri al ve string'e çevir
        products = []
        for idx, value in enumerate(first_column):
            if pd.notna(value):  # NaN değilse
                product_name = str(value).strip()
                if product_name:  # Boş string değilse
                    # MM Price'ı al
                    mm_price = None
                    if mm_price_column and mm_price_column in df.columns:
                        mm_price_val = df.iloc[idx][mm_price_column]
                        if pd.notna(mm_price_val):
                            try:
                                # Fiyatı sayıya çevir
                                if isinstance(mm_price_val, (int, float)):
                                    mm_price = float(mm_price_val)
                                else:
                                    # String ise temizle ve çevir
                                    mm_price_str = str(mm_price_val).strip()
                                    # TL, ₺, virgül, nokta gibi karakterleri temizle
                                    mm_price_str = re.sub(r'[^\d.,]', '', mm_price_str)
                                    # Türk formatı: 1.234,56 -> 1234.56
                                    if '.' in mm_price_str and ',' in mm_price_str:
                                        mm_price_str = mm_price_str.replace('.', '').replace(',', '.')
                                    elif ',' in mm_price_str:
                                        mm_price_str = mm_price_str.replace(',', '.')
                                    mm_price = float(mm_price_str)
                            except (ValueError, TypeError):
                                mm_price = None
                    
                    # EAN'ı al
                    ean = None
                    if ean_column and ean_column in df.columns:
                        ean_val = df.iloc[idx][ean_column]
                        if pd.notna(ean_val):
                            try:
                                # EAN'ı string'e çevir ve temizle
                                ean_str = str(ean_val).strip()
                                # Float'tan gelen .0'ı temizle
                                if '.' in ean_str:
                                    ean_str = ean_str.split('.')[0]
                                if ean_str and ean_str.lower() not in ['nan', 'none', '']:
                                    ean = ean_str
                            except:
                                pass
                    
                    products.append({
                        'product_name': product_name,
                        'mm_price': mm_price,
                        'ean': ean
                    })
        
        logger.info(f"Excel dosyasından {len(products)} ürün okundu")
        if mm_price_column:
            mm_price_count = sum(1 for p in products if p.get('mm_price') is not None)
            logger.info(f"MM Price bulunan ürün sayısı: {mm_price_count}")
        if ean_column:
            ean_count = sum(1 for p in products if p.get('ean') is not None)
            logger.info(f"EAN bulunan ürün sayısı: {ean_count}")
        return products
        
    except FileNotFoundError:
        logger.error(f"Excel dosyası bulunamadı: {excel_file}")
        raise
    except Exception as e:
        logger.error(f"Excel okuma hatası: {str(e)}")
        raise


def is_sponsored_link(item: Dict) -> bool:
    """
    Google Custom Search API sonucunun sponsorlu/reklam linki olup olmadığını kontrol eder.
    
    Args:
        item: Google Custom Search API'den gelen sonuç item'ı
    
    Returns:
        True if sponsored/ad link, False otherwise
    """
    # URL'de reklam domain'lerini kontrol et
    link = item.get("link", "").lower()
    sponsored_domains = [
        "googleadservices.com",
        "doubleclick.net",
        "googlesyndication.com",
        "/aclk",
        "adservice",
        "ads.google",
    ]
    
    if any(domain in link for domain in sponsored_domains):
        return True
    
    # Title veya snippet'te "sponsored", "ad", "reklam" gibi kelimeleri ara
    title = item.get("title", "").lower()
    snippet = item.get("snippet", "").lower()
    html_title = item.get("htmlTitle", "").lower() if item.get("htmlTitle") else ""
    html_snippet = item.get("htmlSnippet", "").lower() if item.get("htmlSnippet") else ""
    
    sponsored_keywords = [
        "sponsored",
        "advertisement",
        "ad",
        "reklam",
        "sponsorlu",
        "ilan",
        "promoted",
    ]
    
    all_text = f"{title} {snippet} {html_title} {html_snippet}"
    if any(keyword in all_text for keyword in sponsored_keywords):
        return True
    
    # Display link'te reklam işareti olabilir
    display_link = item.get("displayLink", "").lower()
    if any(keyword in display_link for keyword in ["ad", "ads", "reklam"]):
        return True
    
    return False


def extract_real_url(link: str) -> str:
    """
    Google redirect URL'lerinden gerçek URL'i çıkarır.
    Çift encode edilmiş URL'leri de düzgün şekilde decode eder.
    
    Args:
        link: Google'dan gelen link (redirect URL olabilir)
    
    Returns:
        Gerçek URL
    """
    # Google redirect URL kontrolü
    if "google.com/url" in link.lower():
        try:
            parsed = urlparse(link)
            params = parse_qs(parsed.query)
            # url parametresini al
            if 'url' in params:
                real_url = params['url'][0]
                
                # Çift encode edilmiş URL'leri decode et
                # Örnek: %25C3%25BC -> %C3%BC -> ü
                # İlk decode: %25 -> %
                decoded_url = unquote(real_url)
                # Eğer hala encoded karakterler varsa tekrar decode et
                if '%' in decoded_url:
                    decoded_url = unquote(decoded_url)
                
                logger.info(f"✅ Google redirect URL'den gerçek URL çıkarıldı: {decoded_url[:100]}...")
                return decoded_url
            else:
                logger.warning(f"⚠️ Google redirect URL'de 'url' parametresi bulunamadı. Tüm parametreler: {list(params.keys())}")
        except Exception as e:
            logger.warning(f"❌ Redirect URL parse hatası: {e}")
    
    # Normal URL ise direkt döndür
    return link


def is_price_valid(found_price: float, mm_price: float = None) -> bool:
    """
    Bulunan fiyatın MM Price'a göre geçerli olup olmadığını kontrol eder.
    MM Price'ın %35'inden fazla veya eksikse geçersiz sayılır.
    
    Args:
        found_price: Bulunan fiyat
        mm_price: MM Price (opsiyonel)
    
    Returns:
        True eğer fiyat geçerliyse, False değilse
    """
    if mm_price is None or mm_price <= 0:
        # MM Price yoksa veya geçersizse, fiyatı kabul et
        return True
    
    if found_price is None or found_price <= 0:
        return False
    
    # %35 tolerans hesapla
    lower_bound = mm_price * 0.65  # %35 eksik
    upper_bound = mm_price * 1.35  # %35 fazla
    
    # Fiyat aralık içindeyse geçerli
    is_valid = lower_bound <= found_price <= upper_bound
    
    if not is_valid:
        logger.debug(f"Fiyat geçersiz: {found_price:.2f} (MM Price: {mm_price:.2f}, Aralık: {lower_bound:.2f}-{upper_bound:.2f})")
    
    return is_valid


async def process_excel_file(excel_file: str, selected_marketplace: str = None):
    """
    Excel dosyasındaki tüm ürünleri işler ve belirtilen marketplace için arama yapar.
    Eğer selected_marketplace None ise, tüm marketplace'ler için çalışır.
    
    Args:
        excel_file: Excel dosya yolu
        selected_marketplace: Çalıştırılacak marketplace (None, "Teknosa", "Hepsiburada", "Trendyol", "Amazon")
    
    Returns:
        Sonuçlar listesi (her ürün için bir dict: product_name, teknosa_fiyatı, hepsiburada_fiyatı, trendyol_fiyatı, amazon_fiyatı)
    """
    # Ürünleri oku (artık dict listesi döndürüyor: product_name ve mm_price)
    products_data = read_excel_products(excel_file)
    
    if not products_data:
        logger.warning("Excel dosyasında ürün bulunamadı!")
        return []
    
    # Mevcut Excel dosyasını oku (eğer varsa) - mevcut değerleri korumak için
    existing_results = {}
    output_file = "results.xlsx"
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file, engine='openpyxl')
            # Mevcut sonuçları dict'e çevir (ürün ismi -> fiyatlar)
            for _, row in existing_df.iterrows():
                product_name = row.get('ürün ismi', '')
                if product_name:
                    existing_results[product_name] = {
                        'teknosa fiyatı': row.get('teknosa fiyatı'),
                        'hepsiburada fiyatı': row.get('hepsiburada fiyatı'),
                        'trendyol fiyatı': row.get('trendyol fiyatı'),
                        'amazon fiyatı': row.get('amazon fiyatı')
                    }
            logger.info(f"📂 Mevcut sonuçlar yüklendi: {len(existing_results)} ürün")
        except Exception as e:
            logger.debug(f"Mevcut Excel dosyası okunamadı (yeni dosya oluşturulacak): {e}")
    
    # Tüm ürünler işlenecek
    logger.info(f"📦 Toplam {len(products_data)} ürün işlenecek")
    
    # Marketplace seçimi
    all_marketplaces = ["Teknosa", "Hepsiburada", "Trendyol", "Amazon"]
    if selected_marketplace:
        # Seçilen marketplace'i doğrula
        selected_marketplace = selected_marketplace.capitalize()
        if selected_marketplace not in all_marketplaces:
            logger.error(f"❌ Geçersiz marketplace: {selected_marketplace}")
            logger.info(f"Geçerli marketplace'ler: {', '.join(all_marketplaces)}")
            return []
        marketplaces = [selected_marketplace]
        logger.info(f"🏪 Sadece {selected_marketplace} için çalıştırılıyor")
    else:
        marketplaces = all_marketplaces
        logger.info(f"🏪 Tüm marketplace'ler için çalıştırılıyor: {', '.join(marketplaces)}")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Toplam {len(products_data)} ürün işlenecek")
    logger.info(f"Marketplaceler: {', '.join(marketplaces)}")
    logger.info(f"{'='*60}\n")
    
    # Eşzamanlı istek sayısını sınırla (semaphore kullanarak)
    semaphore = asyncio.Semaphore(2)  # Aynı anda maksimum 2 fiyat çekme işlemi
    
    async def search_product_with_semaphore(product_name: str, marketplace: str, mm_price: float = None, ean: str = None):
        """Semaphore ile sınırlandırılmış arama"""
        async with semaphore:
            # Rate limiting için bekleme
            await asyncio.sleep(0.3)
            return await search_product(product_name, marketplace, mm_price, ean)
    
    # Sonuçları topla (yatay format için)
    all_results = []
    
    # Her ürün için sırasıyla tüm marketplace'leri dene
    for product_idx, product_data in enumerate(products_data, 1):
        product_name = product_data['product_name']
        mm_price = product_data.get('mm_price')
        ean = product_data.get('ean')
        
        logger.info(f"\n[{product_idx}/{len(products_data)}] {product_name}")
        
        # Her ürün için sonuç dict'i oluştur - mevcut değerleri koru
        product_result = {
            'ürün ismi': product_name,
            'teknosa fiyatı': existing_results.get(product_name, {}).get('teknosa fiyatı'),
            'hepsiburada fiyatı': existing_results.get(product_name, {}).get('hepsiburada fiyatı'),
            'trendyol fiyatı': existing_results.get(product_name, {}).get('trendyol fiyatı'),
            'amazon fiyatı': existing_results.get(product_name, {}).get('amazon fiyatı')
        }
        
        # Her marketplace için arama yap (sadece seçilen marketplace'ler)
        for marketplace in marketplaces:
            logger.debug(f"  🔍 {marketplace} aranıyor...")
            
            # Marketplace başlamadan önce kendi sütununu temizle
            if marketplace == "Teknosa":
                product_result['teknosa fiyatı'] = None
            elif marketplace == "Hepsiburada":
                product_result['hepsiburada fiyatı'] = None
            elif marketplace == "Trendyol":
                product_result['trendyol fiyatı'] = None
            elif marketplace == "Amazon":
                product_result['amazon fiyatı'] = None
            
            try:
                result = await search_product_with_semaphore(product_name, marketplace, mm_price, ean)
                
                # Marketplace'e göre fiyatı ilgili sütuna yaz
                price = result.get('price') if result.get('success') else None
                
                if marketplace == "Teknosa":
                    product_result['teknosa fiyatı'] = price
                elif marketplace == "Hepsiburada":
                    product_result['hepsiburada fiyatı'] = price
                elif marketplace == "Trendyol":
                    product_result['trendyol fiyatı'] = price
                elif marketplace == "Amazon":
                    product_result['amazon fiyatı'] = price
                
                if price is not None:
                    logger.info(f"  ✅ {marketplace}: {price:.2f} TRY")
                else:
                    logger.warning(f"  ⚠️  {marketplace}: Fiyat bulunamadı")
                    
            except Exception as e:
                logger.error(f"  ❌ {marketplace} hatası: {str(e)[:50]}")
                # Hata durumunda mevcut değer korunur (yukarıda zaten yüklendi)
        
        # Ürün sonucunu ekle
        all_results.append(product_result)
        
        # Her 5 üründe bir ara kayıt yap
        if product_idx % 5 == 0:
            logger.info(f"\n💾 Ara kayıt yapılıyor ({product_idx}/{len(products_data)} ürün işlendi)...")
            try:
                output_file = "results.xlsx"
                output_path = os.path.abspath(output_file)
                save_results_to_excel(all_results, output_file)
                logger.info(f"✅ Ara kayıt tamamlandı: {product_idx} ürün kaydedildi")
                logger.info(f"📁 Ara kayıt dosyası: {output_path}")
            except Exception as e:
                logger.error(f"❌ Ara kayıt hatası: {str(e)}")
        
        # Her ürün arasında kısa bir bekleme
        await asyncio.sleep(0.5)
    
    # Sonuçları özetle
    total_products = len(all_results)
    total_prices = sum(1 for r in all_results for key in ['teknosa fiyatı', 'hepsiburada fiyatı', 'trendyol fiyatı', 'amazon fiyatı'] if r.get(key) is not None)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"İşlem tamamlandı!")
    logger.info(f"Toplam ürün: {total_products}")
    logger.info(f"Toplam fiyat bulunan: {total_prices} (her ürün için maksimum 4)")
    logger.info(f"{'='*60}\n")
    
    return all_results


def save_results_to_excel(results: List[Dict], output_file: str = "results.xlsx"):
    """
    Sonuçları Excel dosyasına kaydeder. Mevcut dosya varsa günceller, yoksa yeni oluşturur.
    Yatay format: ürün ismi, teknosa fiyatı, hepsiburada fiyatı, trendyol fiyatı, amazon fiyatı
    
    Args:
        results: Sonuçlar listesi (her ürün için bir dict)
        output_file: Çıktı dosya adı
    """
    if not results:
        logger.warning("Kaydedilecek sonuç yok!")
        return
    
    # Mevcut Excel dosyasını oku (varsa)
    existing_df = None
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file, engine='openpyxl')
            logger.info(f"📂 Mevcut Excel dosyası yüklendi: {len(existing_df)} ürün")
        except Exception as e:
            logger.debug(f"Mevcut Excel dosyası okunamadı (yeni dosya oluşturulacak): {e}")
    
    # Yeni sonuçları DataFrame'e çevir
    new_df = pd.DataFrame(results)
    
    # Sütun sırasını düzenle
    column_order = ['ürün ismi', 'teknosa fiyatı', 'hepsiburada fiyatı', 'trendyol fiyatı', 'amazon fiyatı']
    # Sadece mevcut sütunları al
    existing_columns = [col for col in column_order if col in new_df.columns]
    new_df = new_df[existing_columns]
    
    # Fiyat sütunlarını sayısal formata çevir (None değerleri NaN olarak kalır)
    price_columns = ['teknosa fiyatı', 'hepsiburada fiyatı', 'trendyol fiyatı', 'amazon fiyatı']
    for col in price_columns:
        if col in new_df.columns:
            new_df[col] = pd.to_numeric(new_df[col], errors='coerce')
    
    # Mevcut dosya varsa, yeni sonuçlarla birleştir
    if existing_df is not None and 'ürün ismi' in existing_df.columns:
        # Mevcut DataFrame'i 'ürün ismi' sütununa göre birleştir
        # Yeni değerler mevcut değerlerin üzerine yazılır, ama sadece yeni değerler None değilse
        merged_df = existing_df.copy()
        
        # Yeni sonuçları mevcut DataFrame'e ekle/güncelle
        for _, new_row in new_df.iterrows():
            product_name = new_row.get('ürün ismi')
            if not product_name:
                continue
            
            # Mevcut DataFrame'de bu ürün var mı?
            existing_idx = merged_df[merged_df['ürün ismi'] == product_name].index
            
            if len(existing_idx) > 0:
                # Mevcut satırı güncelle - sadece None olmayan değerleri güncelle
                for col in price_columns:
                    if col in new_df.columns and col in merged_df.columns:
                        new_value = new_row.get(col)
                        if pd.notna(new_value):  # Yeni değer None değilse güncelle
                            merged_df.at[existing_idx[0], col] = new_value
            else:
                # Yeni satır ekle
                merged_df = pd.concat([merged_df, new_row.to_frame().T], ignore_index=True)
        
        df = merged_df
    else:
        df = new_df
    
    # Excel'e yazmadan önce özet bilgi
    total_prices = sum(df[col].notna().sum() for col in price_columns if col in df.columns)
    logger.info(f"\n{'='*60}")
    logger.info(f"Excel'e yazılacak bilgiler:")
    logger.info(f"  Toplam ürün (satır): {len(df)}")
    logger.info(f"  Toplam fiyat bulunan: {total_prices} adet")
    logger.info(f"  Sütunlar: {list(df.columns)}")
    logger.info(f"{'='*60}\n")
    
    # Excel'e yaz
    try:
        output_path = os.path.abspath(output_file)
        df.to_excel(output_file, index=False, engine='openpyxl')
        logger.info(f"✅ Sonuçlar kaydedildi: {output_file}")
        logger.info(f"📁 Dosya yolu: {output_path}")
        
        # Yazılan dosyayı kontrol et
        check_df = pd.read_excel(output_file, engine='openpyxl')
        logger.info(f"✅ Dosya kontrol edildi. Sütunlar: {list(check_df.columns)}")
    except Exception as e:
        logger.error(f"Excel yazma hatası: {str(e)}")
        raise


async def extract_prices_from_excel_urls(excel_file: str) -> None:
    """
    Excel dosyasındaki URL'lerden fiyatları çeker ve price sütununa yazar.
    
    Args:
        excel_file: Excel dosya yolu
    """
    try:
        # Excel dosyasını oku
        df = pd.read_excel(excel_file, engine='openpyxl')
        logger.info(f"Excel dosyası okundu: {len(df)} satır")
        logger.info(f"Mevcut sütunlar: {list(df.columns)}")
        
        # URL sütununu bul (url, link, URL, Link gibi)
        url_column = None
        for col in df.columns:
            if str(col).lower() in ['url', 'link', 'website', 'web']:
                url_column = col
                break
        
        if url_column is None:
            logger.error("URL sütunu bulunamadı! Mevcut sütunlar: " + ", ".join(df.columns))
            return
        
        logger.info(f"URL sütunu bulundu: {url_column}")
        
        # Price sütununu oluştur veya kontrol et
        if 'price' not in df.columns:
            df['price'] = None
            logger.info("Price sütunu oluşturuldu")
        
        # Geçerli URL'leri filtrele
        valid_urls = df[df[url_column].notna() & (df[url_column] != '')]
        logger.info(f"Toplam {len(valid_urls)} geçerli URL bulundu")
        
        # Sadece ilk 10 URL'i al
        valid_urls = valid_urls.head(10)
        logger.info(f"İşlenecek URL sayısı: {len(valid_urls)} (ilk 10 ürün)")
        
        if len(valid_urls) == 0:
            logger.warning("İşlenecek URL bulunamadı!")
            return
        
        # Semaphore ile eşzamanlı istek sayısını sınırla (Hepsiburada için 1'e düşürüldü)
        semaphore = asyncio.Semaphore(1)
        
        async def extract_price_for_url(row_idx: int, url: str) -> tuple:
            """Tek bir URL için fiyat çeker"""
            async with semaphore:
                # Hepsiburada için daha uzun bekleme
                await asyncio.sleep(1.0)
                
                # URL'den marketplace'i belirle
                url_lower = url.lower()
                price_info = None
                
                if "trendyol.com" in url_lower:
                    logger.info(f"[{row_idx+1}] Trendyol fiyat çekiliyor: {url[:60]}...")
                    price_info = await extract_price_from_trendyol(url)
                elif "hepsiburada.com" in url_lower:
                    logger.info(f"[{row_idx+1}] Hepsiburada fiyat çekiliyor: {url[:60]}...")
                    price_info = await extract_price_from_hepsiburada(url)
                elif "teknosa.com" in url_lower:
                    logger.info(f"[{row_idx+1}] Teknosa fiyat çekiliyor: {url[:60]}...")
                    price_info = await extract_price_from_teknosa(url)
                elif "amazon.com" in url_lower or "amazon.com.tr" in url_lower:
                    logger.info(f"[{row_idx+1}] Amazon fiyat çekiliyor: {url[:60]}...")
                    price_info = await extract_price_from_amazon(url)
                else:
                    logger.warning(f"[{row_idx+1}] Desteklenmeyen marketplace: {url[:60]}...")
                    return (row_idx, None, "Unsupported marketplace")
                
                if price_info and price_info.get("success"):
                    price = price_info.get("price")
                    logger.info(f"[{row_idx+1}] ✅ Fiyat bulundu: {price:.2f} TRY")
                    return (row_idx, price, None)
                else:
                    error = price_info.get("error", "Unknown error") if price_info else "Price extraction failed"
                    logger.warning(f"[{row_idx+1}] ⚠️ Fiyat çekilemedi: {error}")
                    # Debug için URL'yi logla
                    logger.debug(f"[{row_idx+1}] URL: {url}")
                    return (row_idx, None, error)
        
        # Tüm URL'ler için fiyat çek
        tasks = []
        for idx, row in valid_urls.iterrows():
            url = str(row[url_column]).strip()
            if url:
                tasks.append(extract_price_for_url(idx, url))
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Toplam {len(tasks)} URL için fiyat çekiliyor...")
        logger.info(f"{'='*60}\n")
        
        results = await asyncio.gather(*tasks)
        
        # Sonuçları DataFrame'e yaz
        price_count = 0
        for row_idx, price, error in results:
            if price is not None:
                df.at[row_idx, 'price'] = price
                price_count += 1
            elif error:
                # Hata mesajını log'la ama Excel'e yazma
                logger.debug(f"Satır {row_idx+1} için hata: {error}")
        
        # Price sütununu sayısal formata çevir
        df['price'] = pd.to_numeric(df['price'], errors='coerce')
        
        # Excel dosyasını kaydet (aynı dosyaya)
        df.to_excel(excel_file, index=False, engine='openpyxl')
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ İşlem tamamlandı!")
        logger.info(f"Toplam {len(tasks)} URL işlendi")
        logger.info(f"Fiyat bulunan: {price_count} adet")
        logger.info(f"Fiyat bulunamayan: {len(tasks) - price_count} adet")
        logger.info(f"Sonuçlar kaydedildi: {excel_file}")
        logger.info(f"{'='*60}\n")
        
    except Exception as e:
        logger.error(f"Excel işleme hatası: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise


async def main():
    """Ana fonksiyon"""
    import sys
    
    # Eğer "--extract-prices" parametresi varsa, sadece fiyat çekme modunu çalıştır
    if len(sys.argv) > 1 and sys.argv[1] == "--extract-prices":
        excel_file = sys.argv[2] if len(sys.argv) > 2 else "results.xlsx"
        if not os.path.exists(excel_file):
            logger.error(f"Excel dosyası bulunamadı: {excel_file}")
            return
        logger.info(f"Excel dosyasından URL'ler okunuyor: {excel_file}")
        await extract_prices_from_excel_urls(excel_file)
        return
    
    # Marketplace seçimi (komut satırı argümanı)
    selected_marketplace = None
    excel_file = EXCEL_FILE
    
    # Argümanları parse et
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ["--marketplace", "-m"]:
            if i + 1 < len(sys.argv):
                selected_marketplace = sys.argv[i + 1]
                i += 2
            else:
                logger.error("--marketplace parametresi için değer gerekli")
                return
        elif arg.startswith("--"):
            # Diğer parametreler
            i += 1
        else:
            # Excel dosya yolu olarak kabul et
            excel_file = arg
            i += 1
    
    if not os.path.exists(excel_file):
        logger.error(f"Excel dosyası bulunamadı: {excel_file}")
        logger.info("\nKullanım:")
        logger.info("  python process_excel.py [dosya.xlsx] [--marketplace MARKETPLACE]")
        logger.info("\nÖrnekler:")
        logger.info("  python process_excel.py file.xlsx --marketplace trendyol")
        logger.info("  python process_excel.py file.xlsx --marketplace hepsiburada")
        logger.info("  python process_excel.py file.xlsx --marketplace teknosa")
        logger.info("  python process_excel.py file.xlsx --marketplace amazon")
        logger.info("  python process_excel.py file.xlsx  # Tüm marketplace'ler için")
        return
    
    logger.info(f"Excel dosyası: {excel_file}")
    if selected_marketplace:
        logger.info(f"Seçilen marketplace: {selected_marketplace}\n")
    else:
        logger.info("Tüm marketplaceler için çalıştırılıyor: Hepsiburada, Teknosa, Trendyol, Amazon\n")
    
    # Excel dosyasını işle
    results = await process_excel_file(excel_file, selected_marketplace)
    
    # Sonuçları kaydet
    if results:
        save_results_to_excel(results)
        
        # Konsola da yazdır
        print("\n" + "="*80)
        print("SONUÇLAR:")
        print("="*80)
        for i, result in enumerate(results, 1):
            product_name = result.get('ürün ismi', 'N/A')
            prices = []
            if result.get('teknosa fiyatı') is not None:
                prices.append(f"Teknosa: {result['teknosa fiyatı']:.2f} TRY")
            if result.get('hepsiburada fiyatı') is not None:
                prices.append(f"Hepsiburada: {result['hepsiburada fiyatı']:.2f} TRY")
            if result.get('trendyol fiyatı') is not None:
                prices.append(f"Trendyol: {result['trendyol fiyatı']:.2f} TRY")
            if result.get('amazon fiyatı') is not None:
                prices.append(f"Amazon: {result['amazon fiyatı']:.2f} TRY")
            
            if prices:
                print(f"{i}. ✅ {product_name[:50]}...")
                for price_str in prices:
                    print(f"   {price_str}")
            else:
                print(f"{i}. ❌ {product_name[:50]}... - Fiyat bulunamadı")
        print("="*80)
    
    # Selenium driver'ı kapat (eğer kullanıldıysa)
    try:
        close_selenium_driver()
    except:
        pass


if __name__ == "__main__":
    asyncio.run(main())

