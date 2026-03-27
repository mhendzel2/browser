from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import markdownify
from fastapi import FastAPI
from pydantic import BaseModel, Field
from playwright.async_api import BrowserContext, Page, Playwright, Response, Route, async_playwright

logger = logging.getLogger(__name__)

_BLOCKED_RESOURCE_TYPES = {"font", "image", "media", "stylesheet"}
_JSON_RESOURCE_TYPES = {"fetch", "xhr"}
_MAX_CAPTURED_RESPONSES = 200


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_asset_path(filename: str) -> Path:
    here = Path(__file__).resolve().parent
    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in [here, *here.parents, Path.cwd()]:
        if base in seen:
            continue
        seen.add(base)
        candidates.append(base / filename)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Unable to locate required asset: {filename}")


@dataclass(slots=True)
class BrowserSettings:
    user_data_dir: Path
    headless: bool
    channel: str
    viewport_width: int
    viewport_height: int
    no_viewport: bool
    ignore_https_errors: bool
    locale: str | None
    timezone_id: str | None
    action_timeout_ms: int
    navigation_timeout_ms: int
    launch_timeout_ms: int
    block_service_workers: bool

    @classmethod
    def from_env(cls) -> BrowserSettings:
        return cls(
            user_data_dir=Path(os.getenv("BROWSER_USER_DATA_DIR", str(Path.cwd() / "browser_data"))),
            headless=_parse_bool(os.getenv("BROWSER_HEADLESS"), False),
            channel=os.getenv("BROWSER_CHANNEL", "chromium").strip() or "chromium",
            viewport_width=int(os.getenv("BROWSER_VIEWPORT_WIDTH", "1440")),
            viewport_height=int(os.getenv("BROWSER_VIEWPORT_HEIGHT", "960")),
            no_viewport=_parse_bool(os.getenv("BROWSER_NO_VIEWPORT"), False),
            ignore_https_errors=_parse_bool(os.getenv("BROWSER_IGNORE_HTTPS_ERRORS"), True),
            locale=_optional_str(os.getenv("BROWSER_LOCALE")),
            timezone_id=_optional_str(os.getenv("BROWSER_TIMEZONE")),
            action_timeout_ms=int(os.getenv("BROWSER_ACTION_TIMEOUT_MS", "15000")),
            navigation_timeout_ms=int(os.getenv("BROWSER_NAVIGATION_TIMEOUT_MS", "30000")),
            launch_timeout_ms=int(os.getenv("BROWSER_LAUNCH_TIMEOUT_MS", "30000")),
            block_service_workers=_parse_bool(os.getenv("BROWSER_BLOCK_SERVICE_WORKERS"), True),
        )


