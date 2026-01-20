# Marketplace Price Tracker

A production-ready tool that searches for products on marketplaces and extracts price information. Uses Google Custom Search API to find product links, then scrapes prices from marketplace pages.

## 🎯 Goal

Given a product name and marketplace name (e.g., "Canon Powershot G7X Mark III" + "Trendyol"), the system:
1. Searches Google using the official Google Custom Search API to find product links (NO scraping for search)
2. Retrieves search results as JSON
3. Extracts price information from marketplace pages using:
   - **Selenium** 
   - **BeautifulSoup/httpx** 
4. Returns price data or redirects to product URL

## 🏗️ Architecture

### Tech Stack
- **Backend**: Python 3.8+ with FastAPI
- **Search API**: Google Custom Search API (official, for finding product links)
- **Price Extraction**: 
  - Selenium 
  - BeautifulSoup + httpx 
- **HTTP Client**: httpx (async HTTP library)
- **Configuration**: pydantic-settings (type-safe config management)
### Project Structure
```
webscrap/
├── main.py              # FastAPI application with endpoints
├── config.py            # Configuration management
├── process_excel.py     # Standalone script for processing Excel files
├── streamlit_app.py     # Streamlit web GUI for non-technical users
├── start.bat            # Windows launcher script
├── start.sh             # Mac/Linux launcher script
├── file.xlsx            # Excel file with product names (first column)
├── requirements.txt     # Python dependencies
├── .gitignore          # Git ignore rules
├── KULLANIM_KILAVUZU.md # Turkish usage guide
└── README.md           # This file
```

## 🚀 Quick Start

### Prerequisites
- Python 3.8 or higher
- Google API Key with Custom Search API enabled
- Google Custom Search Engine ID (CSE ID)

### Setup Instructions

1. **Clone or navigate to the project directory**
   ```bash
   cd webscrap
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up Google Custom Search API**
   
   a. Get a Google API Key:
      - Go to [Google Cloud Console](https://console.cloud.google.com/)
      - Create a new project or select an existing one
      - Enable the "Custom Search API"
      - Create credentials (API Key)
   
   b. Create a Custom Search Engine:
      - Go to [Programmable Search Engine](https://programmablesearchengine.google.com/controlpanel/all)
      - Click "Add" to create a new search engine
      - Set "Sites to search" to `*` (search entire web)
      - Save and note your Search Engine ID (CSE ID)

5. **Create environment file**
   
   **Option 1: Using .env file (Recommended)**
   
   Copy the example file and add your credentials:
   ```bash
   cp .env.example .env
   ```
   
   Then edit `.env` and add your real API keys:
   ```env
   GOOGLE_API_KEY=your_google_api_key_here
   GOOGLE_CSE_ID=your_custom_search_engine_id_here
   GOOGLE_GEMINI_API_KEY=your_gemini_api_key_here (optional)
   HOST=0.0.0.0
   PORT=8000
   ```
   
   **Option 2: Using Streamlit Secrets (For Streamlit only)**
   
   If you're using Streamlit, you can also use Streamlit secrets:
   ```bash
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
   
   Then edit `.streamlit/secrets.toml` with your credentials.
   
   **⚠️ IMPORTANT:** Never commit `.env` or `secrets.toml` files to GitHub!
   
   For detailed security instructions, see [GIZLI_VERILER.md](GIZLI_VERILER.md)

6. **Run the server**
   ```bash
   python main.py
   ```
   Or using uvicorn directly:
   ```bash
   uvicorn main:app --reload
   ```

7. **Access the application**
   - **Streamlit GUI (Recommended for non-technical users)**: Run `start.bat` (Windows) or `./start.sh` (Mac/Linux)
   - **FastAPI Web interface**: http://localhost:8000
   - **API endpoint**: http://localhost:8000/search-and-redirect?product_name=Canon+Powershot+G7X+Mark+III&marketplace=Trendyol
   - **API documentation**: http://localhost:8000/docs (Swagger UI)

## 🖥️ Streamlit Web GUI (Recommended)

For non-technical users, we provide a user-friendly Streamlit web interface.

### Quick Start with Streamlit:

1. **Install dependencies** (if not already done):
   ```bash
   pip install -r requirements.txt
   ```

2. **Launch the application**:
   - **Windows**: Double-click `start.bat`
   - **Mac/Linux**: Run `./start.sh` in terminal

