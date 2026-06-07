"""
Playwright ベースのスクレイパー（stealth対応）
JavaScript レンダリング + 봇検出回避 でカーセンサー・グーネット・
Yahoo!カーから同条件の現在市場価格を取得する
"""
import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
CURRENT_YEAR = 2026


# ───────────────────────────────── テキスト解析ユーティリティ ─────────
def _parse_price_man(text: str) -> float | None:
    t = text.replace(",", "").replace(" ", "").replace("　", "")
    # 万円 パターン
    for pat in [r"(\d+(?:\.\d+)?)\s*万(?:円)?", r"¥\s*(\d+(?:\.\d+)?)\s*万"]:
        m = re.search(pat, t)
        if m:
            v = float(m.group(1))
            if 10 <= v <= 9000:
                return v
    return None


def _parse_mileage_km(text: str) -> int | None:
    t = text.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*[Kk][Mm]", t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d{3,7})\s*[Kk][Mm]", t)
    if m:
        v = int(m.group(1))
        return v if v < 1_000_000 else None
    return None


def _parse_year(text: str) -> int | None:
    # "2019年" or "R1" (令和1 = 2019) or "H30" (平成30 = 2018)
    m = re.search(r"(20\d{2}|19\d{2})\s*年", text)
    if m:
        y = int(m.group(1))
        return y if 1990 <= y <= CURRENT_YEAR else None
    # 令和
    m = re.search(r"R\s*(\d+)", text)
    if m:
        y = 2018 + int(m.group(1))
        return y if 1990 <= y <= CURRENT_YEAR else None
    # 平成
    m = re.search(r"H\s*(\d+)", text)
    if m:
        y = 1988 + int(m.group(1))
        return y if 1990 <= y <= CURRENT_YEAR else None
    return None