class BrowserRuntime:
    def __init__(self, settings: BrowserSettings) -> None:
        self.settings = settings
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._startup_lock = asyncio.Lock()
        self._route_installed = False
        self._captured_responses: deque[dict[str, Any]] = deque(maxlen=_MAX_CAPTURED_RESPONSES)

    async def start(self) -> None:
        if self.context is not None:
            return
        async with self._startup_lock:
            if self.context is not None:
                return

            self.settings.user_data_dir.mkdir(parents=True, exist_ok=True)
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.user_data_dir),
                channel=self.settings.channel,
                headless=self.settings.headless,
                viewport=None if self.settings.no_viewport else {
                    "width": self.settings.viewport_width,
                    "height": self.settings.viewport_height,
                },
                no_viewport=self.settings.no_viewport,
                ignore_https_errors=self.settings.ignore_https_errors,
                accept_downloads=True,
                service_workers="block" if self.settings.block_service_workers else "allow",
                locale=self.settings.locale,
                timezone_id=self.settings.timezone_id,
                timeout=self.settings.launch_timeout_ms,
            )
            self.context.set_default_timeout(self.settings.action_timeout_ms)
            self.context.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
            self.context.on("response", lambda response: asyncio.create_task(self._capture_response(response)))
            self.context.on("page", lambda page: asyncio.create_task(self._track_page(page)))
            self.context.on("weberror", lambda error: logger.warning("Browser page error: %s", error))
            await self.ensure_page()
            logger.info(
                "Browser started with channel=%s headless=%s profile=%s",
                self.settings.channel,
                self.settings.headless,
                self.settings.user_data_dir,
            )

    async def stop(self) -> None:
        if self.context is not None:
            await self.context.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.playwright = None
        self.context = None
        self.page = None
        self._route_installed = False
        self._captured_responses.clear()

    async def ensure_page(self) -> Page:
        await self.start()
        if self.context is None:
            raise RuntimeError("Browser context is unavailable")

        if self.page is not None and not self.page.is_closed():
            return self.page

        pages = [open_page for open_page in self.context.pages if not open_page.is_closed()]
        if pages:
            self.page = pages[0]
        else:
            self.page = await self.context.new_page()
        return self.page

    async def navigate(self, url: str, *, fast_mode: bool) -> dict[str, Any]:
        page = await self.ensure_page()
        await self._configure_routing(page, enabled=fast_mode)
        self._captured_responses.clear()
        response = await page.goto(url, wait_until="domcontentloaded")
        return {
            "status": "success",
            "url": page.url,
            "title": await page.title(),
            "http_status": response.status if response is not None else None,
        }

    async def click(self, element_id: int) -> dict[str, Any]:
        page = await self.ensure_page()
        await page.locator(f'[data-ai-id="{element_id}"]').click(timeout=3_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass
        return {"status": "success", "current_url": page.url}

    async def type_text(self, element_id: int, text: str) -> dict[str, Any]:
        page = await self.ensure_page()
        await page.locator(f'[data-ai-id="{element_id}"]').fill(text)
        return {"status": "success", "text_typed": text}

    async def scroll(self, direction: str, amount: int) -> dict[str, Any]:
        page = await self.ensure_page()
        sign = 1 if direction == "down" else -1
        await page.evaluate("distance => window.scrollBy(0, distance)", sign * amount)
        return {"status": "success"}

    async def auto_scroll(self) -> dict[str, Any]:
        page = await self.ensure_page()
        await page.evaluate(
            """
            async () => {
              await new Promise((resolve) => {
                let totalHeight = 0;
                const distance = 500;
                const timer = setInterval(() => {
                  const scrollHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                  window.scrollBy(0, distance);
                  totalHeight += distance;
                  if (totalHeight >= scrollHeight || totalHeight > 100000) {
                    clearInterval(timer);
                    resolve();
                  }
                }, 200);
              });
            }
            """
        )
        return {"status": "success"}

    async def screenshot(self) -> dict[str, Any]:
        page = await self.ensure_page()
        image_bytes = await page.screenshot(type="jpeg", quality=60)
        return {
            "status": "success",
            "image_base64": base64.b64encode(image_bytes).decode("utf-8"),
        }

    async def html(self, selector: str | None, element_id: int | None) -> dict[str, Any]:
        page = await self.ensure_page()
        target_selector = f'[data-ai-id="{element_id}"]' if element_id is not None else selector
        html = await self._read_html(page, target_selector)
        return {"status": "success", "html": html}

    async def markdown(self, selector: str | None, element_id: int | None) -> dict[str, Any]:
        page = await self.ensure_page()
        target_selector = f'[data-ai-id="{element_id}"]' if element_id is not None else selector
        html = await self._read_html(page, target_selector)
        return {
            "status": "success",
            "markdown": markdownify.markdownify(html, heading_style="ATX"),
        }

    async def readability(self) -> dict[str, Any]:
        page = await self.ensure_page()
        await page.evaluate(
            """
            async () => {
              if (window.Readability) {
                return;
              }
              await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://unpkg.com/@mozilla/readability/Readability.js';
                script.onload = () => resolve();
                script.onerror = () => reject(new Error('Failed to load Readability.js'));
                document.head.appendChild(script);
              });
            }
            """
        )
        article = await page.evaluate(
            """
            () => {
              try {
                const documentClone = document.cloneNode(true);
                const reader = new Readability(documentClone);
                return reader.parse();
              } catch (error) {
                return { error: String(error) };
              }
            }
            """
        )
        return {"status": "success", "article": article}

    async def accessibility(self) -> dict[str, Any]:
        page = await self.ensure_page()
        client = await page.context.new_cdp_session(page)
        snapshot = await client.send("Accessibility.getFullAXTree")
        return {"status": "success", "accessibility_tree": snapshot}

    async def pdf(self) -> dict[str, Any]:
        page = await self.ensure_page()
        client = await page.context.new_cdp_session(page)
        pdf_payload = await client.send(
            "Page.printToPDF",
            {
                "landscape": False,
                "printBackground": True,
                "marginTop": 0,
                "marginBottom": 0,
                "marginLeft": 0,
                "marginRight": 0,
            },
        )
        return {"status": "success", "pdf_base64": pdf_payload.get("data")}

    async def tag_elements(self) -> dict[str, Any]:
        page = await self.ensure_page()
        script_path = _resolve_asset_path("tagger.js")
        element_map = await page.evaluate(script_path.read_text(encoding="utf-8"))
        return {"status": "success", "elements": element_map}

    async def status(self) -> dict[str, Any]:
        page = await self.ensure_page()
        browser = page.context.browser
        return {
            "status": "online",
            "current_url": page.url,
            "headless": self.settings.headless,
            "channel": self.settings.channel,
            "browser_version": browser.version if browser is not None else None,
        }

    async def browser_info(self) -> dict[str, Any]:
        page = await self.ensure_page()
        navigator_data = await page.evaluate(
            """
            () => ({
              userAgent: navigator.userAgent,
              language: navigator.language,
              languages: navigator.languages,
              webdriver: navigator.webdriver,
              platform: navigator.platform,
            })
            """
        )
        browser = page.context.browser
        return {
            "status": "success",
            "channel": self.settings.channel,
            "headless": self.settings.headless,
            "browser_version": browser.version if browser is not None else None,
            "navigator": navigator_data,
        }

    def network_data(self) -> dict[str, Any]:
        return {"status": "success", "responses": list(self._captured_responses)}

    async def _track_page(self, page: Page) -> None:
        self.page = page
        if self._route_installed:
            await self._configure_routing(page, enabled=True)

    async def _capture_response(self, response: Response) -> None:
        if response.request.resource_type not in _JSON_RESOURCE_TYPES:
            return
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return
        try:
            body = await response.json()
        except Exception:
            return
        self._captured_responses.append(
            {
                "url": response.url,
                "status": response.status,
                "method": response.request.method,
                "json": body,
            }
        )

    async def _configure_routing(self, page: Page, *, enabled: bool) -> None:
        try:
            await page.unroute("**/*", self._route_intercept)
        except Exception:
            pass
        self._route_installed = False
        if enabled:
            await page.route("**/*", self._route_intercept)
            self._route_installed = True

    async def _route_intercept(self, route: Route) -> None:
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        await route.continue_()

    async def _read_html(self, page: Page, selector: str | None) -> str:
        if not selector:
            return await page.content()
        locator = page.locator(selector)
        if await locator.count() == 0:
            raise ValueError(f"Selector {selector} not found")
        return await locator.first.inner_html()


settings = BrowserSettings.from_env()
runtime = BrowserRuntime(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="AI Browser API", lifespan=lifespan)


class NavigateRequest(BaseModel):
    url: str = Field(min_length=1)
    fast_mode: bool = False


class ClickRequest(BaseModel):
    element_id: int


class TypeRequest(BaseModel):
    element_id: int
    text: str


class ScrollRequest(BaseModel):
    direction: str = Field(default="down", pattern="^(down|up)$")
    amount: int = Field(default=500, ge=1, le=100000)


@app.post("/navigate")
async def navigate_to_url(request: NavigateRequest):
    try:
        return await runtime.navigate(request.url, fast_mode=request.fast_mode)
    except Exception as exc:
        logger.warning("Navigation failed for %s", request.url, exc_info=True)
        return {"error": str(exc)}


@app.get("/tag_elements")
async def tag_elements():
    try:
        return await runtime.tag_elements()
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/click")
async def click_element(request: ClickRequest):
    try:
        return await runtime.click(request.element_id)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/type")
async def type_text(request: TypeRequest):
    try:
        return await runtime.type_text(request.element_id, request.text)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/scroll")
async def scroll_page(request: ScrollRequest):
    try:
        return await runtime.scroll(request.direction, request.amount)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/auto_scroll")
async def auto_scroll():
    try:
        return await runtime.auto_scroll()
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/accessibility")
async def get_accessibility():
    try:
        return await runtime.accessibility()
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/readability")
async def get_readability():
    try:
        return await runtime.readability()
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/screenshot")
async def get_screenshot():
    try:
        return await runtime.screenshot()
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/html")
async def get_html(selector: str | None = None, element_id: int | None = None):
    try:
        return await runtime.html(selector, element_id)
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/markdown")
async def get_markdown(selector: str | None = None, element_id: int | None = None):
    try:
        return await runtime.markdown(selector, element_id)
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/network_data")
async def get_network_data():
    return runtime.network_data()


@app.get("/pdf")
async def get_pdf():
    try:
        return await runtime.pdf()
    except Exception as exc:
        return {"error": f"Could not generate PDF: {exc}"}


@app.get("/status")
async def get_status():
    try:
        return await runtime.status()
    except Exception:
        return {"status": "offline"}


@app.get("/browser_info")
async def get_browser_info():
    try:
        return await runtime.browser_info()
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