3. **Use the interface**:
   - Upload your Excel file
   - Select a marketplace (or "All Marketplaces")
   - Click "Start" and wait for results
   - Download the results Excel file

The Streamlit app will automatically open in your default browser at `http://localhost:8501`.

For detailed usage instructions in Turkish, see [KULLANIM_KILAVUZU.md](KULLANIM_KILAVUZU.md).

## 📡 API Endpoints

### `GET /search-and-redirect`

Searches Google for a product on a marketplace and redirects to the top result.

**Query Parameters:**
- `product_name` (required): Name of the product to search for
- `marketplace` (required): Marketplace name (e.g., "Trendyol")

**Response:**
- `302 Redirect`: Redirects to the top Google search result URL

**Example Request:**
```bash
curl "http://localhost:8000/search-and-redirect?product_name=Canon+Powershot+G7X+Mark+III&marketplace=Trendyol"
```

**Example Response:**
```
HTTP/1.1 302 Found
Location: https://www.trendyol.com/canon/...
```

### `GET /process-excel`

Excel dosyasından ürün isimlerini okur ve tüm ürünler için arama yapar.
Excel dosyasındaki ilk sütundan (Product Name) satır satır ürün isimlerini okur.

**Query Parameters:**
- `marketplace` (required): Marketplace name (e.g., "Trendyol")
- `excel_file` (optional): Excel file path (default: "file.xlsx")

**Response:**
- `200 OK`: JSON response with search results for all products

**Example Request:**
```bash
curl "http://localhost:8000/process-excel?marketplace=Trendyol&excel_file=file.xlsx"
```

**Example Response:**
```json
{
  "status": "completed",
  "total_products": 10,
  "successful": 8,
  "failed": 2,
  "results": [
    {
      "product_name": "Canon Powershot G7X Mark III",
      "marketplace": "Trendyol",
      "url": "https://www.trendyol.com/...",
      "success": true,
      "error": null
    },
    ...
  ]
}
```

### `GET /health`

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "Google Search Redirect API"
}
```

### `GET /`

Simple HTML frontend for testing the API interactively.

## 🤔 Key Decisions & Architecture Choices

### Why Google Custom Search API?

1. **Compliance & Legal**: Official API eliminates legal concerns associated with web scraping
2. **Reliability**: Google's API is stable, well-documented, and maintained
3. **Rate Limits**: Transparent quota management (100 free queries/day, then paid)
4. **Efficient Link Discovery**: Google API finds product links quickly, then we extract prices using appropriate tools (Selenium for JS-heavy sites, BeautifulSoup for static content)
5. **Structured Data**: Returns clean JSON, easier to parse than HTML scraping

### How This Differs from Marketplace Internal Search

**This Approach (Google Custom Search):**
- ✅ Searches the entire web via Google
- ✅ Finds products across multiple marketplaces automatically
- ✅ Uses Google's ranking algorithm (organic results)
- ✅ No marketplace-specific API keys needed
- ✅ Works with any marketplace without additional integration

**Marketplace Internal Search (Alternative):**
- ❌ Requires separate API integration for each marketplace
- ❌ Each marketplace has different API structures, auth, rate limits
- ❌ Must maintain multiple integrations and handle their breaking changes
- ❌ Limited to marketplaces that offer public APIs
- ✅ Could potentially find more accurate results if marketplace has better search

### Limitations & Trade-offs

#### Limitations:
1. **Cost**: Free tier limited to 100 queries/day. Paid tier: $5 per 1,000 queries
2. **Rate Limits**: Must respect Google's quota to avoid errors
3. **Search Quality**: Depends on Google's index and ranking algorithm
4. **Marketplace Filtering**: May return results from other marketplaces if search query isn't specific enough
5. **No Direct Control**: Can't customize Google's ranking algorithm

#### Trade-offs:
- **Simplicity vs. Control**: Using Google API simplifies architecture but gives less control over search parameters
- **Generic vs. Specific**: Works with any marketplace but may be less accurate than marketplace-specific APIs
- **Cost vs. Maintenance**: API costs money but eliminates need to maintain scrapers

### Architecture Principles

1. **Separation of Concerns**:
   - `config.py`: Configuration management (single responsibility)
   - `main.py`: API endpoints and business logic
   - Environment variables: Credentials and settings

2. **Error Handling**:
   - Comprehensive try-catch blocks
   - Proper HTTP status codes (502, 504, 404, 500)
   - Detailed logging for debugging

3. **Async/Await**:
   - Uses async HTTP client (httpx) for better performance
   - Non-blocking I/O operations

4. **Type Safety**:
   - Pydantic for settings validation
   - FastAPI automatic request validation

5. **Production-Ready**:
   - Health check endpoint
   - Proper logging
   - Environment-based configuration
   - Error handling with meaningful messages

## 🔧 Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_API_KEY` | Your Google API Key | Yes |
| `GOOGLE_CSE_ID` | Your Custom Search Engine ID | Yes |
| `HOST` | Server host (default: 0.0.0.0) | No |
| `PORT` | Server port (default: 8000) | No |

