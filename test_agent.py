import requests
import time
import json

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
    
    # 1. Navigate to a test page
    print("\n--- 1. Navigating to Wikipedia ---")
    res = requests.post(f"{BASE_URL}/navigate", json={"url": "https://www.wikipedia.org/"})
    print(res.json())
    time.sleep(2) # Give it a moment to load
    
    # 2. Tag elements so the AI can "see" what's clickable
    print("\n--- 2. Tagging Elements ---")
    res = requests.get(f"{BASE_URL}/tag_elements")
    data = res.json()
    elements = data.get("elements", {})
    print(f"Found {len(elements)} interactive elements.")
    
    # Find the search input field
    search_input_id = None
    for el_id, info in elements.items():
        if info.get("tagName") == "input" and info.get("type") == "search":
            search_input_id = el_id
            break
            
    if search_input_id is None:
        print("Could not find search input. Here are the elements:")
        print(json.dumps(elements, indent=2))
        return
        
    print(f"Found search input with ID: {search_input_id}")
    
    # 3. Type into the search input
    print("\n--- 3. Typing 'Artificial Intelligence' ---")
    res = requests.post(f"{BASE_URL}/type", json={
        "element_id": int(search_input_id), 
        "text": "Artificial Intelligence"
    })
    print(res.json())
    time.sleep(1)
    
    # 4. Find the search button and click it
    # We re-tag because adding text might have changed the layout/elements
    res = requests.get(f"{BASE_URL}/tag_elements")
    elements = res.json().get("elements", {})
    
    search_button_id = None
    for el_id, info in elements.items():
        if info.get("tagName") == "button" and info.get("type") == "submit":
            search_button_id = el_id
            break
            
    if search_button_id is None:
        print("Could not find search button.")
        return
        
    print(f"\n--- 4. Clicking Search Button (ID: {search_button_id}) ---")
    res = requests.post(f"{BASE_URL}/click", json={"element_id": int(search_button_id)})
    print(res.json())
    time.sleep(3) # Wait for results page
    
    # 5. Scroll down
    print("\n--- 5. Scrolling down ---")
    res = requests.post(f"{BASE_URL}/scroll", json={"direction": "down", "amount": 800})
    print(res.json())
    
    print("\n✅ Test completed successfully!")

if __name__ == "__main__":
    run_test()
