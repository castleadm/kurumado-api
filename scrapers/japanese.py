"""
日本市場スクレイパー: カーセンサー・グーネット・Yahoo!オークション・楽天Car
"""
import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CURRENT_YEAR = 2026

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _get(url: str, params: dict = None, timeout: int = 10) -> requests.Response | None:
    try:
        time.sleep(random.uniform(0.5, 1.5))
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None


def _parse_price_man(text: str) -> float | None:
    """価格テキストから万円単位の数値を抽出"""
    text = text.replace(",", "").replace(" ", "").replace("　", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{3,8})", text)
    if m:
        val = int(m.group(1))
        if val > 10000:
            return round(val / 10000, 1)
        if 10 <= val <= 5000:
            return float(val)
    return None


def _parse_mileage_km(text: str) -> int | None:
    """走行距離テキストからkm単位の数値を抽出"""
    text = text.replace(",", "").replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*km", text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d+)\s*km", text)
    if m:
        return int(m.group(1))
    return None


def _parse_year(text: str) -> int | None:
    """年式テキストから西暦年を抽出"""
    m = re.search(r"(20\d{2}|19\d{2})", text)
    if m:
        return int(m.group(1))
    # 元号変換
    wareki = {
        "令和": 2018, "平成": 1988, "昭和": 1925
    }
    for era, base in wareki.items():
        m = re.search(era + r"(\d+)", text)
        if m:
            return base + int(m.group(1))
    return None


def scrape_carsensor(maker: str, model: str) -> list[dict]:
    """カーセンサーから価格データを取得"""
    query = f"{maker} {model}"
    url = "https://www.carsensor.net/usedcar/search.php"
    params = {"KEYWORD": query, "SORT": "OLD"}

    resp = _get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    # カーセンサーの車両カード候補セレクタ
    cards = (
        soup.select(".cs-stockItem")
        or soup.select(".cassette")
        or soup.select("[class*='stockItem']")
        or soup.select("[class*='carItem']")
        or soup.select("article")
    )

    for card in cards[:60]:
        price = _extract_text_by_patterns(card, [
            "[class*='price']", "[class*='Price']", ".price", ".Price"
        ])
        year_text = _extract_text_by_patterns(card, [
            "[class*='year']", "[class*='Year']", "[class*='nensha']",
            "[class*='model-year']"
        ])
        mileage_text = _extract_text_by_patterns(card, [
            "[class*='mileage']", "[class*='Mileage']", "[class*='走行']",
            "[class*='km']"
        ])

        price_val = _parse_price_man(price) if price else None
        year_val = _parse_year(year_text) if year_text else None
        mileage_val = _parse_mileage_km(mileage_text) if mileage_text else None

        if price_val and year_val:
            listings.append({
                "price": price_val,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 10000),
                "source": "carsensor",
            })

    logger.info(f"carsensor: {len(listings)} listings for '{query}'")
    return listings


def scrape_goonet(maker: str, model: str) -> list[dict]:
    """グーネットから価格データを取得"""
    query = f"{maker} {model}"
    url = "https://www.goo-net.com/usedcar/search/"
    params = {"keyword": query}

    resp = _get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    cards = (
        soup.select(".goodslist_item")
        or soup.select(".search-list-item")
        or soup.select("[class*='carList']")
        or soup.select("[class*='listItem']")
        or soup.select(".cassette")
        or soup.select("li[class*='item']")
    )

    for card in cards[:60]:
        price = _extract_text_by_patterns(card, [
            "[class*='price']", "[class*='Price']", ".price_num", ".car_price"
        ])
        year_text = _extract_text_by_patterns(card, [
            "[class*='year']", "[class*='nensha']", "[class*='model']"
        ])
        mileage_text = _extract_text_by_patterns(card, [
            "[class*='mileage']", "[class*='km']", "[class*='走行']"
        ])

        price_val = _parse_price_man(price) if price else None
        year_val = _parse_year(year_text) if year_text else None
        mileage_val = _parse_mileage_km(mileage_text) if mileage_text else None

        if price_val and year_val:
            listings.append({
                "price": price_val,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 10000),
                "source": "goonet",
            })

    logger.info(f"goonet: {len(listings)} listings for '{query}'")
    return listings