### Google API Quota

- **Free Tier**: 100 queries per day
- **Paid Tier**: $5 per 1,000 queries after free tier
- Monitor usage at: [Google Cloud Console](https://console.cloud.google.com/apis/api/customsearch.googleapis.com/quotas)

## 📝 Example Usage

### Using curl:
```bash
curl -v "http://localhost:8000/search-and-redirect?product_name=iPhone+15+Pro&marketplace=Trendyol"
```

### Using Python:
```python
import requests

response = requests.get(
    "http://localhost:8000/search-and-redirect",
    params={
        "product_name": "Canon Powershot G7X Mark III",
        "marketplace": "Trendyol"
    },
    allow_redirects=False
)

print(f"Redirect URL: {response.headers['Location']}")
```

### Using JavaScript (fetch):
```javascript
const response = await fetch(
  'http://localhost:8000/search-and-redirect?product_name=Canon+Powershot+G7X+Mark+III&marketplace=Trendyol'
);
// Browser will automatically follow redirect
window.location.href = response.url;
```

### Processing Excel File (Command Line):

Python script ile Excel dosyasını işleyin:
```bash
python process_excel.py Trendyol file.xlsx
```

Veya API endpoint ile:
```bash
curl "http://localhost:8000/process-excel?marketplace=Trendyol&excel_file=file.xlsx"
```

### Excel File Format:

Excel dosyası (`file.xlsx`) aşağıdaki formatta olmalıdır:
- **İlk sütun**: Product Name (ürün isimleri burada olmalı)
- Her satır bir ürün adı içermelidir
- Boş satırlar otomatik olarak atlanır

**Örnek Excel yapısı:**
| Product Name |
|--------------|
| Canon Powershot G7X Mark III |
| iPhone 15 Pro |
| Samsung Galaxy S24 |
| ... |

## 🧪 Testing

### Manual Testing
1. Start the server: `python main.py`
2. Open http://localhost:8000 in your browser
3. Enter a product name and marketplace
4. Click "Search & Redirect"
5. You should be redirected to the top Google result

### API Testing
```bash
# Test the API endpoint
curl -v "http://localhost:8000/search-and-redirect?product_name=test&marketplace=test"

# Test health endpoint
curl http://localhost:8000/health
```

## 📚 Documentation

- **API Documentation**: Visit http://localhost:8000/docs for interactive Swagger UI
- **Alternative Docs**: Visit http://localhost:8000/redoc for ReDoc interface

## 🔒 Security Considerations

1. **API Keys**: Never commit `.env` or `secrets.toml` files to version control
   - These files are already in `.gitignore`
   - Use `.env.example` as a template
   - See [GIZLI_VERILER.md](GIZLI_VERILER.md) for detailed security guide
2. **Rate Limiting**: Consider adding rate limiting middleware for production
3. **Input Validation**: FastAPI automatically validates query parameters
4. **HTTPS**: Use HTTPS in production to protect API keys in transit
5. **API Key Restrictions**: Restrict your Google API key to specific IPs/domains in production
6. **Secrets Management**: 
   - For local development: Use `.env` file
   - For Streamlit Cloud: Use Streamlit's built-in secrets management
   - For production: Use environment variables or a secrets management service

## 🚢 Production Deployment

### Docker (Optional)
You can containerize this application:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Deployment Checklist
- [ ] Set up proper environment variables in production
- [ ] Enable HTTPS/SSL
- [ ] Add rate limiting middleware
- [ ] Set up monitoring and logging
- [ ] Configure API key restrictions in Google Cloud Console
- [ ] Set up error tracking (e.g., Sentry)
- [ ] Add caching if needed (Redis)

## 📄 License

This is a mini-project example. Use as you see fit.

## 🤝 Contributing

This is a demonstration project. Feel free to fork and modify for your needs.

---

**Built with ❤️ using FastAPI and Google Custom Search API**

