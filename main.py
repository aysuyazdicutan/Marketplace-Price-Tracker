"""
FastAPI backend for Google Custom Search API integration.
Provides an endpoint to search for products and redirect to the top result.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from typing import Optional, List, Dict
import logging
import pandas as pd
import os
import asyncio
from urllib.parse import urlparse, parse_qs, unquote

from config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Google Search Redirect API",
    description="Search Google using Custom Search API and redirect to top result",
    version="1.0.0"
)

# Google Custom Search API endpoint
GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


@app.get("/", response_class=HTMLResponse)
async def root():
    """Simple HTML frontend for testing."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google Search Redirect</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                max-width: 600px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                margin-top: 0;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                color: #555;
                font-weight: 500;
            }
            input {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 14px;
                box-sizing: border-box;
            }
            button {
                background: #4285f4;
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
                width: 100%;
            }
            button:hover {
                background: #357ae8;
            }
            .info {
                margin-top: 20px;
                padding: 15px;
                background: #e8f0fe;
                border-radius: 4px;
                font-size: 14px;
                color: #1967d2;
            }
            .error {
                color: #d93025;
                margin-top: 10px;
                font-size: 14px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔍 Google Search Redirect</h1>
            <form id="searchForm">
                <div class="form-group">
                    <label for="product_name">Product Name:</label>
                    <input type="text" id="product_name" name="product_name" 
                           placeholder="e.g., Canon Powershot G7X Mark III" required>
                </div>
                <div class="form-group">
                    <label for="marketplace">Marketplace:</label>
                    <input type="text" id="marketplace" name="marketplace" 
                           placeholder="e.g., Trendyol" required>
                </div>
                <button type="submit">Search & Redirect</button>
                <div id="error" class="error"></div>
            </form>
            <div class="info">
                <strong>How it works:</strong> Enter a product name and marketplace. 
                The system will search Google and redirect you to the top organic result.
            </div>
        </div>
        <script>
            document.getElementById('searchForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const productName = document.getElementById('product_name').value;
                const marketplace = document.getElementById('marketplace').value;
                const errorDiv = document.getElementById('error');
                errorDiv.textContent = '';
                
                try {
                    const response = await fetch(`/search-and-redirect?product_name=${encodeURIComponent(productName)}&marketplace=${encodeURIComponent(marketplace)}`);
                    if (response.redirected) {
                        window.location.href = response.url;
                    } else if (!response.ok) {
                        const error = await response.json();
                        errorDiv.textContent = error.detail || 'An error occurred';
                    }
                } catch (error) {
                    errorDiv.textContent = 'Network error: ' + error.message;
                }
            });
        </script>
    </body>
    </html>
    """


@app.get("/search-and-redirect")
async def search_and_redirect(
    product_name: str = Query(..., description="Name of the product to search for"),
    marketplace: str = Query(..., description="Marketplace name (e.g., Trendyol)")
):
    """
    Search Google for a product on a specific marketplace and redirect to the top result.
    
    Args:
        product_name: The name of the product to search for
        marketplace: The marketplace name (e.g., "Trendyol")
    
    Returns:
        HTTP 302 redirect to the top Google search result
        
    Raises:
        HTTPException: If search fails or no results found
    """
    try:
        # Construct search query: "product_name marketplace site:marketplace.com"
        # This ensures we get results from the specific marketplace
        search_query = f"{product_name} {marketplace}"
        
        logger.info(f"Searching Google for: '{search_query}'")
        
        # Prepare API request parameters
        params = {
            "key": settings.google_api_key,
            "cx": settings.google_cse_id,
            "q": search_query,
            "num": 1  # We only need the top result
        }
        
        # Make request to Google Custom Search API
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(GOOGLE_SEARCH_URL, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            # Check if we have search results
            if "items" not in data or len(data["items"]) == 0:
                logger.warning(f"No search results found for: '{search_query}'")
                raise HTTPException(
                    status_code=404,
                    detail=f"No search results found for '{product_name}' on {marketplace}"
                )
            
            # Extract the top result URL
            top_result = data["items"][0]
            redirect_url = top_result["link"]
            
            # Google redirect URL'lerinden gerçek URL'i çıkar
            if "google.com/url" in redirect_url.lower():
                try:
                    parsed = urlparse(redirect_url)
                    params = parse_qs(parsed.query)
                    if 'url' in params:
                        real_url = params['url'][0]
                        # Çift encode edilmiş URL'leri decode et
                        decoded_url = unquote(real_url)
                        if '%' in decoded_url:
                            decoded_url = unquote(decoded_url)
                        redirect_url = decoded_url
                        logger.info(f"Google redirect URL'den gerçek URL çıkarıldı: {redirect_url[:100]}...")
                except Exception as e:
                    logger.warning(f"Redirect URL parse hatası: {e}")
            
            logger.info(f"Redirecting to: {redirect_url}")
            
            # Return HTTP 302 redirect
            return RedirectResponse(url=redirect_url, status_code=302)
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Google API error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=502,
            detail=f"Google API error: {e.response.status_code}"
        )
    except httpx.TimeoutException:
        logger.error("Request to Google API timed out")
        raise HTTPException(
            status_code=504,
            detail="Request to Google API timed out"
        )
    except KeyError as e:
        logger.error(f"Missing required field in API response: {e}")
        raise HTTPException(
            status_code=502,
            detail="Invalid response from Google API"
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.get("/process-excel")
async def process_excel_endpoint(
    marketplace: str = Query(..., description="Marketplace name (e.g., Trendyol)"),
    excel_file: str = Query(default="file.xlsx", description="Excel file path")
):
    """
    Excel dosyasındaki tüm ürünleri işler ve sonuçları JSON olarak döndürür.
    Excel dosyasındaki ilk sütundan (Product Name) ürün isimlerini okur.
    
    Args:
        marketplace: Marketplace adı (örn: "Trendyol")
        excel_file: Excel dosya yolu (varsayılan: "file.xlsx")
    
    Returns:
        JSON response with search results for all products
    """
    try:
        # Excel dosyasının var olup olmadığını kontrol et
        if not os.path.exists(excel_file):
            raise HTTPException(
                status_code=404,
                detail=f"Excel file not found: {excel_file}"
            )
        
        # Excel dosyasını oku
        try:
            df = pd.read_excel(excel_file, engine='openpyxl')
            # İlk sütunu al (Product Name)
            first_column = df.iloc[:, 0]
            # Boş olmayan değerleri al
            products = []
            for value in first_column:
                if pd.notna(value):
                    product_name = str(value).strip()
                    if product_name:
                        products.append(product_name)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Error reading Excel file: {str(e)}"
            )
        
        if not products:
            raise HTTPException(
                status_code=404,
                detail="No products found in Excel file"
            )
        
        logger.info(f"Processing {len(products)} products from {excel_file}")
        
        # Her ürün için arama yap (async)
        async def search_single_product(product_name: str):
            """Tek bir ürün için arama yapar"""
            try:
                search_query = f"{product_name} {marketplace}"
                params = {
                    "key": settings.google_api_key,
                    "cx": settings.google_cse_id,
                    "q": search_query,
                    "num": 1
                }
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(GOOGLE_SEARCH_URL, params=params)
                    response.raise_for_status()
                    data = response.json()
                    
                    if "items" not in data or len(data["items"]) == 0:
                        return {
                            "product_name": product_name,
                            "marketplace": marketplace,
                            "url": None,
                            "success": False,
                            "error": "No search results found"
                        }
                    
                    top_result = data["items"][0]
                    return {
                        "product_name": product_name,
                        "marketplace": marketplace,
                        "url": top_result["link"],
                        "success": True,
                        "error": None
                    }
            except Exception as e:
                return {
                    "product_name": product_name,
                    "marketplace": marketplace,
                    "url": None,
                    "success": False,
                    "error": str(e)
                }
        
        # Tüm ürünleri paralel olarak işle
        tasks = [search_single_product(product) for product in products]
        results = await asyncio.gather(*tasks)
        
        # Özet bilgiler
        successful = sum(1 for r in results if r["success"])
        failed = len(results) - successful
        
        return JSONResponse({
            "status": "completed",
            "total_products": len(products),
            "successful": successful,
            "failed": failed,
            "results": results
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing Excel file: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Google Search Redirect API"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )

