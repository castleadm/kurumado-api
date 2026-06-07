"""
全スクレイパーを統合
1. Playwright で指定年式・走行距離の現在価格を取得（高精度）
2. requests+BS4 で広範囲の年式データを並列取得（減価曲線用）
"""
import logging
import numpy as np
from .goonet_scraper import scrape_goonet
from .playwright_scraper import scrape_current_price
from .japanese import scrape_all_japanese
from .overseas import scrape_all_overseas, calc_overseas_buyback_floor

logger = logging.getLogger(__name__)


class DataAggregator:
    def search(self, maker: str, model: str, year: int = 2020,
               mileage: int = 50000) -> dict:
        """
        Returns:
            {
              "japan": [...],
              "overseas": [...],
              "combined": [...],
              "source_summary": {...},
              "scraped_current_price": float | None,   ← ★ Playwright 取得価格
              "current_price_matched_count": int,
            }
        """
        logger.info(f"=== Scraping START: {maker} {model} {year}年式 {mileage}km ===")

        # ── 1. グーネット requests スクレイプ（高速・確実）────────────
        goonet_listings = scrape_goonet(maker, model, year)
        scraped_current_price = None
        matched_count = 0

        goonet_sell_p25 = None
        if goonet_listings:
            scraped_current_price, goonet_sell_p25, matched_count = _calc_current_price(
                goonet_listings, year, mileage
            )
            logger.info(
                f"Goonet: {len(goonet_listings)} listings, "
                f"matched={matched_count}, current_price={scraped_current_price}, p25={goonet_sell_p25}"
            )

        # ── 2. Playwright フォールバック（グーネット取得できなかった場合）─
        pw_listings = []
        if scraped_current_price is None:
            logger.info("Goonet failed, trying Playwright...")
            pw_result = scrape_current_price(maker, model, year, mileage)
            pw_listings = pw_result["listings"]
            scraped_current_price = pw_result["current_price"]
            matched_count = pw_result["matched_count"]
            logger.info(
                f"Playwright: {len(pw_listings)} total, matched={matched_count}, "
                f"current_price={scraped_current_price}"
            )

        # ── 3. 広範囲の年式データ（減価曲線フィッティング用）─────────
        jp_broad = scrape_all_japanese(maker, model)
        os_listings = scrape_all_overseas(maker, model)

        # ── 4. 海外買取フロア（AutoScout24 から並列取得）────────────
        overseas_buyback_floor = calc_overseas_buyback_floor(maker, model, year, mileage)

        # ── 5. 統合・重複除去・外れ値除去 ────────────────────────────
        all_jp = _merge(goonet_listings, _merge(pw_listings, jp_broad))
        combined = _merge(all_jp, os_listings)
        combined = _deduplicate(combined)
        combined = _remove_outliers(combined)

        source_summary = {}
        for item in combined:
            src = item["source"]
            source_summary[src] = source_summary.get(src, 0) + 1

        logger.info(
            f"=== Scraping DONE: combined={len(combined)} listings | {source_summary} | "
            f"overseas_buyback_floor={overseas_buyback_floor} ==="
        )

        return {
            "japan": all_jp,
            "overseas": os_listings,
            "combined": combined,
            "source_summary": source_summary,
            "scraped_current_price": scraped_current_price,
            "current_price_matched_count": matched_count,
            "overseas_buyback_floor": overseas_buyback_floor,
            "goonet_sell_p25": goonet_sell_p25,
        }


def _merge(a: list[dict], b: list[dict]) -> list[dict]:
    return list(a) + list(b)


def _deduplicate(listings: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in listings:
        key = (item["year"], round(item["price"] / 5) * 5)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _calc_current_price(listings: list[dict], year: int, mileage: int) -> tuple[float | None, float | None, int]:
    """同年式±1・走行距離±50% でフィルタして中央値とP25を返す"""
    year_ok = [l for l in listings if l.get("year") and abs(l["year"] - year) <= 1]
    if not year_ok:
        return None, None, 0

    mileage_ok = year_ok
    if mileage > 0 and year_ok:
        km_filtered = [
            l for l in year_ok
            if l.get("mileage") and abs(l["mileage"] - mileage) <= max(mileage * 0.5, 20000)
        ]
        if len(km_filtered) >= 1:
            mileage_ok = km_filtered

    if not mileage_ok:
        return None, None, 0

    prices = sorted(l["price"] for l in mileage_ok)
    if len(prices) >= 4:
        q1 = prices[len(prices) // 4]
        q3 = prices[3 * len(prices) // 4]
        iqr = q3 - q1
        prices = [p for p in prices if q1 - 1.5 * iqr <= p <= q3 + 1.5 * iqr]
    if not prices:
        return None, None, 0
    p25 = float(np.percentile(prices, 25)) if len(prices) >= 3 else None
    return float(np.median(prices)), p25, len(mileage_ok)


def _remove_outliers(listings: list[dict]) -> list[dict]:
    if len(listings) < 6:
        return listings
    prices = sorted(l["price"] for l in listings)
    n = len(prices)
    q1, q3 = prices[n // 4], prices[3 * n // 4]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    filtered = [l for l in listings if lo <= l["price"] <= hi]
    removed = len(listings) - len(filtered)
    if removed:
        logger.info(f"Removed {removed} outliers")
    return filtered
