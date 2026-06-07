"""
海外市場スクレイパー
- AutoScout24（欧州）: __NEXT_DATA__ JSON から価格・年式取得（requests のみ）
- KBB（米国）/ AutoTrader UK: 減価傾向補正用
"""
import re
import json
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from utils.fx import get_rate_to_jpy

logger = logging.getLogger(__name__)
CURRENT_YEAR = 2026

# ── AutoScout24 メーカー/モデル名マッピング ───────────────────────────
_AS24_MAKER = {
    "ポルシェ": "porsche", "porsche": "porsche",
    "メルセデス": "mercedes-benz", "メルセデスベンツ": "mercedes-benz",
    "メルセデスamg": "mercedes-benz", "mercedes": "mercedes-benz",
    "bmw": "bmw", "BMW": "bmw",
    "アウディ": "audi", "audi": "audi",
    "フォルクスワーゲン": "volkswagen", "volkswagen": "volkswagen", "vw": "volkswagen",
    "ボルボ": "volvo", "volvo": "volvo",
    "ランドローバー": "land-rover", "land rover": "land-rover", "land_rover": "land-rover",
    "トヨタ": "toyota", "toyota": "toyota",
    "ホンダ": "honda", "honda": "honda",
    "日産": "nissan", "nissan": "nissan",
    "レクサス": "lexus", "lexus": "lexus",
    "マツダ": "mazda", "mazda": "mazda",
    "スバル": "subaru", "subaru": "subaru",
}
_AS24_MODEL = {
    "マカン": "macan", "カイエン": "cayenne", "パナメーラ": "panamera",
    "タイカン": "taycan", "ボクスター": "boxster", "ケイマン": "cayman",
    "cクラス": "c-class", "eクラス": "e-class", "sクラス": "s-class",
    "gクラス": "g-class", "グレード": "glc", "glc": "glc", "gle": "gle",
    "e63s": "e-class", "e63": "e-class", "c63s": "c-class", "c63": "c-class",
    "g63": "g-class", "s63": "s-class",
    "3シリーズ": "3er", "3series": "3er", "5シリーズ": "5er", "5series": "5er",
    "7シリーズ": "7er", "x3": "x3", "x5": "x5", "m3": "m3", "m5": "m5",
    "ディフェンダー": "defender", "defender": "defender",
    "レンジローバー": "range-rover", "range rover": "range-rover",
    "プリウス": "prius", "ランドクルーザー": "land-cruiser", "ランクル": "land-cruiser",
    "ジムニー": "jimny",
}

# 輸出コスト係数: 海外売値 → 日本での買取価格下限
# 日本から欧州輸出のコスト(輸送+関税+諸経費): 約15-20%
_EXPORT_COST_RATIO = 0.80


def _as24_maker_model(maker: str, model: str) -> tuple[str, str] | None:
    """AutoScout24 用 maker/model スラッグを返す"""
    maker_l = maker.lower().replace(" ", "").replace("　", "")
    model_l = model.lower().replace(" ", "").replace("　", "")

    as_maker = None
    for k, v in _AS24_MAKER.items():
        if k.lower().replace(" ", "") in maker_l or maker_l in k.lower().replace(" ", ""):
            as_maker = v
            break

    as_model = None
    for k, v in _AS24_MODEL.items():
        if k.lower().replace(" ", "") in model_l or model_l in k.lower().replace(" ", ""):
            as_model = v
            break
    if not as_model:
        # 英語モデル名をそのままスラッグ化
        as_model = model.lower().replace(" ", "-")

    return (as_maker, as_model) if as_maker else None


def scrape_autoscout24(maker: str, model: str, year: int) -> list[dict]:
    """
    AutoScout24（独）から年式±2の中古車リストを取得。
    __NEXT_DATA__ JSON から価格・年式・走行距離を抽出。
    """
    codes = _as24_maker_model(maker, model)
    if not codes:
        return []
    as_maker, as_model = codes

    eur_to_jpy = get_rate_to_jpy("EUR")
    year_from = max(year - 2, year - 2)
    year_to = year + 1

    url = (
        f"https://www.autoscout24.com/lst/{as_maker}/{as_model}"
        f"?atype=C&cy=D,A,CH,NL,B,I,E,F&damaged=false"
        f"&fregfrom={year_from}&fregto={year_to}&sort=age&desc=0"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }

    try:
        time.sleep(random.uniform(0.5, 1.2))
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.warning(f"autoscout24 {r.status_code}: {url}")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            return []
        data = json.loads(script.text)
    except Exception as e:
        logger.warning(f"autoscout24 fetch error: {e}")
        return []

    # listings配列を探索
    listings_raw = _find_list_with_key(data, "vehicleDetails")
    if not listings_raw:
        return []

    listings = []
    for item in listings_raw:
        try:
            price_eur = _parse_as24_price(item.get("price", {}))
            yr, km = _parse_as24_vehicle(item.get("vehicleDetails", []))
            if not (price_eur and yr):
                continue
            price_jpy = round(price_eur * eur_to_jpy / 10000, 1)
            if not (10 <= price_jpy <= 10000):
                continue
            listings.append({
                "price": price_jpy,
                "year": yr,
                "age": max(0, CURRENT_YEAR - yr),
                "mileage": km or max(0, CURRENT_YEAR - yr) * 12000,
                "source": "autoscout24",
                "currency": "EUR",
                "original_price": price_eur,
            })
        except Exception:
            continue

    logger.info(f"autoscout24: {len(listings)} listings for {maker} {model} {year}")
    return listings


