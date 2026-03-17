import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright, BrowserContext, Page
import base64
import markdownify
from typing import Optional

# Global state
playwright_instance = None
browser_context: BrowserContext = None
page: Page = None
intercepted_responses = []

async def handle_response(response):
    if response.request.resource_type in ["xhr", "fetch"]:
        try:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                body = await response.json()
                intercepted_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "method": response.request.method,
                    "json": body
                })
        except Exception:
            pass

async def route_intercept(route):
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()

USER_DATA_DIR = os.path.join(os.getcwd(), "browser_data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Launch the browser
    global playwright_instance, browser_context, page
    playwright_instance = await async_playwright().start()
    
    # Launch persistent context to save logins/cookies
    # Apply initial stealth arguments to bypass basic detection
    browser_context = await playwright_instance.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=False, # We want to see it for now
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        args=[
            "--disable-blink-features=AutomationControlled", # Hides "Chrome is being controlled by automated software"
        ],
        ignore_default_args=["--enable-automation"]
    )
    
    # Inject our custom stealth JS into EVERY page created in this context
    try:
        with open("stealth.js", "r") as f:
            stealth_js = f.read()
        await browser_context.add_init_script(stealth_js)
    except Exception as e:
        print("Warning: Could not load stealth.js scripts", e)
    
    # Get the default page or create a new one
    pages = browser_context.pages
    if len(pages) > 0:
        page = pages[0]
    else:
        page = await browser_context.new_page()
    page.on("response", handle_response)
        
    print("Browser started successfully.")
    
    yield
    
    # Shutdown: Clean up
    print("Shutting down browser...")
    if browser_context:
        try:
            await browser_context.close()
        except Exception:
            pass
    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception:
            pass

app = FastAPI(title="AI Browser API", lifespan=lifespan)

async def _ensure_page():
    global browser_context, page
    try:
        if not page or page.is_closed():
            pages = browser_context.pages
            if len(pages) > 0:
                page = pages[0]
            else:
                page = await browser_context.new_page()
            page.on("response", handle_response)
    except Exception as e:
        print(f"Error ensuring page: {e}")
        # Try to recover by creating a new page
        try:
            page = await browser_context.new_page()
            page.on("response", handle_response)
        except:
            pass
    return page

class NavigateRequest(BaseModel):
    url: str
    fast_mode: bool = False

@app.post("/navigate")
async def navigate_to_url(request: NavigateRequest):
    global page, intercepted_responses
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        await page.unroute("**/*")
        if request.fast_mode:
            await page.route("**/*", route_intercept)
            
        intercepted_responses.clear()
        
        await page.goto(request.url)
        return {"status": "success", "url": page.url}
    except Exception as e:
        return {"error": str(e)}

@app.get("/tag_elements")
async def tag_elements():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        with open("tagger.js", "r") as f:
            js_code = f.read()
            
        element_map = await page.evaluate(js_code)
        return {"status": "success", "elements": element_map}
    except Exception as e:
        return {"error": str(e)}

class ClickRequest(BaseModel):
    element_id: int

@app.post("/click")
async def click_element(request: ClickRequest):
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        # Use a timeout of 3s since we know the element should already be there
        await page.click(f'[data-ai-id="{request.element_id}"]', timeout=3000)
        
        # Wait for potential navigation
        await page.wait_for_load_state("networkidle", timeout=3000)
        return {"status": "success", "current_url": page.url}
    except Exception as e:
        return {"error": str(e)}

class TypeRequest(BaseModel):
    element_id: int
    text: str

@app.post("/type")
async def type_text(request: TypeRequest):
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        selector = f'[data-ai-id="{request.element_id}"]'
        await page.fill(selector, request.text)
        return {"status": "success", "text_typed": request.text}
    except Exception as e:
        return {"error": str(e)}

class ScrollRequest(BaseModel):
    direction: str = "down" # "up" or "down"
    amount: int = 500

@app.post("/scroll")
async def scroll_page(request: ScrollRequest):
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        sign = 1 if request.direction == "down" else -1
        await page.evaluate(f"window.scrollBy(0, {sign * request.amount})")
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/accessibility")
async def get_accessibility():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        client = await page.context.new_cdp_session(page)
        snapshot = await client.send('Accessibility.getFullAXTree')
        return {"status": "success", "accessibility_tree": snapshot}
    except Exception as e:
        return {"error": str(e)}

@app.get("/readability")
async def get_readability():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        # Inject Mozilla Readability
        await page.add_script_tag(url="https://unpkg.com/@mozilla/readability/Readability.js")
        
        # Evaluate it on a clone of the document
        article = await page.evaluate('''() => {
            try {
                const documentClone = document.cloneNode(true);
                const reader = new Readability(documentClone);
                const article = reader.parse();
                return article;
            } catch (e) {
                return {error: e.toString()};
            }
        }''')
        
        return {"status": "success", "article": article}
    except Exception as e:
        return {"error": str(e)}

@app.get("/screenshot")
async def get_screenshot():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        # Take a screenshot as jpeg to keep size manageable
        image_bytes = await page.screenshot(type="jpeg", quality=60)
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        return {"status": "success", "image_base64": img_b64}
    except Exception as e:
        return {"error": str(e)}

@app.get("/html")
async def get_html(selector: Optional[str] = None, element_id: Optional[int] = None):
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        if element_id is not None:
            selector = f'[data-ai-id="{element_id}"]'
            
        if selector:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                html = await locator.inner_html()
            else:
                return {"error": f"Selector {selector} not found"}
        else:
            html = await page.content()
            
        return {"status": "success", "html": html}
    except Exception as e:
        return {"error": str(e)}

@app.get("/markdown")
async def get_markdown(selector: Optional[str] = None, element_id: Optional[int] = None):
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        if element_id is not None:
            selector = f'[data-ai-id="{element_id}"]'
            
        if selector:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                html = await locator.inner_html()
            else:
                return {"error": f"Selector {selector} not found"}
        else:
            html = await page.content()
            
        md = markdownify.markdownify(html, heading_style="ATX")
        return {"status": "success", "markdown": md}
    except Exception as e:
        return {"error": str(e)}

@app.get("/network_data")
async def get_network_data():
    return {"status": "success", "responses": intercepted_responses}

@app.post("/auto_scroll")
async def auto_scroll():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        await page.evaluate('''async () => {
            await new Promise((resolve) => {
                let totalHeight = 0;
                let distance = 500;
                let timer = setInterval(() => {
                    let scrollHeight = document.body.scrollHeight;
                    window.scrollBy(0, distance);
                    totalHeight += distance;

                    if (totalHeight >= scrollHeight || totalHeight > 100000) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 200);
            });
        }''')
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/pdf")
async def get_pdf():
    global page
    page = await _ensure_page()
    if not page:
        return {"error": "Browser not initialized or closed"}
        
    try:
        client = await page.context.new_cdp_session(page)
        res = await client.send('Page.printToPDF', {
            "landscape": False,
            "printBackground": True,
            "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0
        })
        return {"status": "success", "pdf_base64": res.get("data")}
    except Exception as e:
        return {"error": "Could not generate PDF: " + str(e)}

@app.get("/status")
async def get_status():
    global page
    page = await _ensure_page()
    if not page:
         return {"status": "offline"}
    return {"status": "online", "current_url": page.url}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