def scrape_yahoo_auction(maker: str, model: str) -> list[dict]:
    """Yahoo!オークションから価格データを取得"""
    query = f"{maker} {model} 中古車"
    url = "https://auctions.yahoo.co.jp/search/search"
    params = {
        "p": query,
        "auccat": "2084005166",  # 乗用車カテゴリ
        "va": query,
        "b": "1",
        "n": "50",
    }

    resp = _get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    items = (
        soup.select(".Product")
        or soup.select("[class*='auction']")
        or soup.select("li[class*='item']")
        or soup.select(".item")
    )

    for item in items[:60]:
        price = _extract_text_by_patterns(item, [
            "[class*='price']", "[class*='Price']", ".Product__price",
            "[class*='bid']", "[class*='Bid']"
        ])
        # Yahoo!オークションは年式をタイトルから抽出
        title = item.get_text()
        year_val = _parse_year(title)
        price_val = _parse_price_man(price) if price else None
        mileage_val = _parse_mileage_km(title)

        if price_val and year_val and 10 <= price_val <= 5000:
            listings.append({
                "price": price_val,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 10000),
                "source": "yahoo_auction",
            })

    logger.info(f"yahoo_auction: {len(listings)} listings for '{query}'")
    return listings


def scrape_rakuten_car(maker: str, model: str) -> list[dict]:
    """楽天Carから価格データを取得"""
    query = f"{maker} {model}"
    url = "https://car.rakuten.co.jp/usedcar/"
    params = {"keyword": query}

    resp = _get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    cards = (
        soup.select(".car-item")
        or soup.select("[class*='carItem']")
        or soup.select("[class*='list-item']")
        or soup.select(".item-wrap")
        or soup.select("li[class*='item']")
    )

    for card in cards[:60]:
        price = _extract_text_by_patterns(card, [
            "[class*='price']", ".price", ".Price", "[class*='Price']"
        ])
        year_text = _extract_text_by_patterns(card, [
            "[class*='year']", "[class*='nensha']"
        ])
        mileage_text = _extract_text_by_patterns(card, [
            "[class*='mileage']", "[class*='km']"
        ])

        price_val = _parse_price_man(price) if price else None
        year_val = _parse_year(year_text) if year_text else None
        mileage_val = _parse_mileage_km(mileage_text) if mileage_text else None

        if price_val and year_val:
            listings.append({
                "price": price_val,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 10000),
                "source": "rakuten_car",
            })

    logger.info(f"rakuten_car: {len(listings)} listings for '{query}'")
    return listings


def _extract_text_by_patterns(soup_elem, selectors: list[str]) -> str | None:
    for sel in selectors:
        try:
            found = soup_elem.select_one(sel)
            if found:
                text = found.get_text(strip=True)
                if text:
                    return text
        except Exception:
            continue
    return None


def scrape_all_japanese(maker: str, model: str) -> list[dict]:
    """全日本サイトを並列スクレイピング"""
    scrapers = [
        ("carsensor", scrape_carsensor),
        ("goonet", scrape_goonet),
        ("yahoo_auction", scrape_yahoo_auction),
        ("rakuten_car", scrape_rakuten_car),
    ]

    all_listings = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fn, maker, model): name
            for name, fn in scrapers
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result(timeout=20)
                all_listings.extend(results)
            except Exception as e:
                logger.warning(f"{name} scraper error: {e}")

    # 価格フィルタリング（異常値除去）
    valid = [l for l in all_listings if 5 <= l["price"] <= 5000 and l["age"] >= 0]
    return valid