def _find_list_with_key(obj, key, depth=0):
    if depth > 10:
        return None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and key in obj[0]:
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            result = _find_list_with_key(v, key, depth + 1)
            if result:
                return result
    return None


def _parse_as24_price(price_obj: dict) -> float | None:
    fmt = price_obj.get("priceFormatted", "")
    m = re.search(r"[\d.,]+", fmt.replace(",", "").replace(".", "").replace("\xa0", ""))
    if not m:
        m = re.search(r"(\d+)", fmt.replace(",", ""))
    if m:
        val = float(re.sub(r"[^\d]", "", m.group()))
        if 1000 <= val <= 500000:
            return val
    return None


def _parse_as24_vehicle(details: list) -> tuple[int | None, int | None]:
    """vehicleDetails配列から year, mileage を返す"""
    year = None
    mileage = None
    for d in details:
        label = d.get("ariaLabel", "").lower()
        data_str = d.get("data", "")
        if "registration" in label or "year" in label:
            m = re.search(r"(20\d{2}|19\d{2})", data_str)
            if m:
                year = int(m.group(1))
        elif "mileage" in label or "km" in label:
            km_str = data_str.replace(",", "").replace(".", "")
            m = re.search(r"(\d+)", km_str)
            if m:
                mileage = int(m.group(1))
    return year, mileage


