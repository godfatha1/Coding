"""A reusable signed-in browser window, driven through the page itself.

SoundCloud has not granted new API applications in years, so the only way to
read and write your own likes is to do what you would do by hand: open the
site, look at the page, click the button. That is exactly what this does.

Scope, deliberately narrow:

* It never sees or stores your password. You sign in yourself, once, in a real
  browser window.
* It does not read, extract or transmit any token, key or credential. It does
  not call SoundCloud's internal endpoints. It reads the rendered page and
  clicks controls, the same operations a person performs.
* The only thing kept on disk is Playwright's storage state -- the ordinary
  browser profile that keeps you signed in between runs, the same as a browser
  on your own machine. It stays local, at mode 0600, and is encrypted when
  LIKESYNC_SECRET_KEY is set.

Playwright is an optional dependency (``pip install 'likesync[web]'``). The
``Page`` protocol keeps the providers testable without a live site.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Self

from .errors import AuthError, ConfigError
from .secretfile import read_secret_json, write_secret_json

log = logging.getLogger("likesync.websession")

HOME = "https://soundcloud.com"
LOGIN_TIMEOUT_S = 300.0
# Where a stock Playwright install and this image keep Chromium.
_KNOWN_BINARIES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


class Page(Protocol):
    """The page operations the providers need. Faked in tests."""

    def goto(self, url: str) -> None: ...

    def current_url(self) -> str: ...

    def extract(self, script: str) -> Any:
        """Evaluate a JS expression in the page and return its JSON result."""
        ...

    def click(self, selector: str, *, timeout_ms: int = 5_000) -> bool:
        """Click the first match. False if it never appeared."""
        ...

    def scroll_to_end(self, *, max_scrolls: int = 200, settle_ms: int = 450) -> bool:
        """Scroll until the list stops growing.

        Returns True if the bottom was reached, False if it ran out of
        scrolls while the page was still growing -- which means any list read
        afterwards is incomplete.
        """
        ...

    def wait(self, ms: int) -> None: ...


def find_browser() -> str | None:
    for candidate in _KNOWN_BINARIES:
        if Path(candidate).exists():
            return candidate
    return None


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise ConfigError(
            "SoundCloud web mode needs Playwright:\n"
            "  pip install 'likesync[web]'\n"
            "  python3 -m playwright install chromium"
        ) from exc
    return sync_playwright


@dataclass
class BrowserPage:
    """Playwright-backed :class:`Page`."""

    _page: Any
    min_interval_s: float = 0.4
    _last: float = field(default=0.0, init=False)

    def _pace(self) -> None:
        # One person's library is not worth hammering anyone's servers over.
        gap = time.monotonic() - self._last
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last = time.monotonic()

    def goto(self, url: str) -> None:
        self._pace()
        self._page.goto(url, wait_until="domcontentloaded")

    def current_url(self) -> str:
        return str(self._page.url)

    def extract(self, script: str) -> Any:
        return self._page.evaluate(script)

    def click(self, selector: str, *, timeout_ms: int = 5_000) -> bool:
        self._pace()
        try:
            locator = self._page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.click(timeout=timeout_ms)
            return True
        except Exception as exc:  # noqa: BLE001 - a missing control is a result
            log.debug("click %s failed: %s", selector, exc)
            return False

    def scroll_to_end(self, *, max_scrolls: int = 200, settle_ms: int = 450) -> bool:
        """SoundCloud paginates by infinite scroll, so walk to the bottom."""
        previous = -1
        scrolls = 0
        stable = 0
        while scrolls < max_scrolls:
            height = self._page.evaluate(
                "() => { window.scrollTo(0, document.body.scrollHeight);"
                " return document.body.scrollHeight; }"
            )
            self._page.wait_for_timeout(settle_ms)
            scrolls += 1
            if height == previous:
                stable += 1
                # Two quiet rounds: one can just be a slow fetch.
                if stable >= 2:
                    return True
            else:
                stable = 0
            previous = height
        log.warning("scrolled %d times and the page was still growing", scrolls)
        return False

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)


@dataclass
class BrowserSession:
    """Owns the Chromium process and the persisted sign-in."""

    state_path: Path
    executable_path: str | None = None
    headless: bool = True
    timeout_ms: int = 30_000

    _pw: Any = field(default=None, init=False, repr=False)
    _browser: Any = field(default=None, init=False, repr=False)
    _context: Any = field(default=None, init=False, repr=False)
    _page: Any = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False)

    # -- lifecycle ---------------------------------------------------------

    def _launch(self, *, headless: bool, with_state: bool) -> None:
        self._pw = _playwright()().start()
        launch_kw: dict[str, Any] = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        executable = self.executable_path or find_browser()
        if executable:
            launch_kw["executable_path"] = executable
        try:
            self._browser = self._pw.chromium.launch(**launch_kw)
        except Exception as exc:
            self._shutdown()
            raise ConfigError(
                f"could not launch Chromium: {exc}\n"
                "Install it with: python3 -m playwright install chromium\n"
                "Or set soundcloud.browser_executable to an existing binary."
            ) from exc

        context_kw: dict[str, Any] = {
            "viewport": {"width": 1400, "height": 1000},
            "locale": "en-GB",
        }
        state = read_secret_json(self.state_path) if with_state else None
        if state:
            context_kw["storage_state"] = state
        self._context = self._browser.new_context(**context_kw)
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()

    def _shutdown(self) -> None:
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001,S110 - see below
                    # Teardown runs on the error path too. A browser that is
                    # already gone must not mask the original failure.
                    pass
                setattr(self, attr, None)
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001,S110 - same reason as above
                pass
            self._pw = None
        self._started = False

    def _save_state(self) -> None:
        """Persist the refreshed sign-in cookies for the next run."""
        if self._context is None:
            return
        try:
            write_secret_json(self.state_path, self._context.storage_state())
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save the browser session: %s", exc)

    def close(self) -> None:
        if self._started:
            self._save_state()
        self._shutdown()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- use ---------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        if not self.state_path.exists():
            raise AuthError(
                "No saved SoundCloud browser session.\n"
                "Run this once on a machine with a screen:\n"
                "  likesync login soundcloud --web\n"
                "On a headless box, run it on your laptop and copy the session "
                "over with `likesync session export`."
            )
        self._launch(headless=self.headless, with_state=True)
        self._started = True

    def page(self) -> Page:
        if not self._started:
            self.start()
        return BrowserPage(self._page)

    def page_proxy(self) -> Page:
        """A Page that launches the browser only when first used.

        Commands like `status` must not spawn Chromium just by constructing
        the providers.
        """
        return LazyPage(self)

    def signed_in(self) -> bool:
        """Does the site treat us as signed in?

        Checked by what the page shows, not by inspecting any stored value.
        """
        page = self.page()
        page.goto(f"{HOME}/you/likes")
        page.wait(1500)
        url = page.current_url()
        if "/signin" in url or "/login" in url:
            return False
        return bool(
            page.extract(
                "() => !!document.querySelector("
                "'.header__userNavUsernameButton, [class*=userNav], "
                "a[href$=\"/you/likes\"], .profileMenu')"
            )
        )

    # -- interactive sign-in ----------------------------------------------

    def login(self) -> None:
        """Open a window and wait for the user to sign in themselves."""
        self._launch(headless=False, with_state=self.state_path.exists())
        print(
            "\nA browser window is opening on soundcloud.com.\n"
            "Sign in exactly as you normally would — including Google or "
            "Apple sign-in if that is what you use.\n"
            "likesync never sees your password. It only keeps the browser "
            "profile afterwards, so it stays signed in between runs.\n"
            "The window closes itself once sign-in is detected."
        )
        page = BrowserPage(self._page)
        page.goto(f"{HOME}/signin")

        deadline = time.time() + LOGIN_TIMEOUT_S
        while time.time() < deadline:
            self._page.wait_for_timeout(1500)
            url = page.current_url()
            if "/signin" in url or "/login" in url:
                continue
            # Away from the sign-in page: confirm against the real likes page.
            if self.signed_in():
                break
        else:
            self._shutdown()
            raise AuthError(
                "timed out waiting for sign-in. Re-run `likesync login "
                "soundcloud --web` and complete it within 5 minutes."
            )

        self._started = True
        self._save_state()
        print(f"\nSigned in. Browser profile saved to {self.state_path}")
        print("Verify any time with: likesync probe soundcloud")


@dataclass
class LazyPage:
    """Defers opening the browser until a page operation is actually needed."""

    session: BrowserSession
    _inner: Page | None = field(default=None, init=False, repr=False)

    def _page(self) -> Page:
        if self._inner is None:
            self._inner = self.session.page()
        return self._inner

    def goto(self, url: str) -> None:
        self._page().goto(url)

    def current_url(self) -> str:
        return self._page().current_url()

    def extract(self, script: str) -> Any:
        return self._page().extract(script)

    def click(self, selector: str, *, timeout_ms: int = 5_000) -> bool:
        return self._page().click(selector, timeout_ms=timeout_ms)

    def scroll_to_end(self, *, max_scrolls: int = 200, settle_ms: int = 450) -> bool:
        return self._page().scroll_to_end(max_scrolls=max_scrolls, settle_ms=settle_ms)

    def wait(self, ms: int) -> None:
        self._page().wait(ms)
