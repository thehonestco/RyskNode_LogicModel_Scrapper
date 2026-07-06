import requests
import json
import sys

BASE_URL = "http://localhost:8080/api/v1"

def test_json_api(cin, endpoint):
    url = f"{BASE_URL}/{endpoint}"
    payload = {
        "entity_id": cin,
        "seller_id": "test-seller",
        "requested_amount": 5000000.0,
        "avg_monthly_purchase_volume": 1000000.0,
        "credit_period_days": 30,
        "include_xai": True
    }
    headers = {"Content-Type": "application/json"}
    
    print(f"Testing POST {url} for {cin}...")
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            inner_data = data.get("data", {})
            print(f"  Response Code: {data.get('response_code')}")
            print(f"  Risk Band: {inner_data.get('risk_band')}")
            print(f"  Pralyon Score: {inner_data.get('pralyon_score')}")
            print(f"  Blended PD: {inner_data.get('blended_pd')}")
            if "evaluated_limit" in inner_data:
                print(f"  Evaluated Limit: {inner_data.get('evaluated_limit')}")
            return True
        else:
            print(f"  Error: {response.text}")
            return False
    except Exception as e:
        print(f"  Exception: {e}")
        return False

def test_report_api(cin, endpoint):
    url = f"{BASE_URL}/{endpoint}/report"
    payload = {
        "entity_id": cin,
        "seller_id": "test-seller",
        "requested_amount": 5000000.0,
        "avg_monthly_purchase_volume": 1000000.0,
        "credit_period_days": 30,
        "include_xai": True
    }
    headers = {"Content-Type": "application/json"}
    
    print(f"Testing POST {url} for {cin}...")
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            html = response.text
            print(f"  HTML Size: {len(html)} characters")
            # Verify no template errors
            if "jinja2" in html.lower() or "undefined" in html.lower() or "exception" in html.lower():
                print("  WARNING: Found potential Jinja2 or Undefined errors in HTML!")
                return False
            else:
                print("  HTML clean of common Jinja2 errors.")
                return True
        else:
            print(f"  Error: {response.text}")
            return False
    except Exception as e:
        print(f"  Exception: {e}")
        return False

print("=== STARTING PPRE INTEGRATION TEST ===")
success = True

# Company 1: TATA CONSULTING ENGINEERS (Full Data)
print("\n--- Testing TATA CONSULTING (Full Data) ---")
success &= test_json_api("U74210MH1999PLC123010", "assess")
success &= test_report_api("U74210MH1999PLC123010", "assess")
success &= test_json_api("U74210MH1999PLC123010", "credit-limit")
success &= test_report_api("U74210MH1999PLC123010", "credit-limit")

# Company 2: NICHE CONSULTING (No MCA Financials Data)
print("\n--- Testing NICHE CONSULTING (No MCA Data) ---")
success &= test_json_api("U74140MH2007PTC170348", "assess")
success &= test_report_api("U74140MH2007PTC170348", "assess")
success &= test_json_api("U74140MH2007PTC170348", "credit-limit")
success &= test_report_api("U74140MH2007PTC170348", "credit-limit")

if success:
    print("\n=== ALL TESTS PASSED SUCCESSFULLY ===")
    sys.exit(0)
else:
    print("\n=== SOME TESTS FAILED ===")
    sys.exit(1)
