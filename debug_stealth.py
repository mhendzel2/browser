import traceback

try:
    import playwright_stealth
    print("Import successful!")
except Exception as e:
    print(f"Caught exception: {e}")
    traceback.print_exc()