def calc_overseas_buyback_floor(
    maker: str, model: str, year: int, mileage: int
) -> float | None:
    """
    海外市場価格から日本の買取価格下限を算出。
    海外売値 × 輸出コスト係数 = 日本での買取フロア価格（万円）
    """
    listings = scrape_autoscout24(maker, model, year)
    if not listings:
        return None

    # 年式 ±1 でフィルタ
    yr_ok = [l for l in listings if abs(l["year"] - year) <= 1]
    if not yr_ok:
        yr_ok = listings if listings else []

    if not yr_ok:
        return None

    # 走行距離フィルタ（±50%）
    if mileage > 0:
        km_ok = [l for l in yr_ok if abs(l["mileage"] - mileage) <= max(mileage * 0.5, 20000)]
        if len(km_ok) >= 1:
            yr_ok = km_ok

    import numpy as np
    prices = sorted(l["price"] for l in yr_ok)
    # 上位外れ値除去（高グレード除外）
    if len(prices) >= 4:
        q3 = prices[3 * len(prices) // 4]
        prices = [p for p in prices if p <= q3 * 1.5]
    if not prices:
        return None

    # 25パーセンタイルを使用: 高グレード混入を避けベースグレード相当の価格を参照
    p25 = float(np.percentile(prices, 25))
    buyback_floor = round(p25 * _EXPORT_COST_RATIO, 1)
    logger.info(
        f"overseas buyback floor: p25={p25:.1f}万円 × {_EXPORT_COST_RATIO} = {buyback_floor:.1f}万円 ({len(prices)} listings)"
    )
    return buyback_floor

CURRENT_YEAR = 2026

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


def _get(url: str, params: dict = None, timeout: int = 12) -> requests.Response | None:
    try:
        time.sleep(random.uniform(0.8, 2.0))
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None


def _parse_price_usd(text: str) -> float | None:
    """USD価格をドルで抽出"""
    text = text.replace(",", "")
    m = re.search(r"\$\s*(\d+)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{4,6})", text)
    if m:
        val = int(m.group(1))
        if 1000 <= val <= 200000:
            return float(val)
    return None


def _parse_price_gbp(text: str) -> float | None:
    """GBP価格をポンドで抽出"""
    text = text.replace(",", "")
    m = re.search(r"£\s*(\d+)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{4,6})", text)
    if m:
        val = int(m.group(1))
        if 500 <= val <= 200000:
            return float(val)
    return None


def _parse_year(text: str) -> int | None:
    m = re.search(r"(20\d{2}|19\d{2})", text)
    return int(m.group(1)) if m else None


def _parse_mileage(text: str) -> int | None:
    text = text.replace(",", "")
    m = re.search(r"(\d+)\s*(?:miles?|mi)", text, re.IGNORECASE)
    if m:
        return int(int(m.group(1)) * 1.609)  # miles to km
    m = re.search(r"(\d+)\s*(?:km|kilometers?)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def scrape_kbb(maker: str, model: str) -> list[dict]:
    """
    Kelley Blue Book: 米国中古車価格データを取得
    メーカー名を英語化して検索
    """
    maker_map = {
        "トヨタ": "toyota", "ホンダ": "honda", "日産": "nissan",
        "マツダ": "mazda", "スバル": "subaru", "三菱": "mitsubishi",
        "ダイハツ": "daihatsu", "スズキ": "suzuki", "レクサス": "lexus",
        "メルセデス": "mercedes-benz", "BMW": "bmw", "アウディ": "audi",
        "フォルクスワーゲン": "volkswagen", "ボルボ": "volvo",
    }
    model_map = {
        "プリウス": "prius", "アクア": "yaris", "カローラ": "corolla",
        "ランドクルーザー": "land-cruiser", "ハイエース": "hiace",
        "フィット": "fit", "シビック": "civic", "アコード": "accord",
        "ノート": "note", "リーフ": "leaf", "セレナ": "serena",
        "cx-5": "cx-5", "cx5": "cx-5", "マツダ3": "mazda3",
        "フォレスター": "forester", "アウトバック": "outback",
        "ジムニー": "jimny",
    }

    en_maker = maker_map.get(maker, maker.lower())
    en_model = model_map.get(model, model.lower().replace(" ", "-"))

    url = f"https://www.kbb.com/{en_maker}/{en_model}/"
    resp = _get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    # KBBのリスティングカード
    cards = (
        soup.select("[data-test='srpCard']")
        or soup.select(".vehicle-card")
        or soup.select("[class*='ListingCard']")
        or soup.select(".listing-card")
    )

    for card in cards[:40]:
        text = card.get_text()
        price_val = None
        for price_elem in card.select("[class*='price'], [class*='Price'], .price"):
            price_val = _parse_price_usd(price_elem.get_text())
            if price_val:
                break

        if not price_val:
            price_val = _parse_price_usd(text)

        year_val = _parse_year(text)
        mileage_val = _parse_mileage(text)

        # USD → 万円に換算 (1USD ≈ 150円)
        if price_val and year_val:
            price_jpy = round(price_val * 150 / 10000, 1)
            listings.append({
                "price": price_jpy,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 15000),
                "source": "kbb_us",
                "currency": "USD",
                "original_price": price_val,
            })

    logger.info(f"kbb: {len(listings)} listings for '{maker} {model}'")
    return listings


def scrape_autotrader_uk(maker: str, model: str) -> list[dict]:
    """AutoTrader UK: 欧州中古車価格データを取得"""
    maker_map = {
        "トヨタ": "toyota", "ホンダ": "honda", "日産": "nissan",
        "マツダ": "mazda", "スバル": "subaru", "三菱": "mitsubishi",
        "レクサス": "lexus", "メルセデス": "mercedes-benz", "BMW": "bmw",
        "アウディ": "audi", "フォルクスワーゲン": "volkswagen",
        "ボルボ": "volvo",
    }
    model_map = {
        "プリウス": "prius", "カローラ": "corolla", "フィット": "jazz",
        "シビック": "civic", "リーフ": "leaf", "cx-5": "cx-5",
        "フォレスター": "forester", "アウトバック": "outback",
        "ジムニー": "jimny",
    }

    en_maker = maker_map.get(maker, maker.lower())
    en_model = model_map.get(model, model.lower().replace(" ", "-"))

    url = "https://www.autotrader.co.uk/car-search"
    params = {
        "sort": "relevance",
        "postcode": "SW1A1AA",
        "make": en_maker.upper(),
        "model": en_model.upper(),
        "radius": "1500",
    }

    resp = _get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    listings = []

    cards = (
        soup.select("[data-testid='trader-seller-listing']")
        or soup.select(".search-listing-card")
        or soup.select("[class*='listing']")
        or soup.select("article")
    )

    for card in cards[:40]:
        text = card.get_text()
        price_val = None
        for price_elem in card.select("[class*='price'], [class*='Price'], .price"):
            price_val = _parse_price_gbp(price_elem.get_text())
            if price_val:
                break
        if not price_val:
            price_val = _parse_price_gbp(text)

        year_val = _parse_year(text)
        mileage_val = _parse_mileage(text)

        # GBP → 万円 (1GBP ≈ 190円)
        if price_val and year_val:
            price_jpy = round(price_val * 190 / 10000, 1)
            listings.append({
                "price": price_jpy,
                "year": year_val,
                "age": max(0, CURRENT_YEAR - year_val),
                "mileage": mileage_val or (max(0, CURRENT_YEAR - year_val) * 15000),
                "source": "autotrader_uk",
                "currency": "GBP",
                "original_price": price_val,
            })

    logger.info(f"autotrader_uk: {len(listings)} listings for '{maker} {model}'")
    return listings


def scrape_all_overseas(maker: str, model: str) -> list[dict]:
    """海外サイトを並列スクレイピング"""
    all_listings = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(scrape_kbb, maker, model): "kbb",
            executor.submit(scrape_autotrader_uk, maker, model): "autotrader_uk",
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result(timeout=25)
                all_listings.extend(results)
            except Exception as e:
                logger.warning(f"{name} scraper error: {e}")

    return [l for l in all_listings if 5 <= l["price"] <= 5000 and l["age"] >= 0]
