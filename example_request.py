"""
Example script demonstrating how to use the Google Search Redirect API.

This script shows how to make a request to the /search-and-redirect endpoint
and handle the redirect response.
"""
import requests
import sys


def example_request(product_name: str, marketplace: str, base_url: str = "http://localhost:8000"):
    """
    Make a request to the search-and-redirect endpoint.
    
    Args:
        product_name: Name of the product to search for
        marketplace: Marketplace name (e.g., "Trendyol")
        base_url: Base URL of the API server
    
    Returns:
        The redirect URL (Location header)
    """
    endpoint = f"{base_url}/search-and-redirect"
    params = {
        "product_name": product_name,
        "marketplace": marketplace
    }
    
    print(f"Searching for: '{product_name}' on '{marketplace}'")
    print(f"Request URL: {endpoint}")
    print(f"Parameters: {params}\n")
    
    try:
        # Don't follow redirects automatically - we want to see the redirect response
        response = requests.get(endpoint, params=params, allow_redirects=False, timeout=10)
        
        if response.status_code == 302:
            redirect_url = response.headers.get('Location')
            print(f"✅ Success! Redirect URL: {redirect_url}")
            return redirect_url
        else:
            print(f"❌ Error: Status code {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        print("❌ Error: Could not connect to the server.")
        print("Make sure the server is running: python main.py")
        return None
    except requests.exceptions.Timeout:
        print("❌ Error: Request timed out")
        return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None


if __name__ == "__main__":
    # Example 1: Canon Powershot G7X Mark III on Trendyol
    print("=" * 60)
    print("Example 1: Canon Powershot G7X Mark III on Trendyol")
    print("=" * 60)
    example_request(
        product_name="Canon Powershot G7X Mark III",
        marketplace="Trendyol"
    )
    
    print("\n" + "=" * 60)
    print("Example 2: iPhone 15 Pro on Amazon")
    print("=" * 60)
    example_request(
        product_name="iPhone 15 Pro",
        marketplace="Amazon"
    )
    
    # You can also provide custom arguments via command line
    if len(sys.argv) == 3:
        print("\n" + "=" * 60)
        print(f"Custom Request: {sys.argv[1]} on {sys.argv[2]}")
        print("=" * 60)
        example_request(
            product_name=sys.argv[1],
            marketplace=sys.argv[2]
        )



