"""為替レート取得（open.er-api.com 無料API、1時間キャッシュ）"""
import time
import logging

import requests

logger = logging.getLogger(__name__)

_cache: dict = {}
_CACHE_TTL = 3600  # 1時間

_FALLBACK = {"USD": 150.0, "EUR": 160.0, "GBP": 190.0}


def get_rate_to_jpy(currency: str) -> float:
    """1 {currency} = ? JPY"""
    now = time.time()
    if currency in _cache and now - _cache[currency]["ts"] < _CACHE_TTL:
        return _cache[currency]["rate"]

    try:
        r = requests.get(
            f"https://open.er-api.com/v6/latest/{currency}",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            rate = data["rates"].get("JPY")
            if rate:
                _cache[currency] = {"rate": float(rate), "ts": now}
                logger.info(f"FX: 1 {currency} = {rate:.2f} JPY")
                return float(rate)
    except Exception as e:
        logger.warning(f"FX fetch failed ({currency}): {e}")

    fallback = _FALLBACK.get(currency, 150.0)
    logger.info(f"FX: using fallback 1 {currency} = {fallback} JPY")
    return fallback
