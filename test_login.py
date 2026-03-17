import requests
import time
import json
import sys

BASE_URL = "http://localhost:8000"

def wait_for_server():
    print("Waiting for browser server to start...")
    for _ in range(10):
        try:
            response = requests.get(f"{BASE_URL}/status")
            if response.status_code == 200:
                print("Server is up!")
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    raise Exception("Server failed to start")

def run_test():
    wait_for_server()
    
    # 1. Navigate to the login page
    print("\n--- 1. Navigating to unusualwhales.com Login ---")
    res = requests.post(f"{BASE_URL}/navigate", json={"url": "https://unusualwhales.com/login"})
    print(res.json())
    time.sleep(3) # Give it a moment to load
    
    # 2. Tag elements so you can "see" what's clickable
    print("\n--- 2. Tagging Elements ---")
    res = requests.get(f"{BASE_URL}/tag_elements")
    data = res.json()
    elements = data.get("elements", {})
    print(f"Found {len(elements)} interactive elements on the page.")
    
    # Pause and let the human take over!
    print("\n=======================================================")
    print("HUMAN INTERVENTION REQUIRED!")
    print("1. Look at the open Chromium browser window.")
    print("2. You can either type your credentials directly into the browser window...")
    print("3. OR you can tell me the ID numbers of the email/password fields if you want me (the AI) to type them.")
    print("=======================================================\n")
    
    input("Press Enter here in the terminal ONLY AFTER you have successfully logged in and solved any CAPTCHAs/2FAs...")
    
    print("\n--- 3. Verifying Login State ---")
    res = requests.get(f"{BASE_URL}/status")
    print("Current URL after login:", res.json().get('current_url'))
    
    print("\n✅ Test complete! If you close the browser and run this script again, it should remember you are logged in!")

if __name__ == "__main__":
    run_test()