# ──────────────────────────── HTML からリスティング抽出 ────────────────
def _extract_from_html(html: str, source: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    listings = []

    # site-specific selectors (ordered by specificity)
    selectors_by_source = {
        "carsensor": [
            "[class*='cassetteItem']", ".cassette", ".cs-stockItem",
            "[class*='StockItem']", "[class*='stock-item']",
            "div[class*='car-']", "li[class*='car-']",
            "article", ".item",
        ],
        "goonet": [
            "[class*='goodslist_item']", "[class*='listItem']",
            "[class*='list-item']", "[class*='CarItem']",
            "[class*='car-item']", "article", ".item",
        ],
        "yahoo_car": [
            "[class*='Product']", "[class*='product']",
            "[class*='item']", "li.result", "div.result",
        ],
        "yahoo_auction": [
            "[class*='Product']", "[class*='product']",
            "li[class*='item']", ".cf li",
        ],
    }

    selectors = selectors_by_source.get(source, ["article", ".item", "li"])

    cards = []
    for sel in selectors:
        try:
            cards = soup.select(sel)
            if len(cards) >= 2:
                break
        except Exception:
            continue

    if cards:
        for card in cards[:120]:
            text = card.get_text(separator=" ", strip=True)
            _try_append(text, source, listings)
    else:
        full_text = soup.get_text(separator="\n")
        listings = _sliding_window_extract(full_text, source)

    return listings


def _try_append(text: str, source: str, out: list):
    price = _parse_price_man(text)
    year = _parse_year(text)
    mileage = _parse_mileage_km(text)
    if price and year:
        out.append({
            "price": price,
            "year": year,
            "age": max(0, CURRENT_YEAR - year),
            "mileage": mileage or max(0, CURRENT_YEAR - year) * 10000,
            "source": source,
        })


def _sliding_window_extract(text: str, source: str) -> list[dict]:
    WINDOW = 500
    listings = []

    price_hits = [
        (m.start(), float(m.group(1)))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*万(?:円)?", text)
        if 10 <= float(m.group(1)) <= 9000
    ]
    year_hits = [
        (m.start(), int(m.group(1)))
        for m in re.finditer(r"(20\d{2}|19\d{2})\s*年", text)
        if 1990 <= int(m.group(1)) <= CURRENT_YEAR
    ]
    mile_hits = [
        (m.start(), int(float(m.group(1)) * 10000))
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*万\s*[Kk][Mm]", text)
    ]

    seen_prices = set()
    for ppos, price in price_hits:
        if price in seen_prices:
            continue
        nearby_years = [y for ypos, y in year_hits if abs(ypos - ppos) < WINDOW]
        if not nearby_years:
            continue
        year = nearby_years[0]
        nearby_miles = [m for mpos, m in mile_hits if abs(mpos - ppos) < WINDOW]
        mileage = nearby_miles[0] if nearby_miles else max(0, CURRENT_YEAR - year) * 10000
        seen_prices.add(price)
        listings.append({
            "price": price,
            "year": year,
            "age": max(0, CURRENT_YEAR - year),
            "mileage": mileage,
            "source": source,
        })

    return listings


# ──────────────────────────── Playwright 共通ドライバ ─────────────────
def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return True
    except ImportError:
        return False


def _apply_stealth(page):
    """playwright-stealth が使えれば適用、なければ手動パッチ"""
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        return
    except ImportError:
        pass
    # 手動 stealth パッチ
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP','ja','en-US','en']});
        window.chrome = {runtime: {}};
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    """)


def _scrape_site(url: str, source: str,
                 wait_selector: str | None = None,
                 extra_wait_ms: int = 4000) -> list[dict]:
    if not _playwright_available():
        logger.warning("Playwright not installed")
        return []
    from playwright.sync_api import sync_playwright

    listings = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.6367.201 Safari/537.36"
            ),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        page = ctx.new_page()
        _apply_stealth(page)
        page.set_default_timeout(25000)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # ランダム遅延でボット検出を回避
            page.wait_for_timeout(extra_wait_ms + random.randint(500, 1500))

            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass

            # スクロールしてコンテンツをロード
            page.evaluate("window.scrollTo(0, 300)")
            page.wait_for_timeout(800)

            html = page.content()
            listings = _extract_from_html(html, source)
            logger.info(f"{source} (playwright): {len(listings)} listings from {url[:80]}")

            # デバッグ用：結果が0件なら一部HTMLを記録
            if not listings:
                snippet = html[:2000].replace("\n", " ")
                logger.debug(f"{source} HTML snippet: {snippet}")

        except Exception as e:
            logger.warning(f"{source} playwright error: {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return listings


# ──────────────────────────── 各サイトスクレイパー ───────────────────
def scrape_carsensor(maker: str, model: str, year: int) -> list[dict]:
    y_from = max(year - 1, 1990)
    y_to = min(year + 1, CURRENT_YEAR)
    # カーセンサー 検索 URL（実際のパラメータ形式）
    keyword = quote(f"{maker} {model}")
    url = (
        f"https://www.carsensor.net/usedcar/search.php"
        f"?KEYWORD={keyword}"
        f"&NENSHA_START={y_from}&NENSHA_END={y_to}"
        f"&SORT=NEW&LIMIT=30"
    )
    return _scrape_site(
        url, "carsensor",
        wait_selector="[class*='cassetteItem'],[class*='stockItem'],article.cassette",
        extra_wait_ms=4500,
    )


def scrape_goonet(maker: str, model: str, year: int) -> list[dict]:
    y_from = max(year - 1, 1990)
    y_to = min(year + 1, CURRENT_YEAR)
    keyword = quote(f"{maker} {model}")
    # グーネット 検索 URL
    url = (
        f"https://www.goo-net.com/usedcar/search/"
        f"?keyword={keyword}&nen1={y_from}&nen2={y_to}&limit=30"
    )
    return _scrape_site(
        url, "goonet",
        wait_selector="[class*='goodslist'],[class*='listItem'],[class*='CarItem']",
        extra_wait_ms=4500,
    )


def scrape_yahoo_car(maker: str, model: str, year: int) -> list[dict]:
    """Yahoo!カーナビ（cars.yahoo.co.jp）"""
    y_from = max(year - 1, 1990)
    y_to = min(year + 1, CURRENT_YEAR)
    keyword = quote(f"{maker} {model}")
    url = (
        f"https://cars.yahoo.co.jp/used/search"
        f"?keyword={keyword}&minYear={y_from}&maxYear={y_to}&sort=price"
    )
    return _scrape_site(
        url, "yahoo_car",
        wait_selector="[class*='SearchResult'],[class*='CarItem'],[class*='item']",
        extra_wait_ms=4000,
    )


def scrape_yahoo_auction(maker: str, model: str, year: int) -> list[dict]:
    query = quote(f"{maker} {model} {year}年")
    url = (
        f"https://auctions.yahoo.co.jp/search/search"
        f"?p={query}&auccat=2084005166&b=1&n=50&s1=cbids&o1=d"
    )
    return _scrape_site(
        url, "yahoo_auction",
        wait_selector="[class*='Product'],[class*='item']",
        extra_wait_ms=3500,
    )


# ──────────────────────────── requests+BS4 フォールバック ─────────────
def _requests_scrape(url: str, source: str) -> list[dict]:
    """Playwright が完全ブロックされた場合の requests フォールバック"""
    import requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.6367.201 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.co.jp/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return _extract_from_html(resp.text, source)
    except Exception as e:
        logger.debug(f"{source} requests fallback error: {e}")
    return []


# ──────────────────────────── 並列スクレイピング ──────────────────────
def scrape_current_price(maker: str, model: str, year: int, mileage: int) -> dict:
    """
    現在の市場価格を並列スクレイピングで取得
    Returns:
        {
          "listings": [...],
          "current_price": float | None,
          "matched_count": int,
        }
    """
    tasks = [
        ("carsensor",    scrape_carsensor,    (maker, model, year)),
        ("goonet",       scrape_goonet,        (maker, model, year)),
        ("yahoo_car",    scrape_yahoo_car,     (maker, model, year)),
        ("yahoo_auction", scrape_yahoo_auction, (maker, model, year)),
    ]

    all_listings = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn, *args): name for name, fn, args in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result(timeout=40)
                all_listings.extend(results)
                logger.info(f"{name}: {len(results)} listings")
            except Exception as e:
                logger.warning(f"{name} failed: {e}")

    # 価格フィルター
    all_listings = [l for l in all_listings if 10 <= l["price"] <= 9000]

    # 同年式 (±1年) フィルター
    year_matches = [l for l in all_listings if abs(l["year"] - year) <= 1]

    # 走行距離フィルター (±50%)
    matched = year_matches
    if year_matches and mileage > 0:
        mileage_ok = [
            l for l in year_matches
            if abs(l["mileage"] - mileage) <= max(mileage * 0.5, 20000)
        ]
        if len(mileage_ok) >= 2:
            matched = mileage_ok

    current_price = None
    if matched:
        import numpy as np
        prices = sorted(l["price"] for l in matched)
        if len(prices) >= 4:
            q1 = prices[len(prices) // 4]
            q3 = prices[3 * len(prices) // 4]
            iqr = q3 - q1
            prices = [p for p in prices if q1 - 1.5 * iqr <= p <= q3 + 1.5 * iqr]
        if prices:
            current_price = float(np.median(prices))

    logger.info(
        f"scrape_current_price: total={len(all_listings)}, "
        f"year_match={len(year_matches)}, matched={len(matched)}, "
        f"price={current_price}"
    )

    return {
        "listings": all_listings,
        "year_matched": year_matches,
        "current_price": current_price,
        "matched_count": len(matched),
    }
