import logging
import numpy as np
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

from scrapers.goonet_scraper import scrape_goonet
from utils.cache import FileCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": [
    "https://kurumado.jp",
    "https://www.kurumado.jp",
    "http://localhost:*",
    "http://127.0.0.1:*",
]}})

cache = FileCache()

CURRENT_YEAR = 2026

# 車齢別ブランド平均残価率（%）— 業界平均ベース
_BASELINE_RESALE = {
    1: 87, 2: 77, 3: 70, 4: 63, 5: 57,
    6: 52, 7: 47, 8: 43, 9: 40, 10: 37,
}


def _calc_median_price(listings: list[dict], year: int, mileage_km: float) -> tuple[float | None, float | None, int]:
    """同年式±1・走行距離±50%でフィルタして中央値とP25を返す"""
    mileage = int(mileage_km * 10000)
    year_ok = [l for l in listings if l.get("year") and abs(l["year"] - year) <= 1]
    if not year_ok:
        return None, None, 0

    km_filtered = year_ok
    if mileage > 0:
        f = [l for l in year_ok if l.get("mileage") and
             abs(l["mileage"] - mileage) <= max(mileage * 0.6, 20000)]
        if f:
            km_filtered = f

    prices = sorted(l["price"] for l in km_filtered)
    if not prices:
        return None, None, 0

    # IQR外れ値除去
    if len(prices) >= 4:
        q1 = prices[len(prices) // 4]
        q3 = prices[3 * len(prices) // 4]
        iqr = q3 - q1
        prices = [p for p in prices if q1 - 1.5 * iqr <= p <= q3 + 1.5 * iqr]

    if not prices:
        return None, None, 0

    median = float(np.median(prices))
    p25 = float(np.percentile(prices, 25)) if len(prices) >= 3 else None
    return median, p25, len(km_filtered)


def _score_resale(resale_rate: float, age: int) -> tuple[str, int]:
    """残価率と車齢からS/A/B/Cランクとスコア(0-100)を算出"""
    age_c = max(1, min(age, 10))
    baseline = _BASELINE_RESALE[age_c]
    diff = resale_rate - baseline

    score = max(0, min(100, 50 + int(diff * 2.5)))

    if diff >= 12:
        rank, reason = "S", f"平均より{diff:.1f}pt高い超高残価"
    elif diff >= 4:
        rank, reason = "A", f"平均より{diff:.1f}pt高い高残価"
    elif diff >= -4:
        rank, reason = "B", f"平均的な残価（±{abs(diff):.1f}pt）"
    else:
        rank, reason = "C", f"平均より{abs(diff):.1f}pt低い低残価"

    return rank, score, reason


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/resale-score")
def resale_score():
    """
    クエリパラメータ:
      maker        - メーカー名（日本語）
      model        - 車種名（日本語 or NHTSA英語名）
      year         - 年式（例: 2022）
      mileage      - 現在の走行距離（万km、例: 3.5）
      new_price    - グレードDBからの新車価格（万円）
      market_price - ユーザー入力の実勢価格（万円、省略可）
    """
    maker = request.args.get("maker", "").strip()
    model = request.args.get("model", "").strip()
    try:
        year = int(request.args.get("year", 2020))
        mileage_km = float(request.args.get("mileage", 3.0))
        new_price = float(request.args.get("new_price", 0))
        market_price_input = float(request.args.get("market_price", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "パラメータ不正"}), 400

    if not maker or not model:
        return jsonify({"error": "maker・modelは必須"}), 400

    if not (1960 <= year <= CURRENT_YEAR):
        return jsonify({"error": "年式が範囲外"}), 400

    # キャッシュ確認（グーネット結果を5時間キャッシュ）
    cache_key = f"resale_{maker}_{model}_{year}"
    listings = cache.get(cache_key)
    if listings is None:
        listings = scrape_goonet(maker, model, year)
        if listings:
            cache.set(cache_key, listings)

    # 市場価格の計算（グーネット中央値）
    median_price, p25_price, sample_count = _calc_median_price(listings or [], year, mileage_km)

    # 有効価格: ユーザー入力 > グーネット中央値
    effective_price = market_price_input if market_price_input > 0 else median_price

    current_age = CURRENT_YEAR - year

    if not effective_price or not new_price:
        return jsonify({
            "market_price_median": median_price,
            "market_price_p25": p25_price,
            "sample_count": sample_count,
            "resale_rate": None,
            "rank": None,
            "score": None,
            "reason": None,
            "error": "価格データ不足（新車価格またはグーネット価格が取得できませんでした）",
        })

    resale_rate = round(effective_price / new_price * 100, 1)
    rank, score, reason = _score_resale(resale_rate, current_age)

    return jsonify({
        "market_price_median": median_price,
        "market_price_p25": p25_price,
        "sample_count": sample_count,
        "effective_price": effective_price,
        "new_price_used": new_price,
        "resale_rate": resale_rate,
        "rank": rank,
        "score": score,
        "reason": reason,
        "car_age": current_age,
        "baseline_resale": _BASELINE_RESALE.get(min(current_age, 10)),
    })


@app.route("/api/predict", methods=["POST"])
def predict():
    body = request.get_json(force=True)
    maker = (body.get("maker") or "").strip()
    model_name = (body.get("model") or "").strip()

    if not maker or not model_name:
        return jsonify({"error": "メーカーと車種名を入力してください"}), 400

    try:
        year = int(body.get("year") or 2020)
        mileage = int(body.get("mileage") or 50000)
        annual_mileage = int(body.get("annual_mileage") or 10000)
    except (ValueError, TypeError):
        return jsonify({"error": "年式・走行距離は数値で入力してください"}), 400

    grade = body.get("grade", "base")
    repair_history = body.get("repair_history", "none")
    condition = body.get("condition", "good")

    if not (1990 <= year <= CURRENT_YEAR):
        return jsonify({"error": f"年式は1990〜{CURRENT_YEAR}の範囲で入力してください"}), 400

    cache_key = f"{maker}_{model_name}_{year}_{mileage // 10000}"
    use_cache = body.get("use_cache", True)

    scraped_data = None
    from_cache = False

    if use_cache:
        scraped_data = cache.get(cache_key)
        if scraped_data:
            from_cache = True

    if scraped_data is None:
        try:
            from scrapers.aggregator import DataAggregator
            aggregator = DataAggregator()
            scraped_data = aggregator.search(maker, model_name, year=year, mileage=mileage)
            if scraped_data.get("combined") or scraped_data.get("scraped_current_price"):
                cache.set(cache_key, scraped_data)
        except Exception as e:
            logger.error(f"aggregator error: {e}")
            scraped_data = {}

    current_age = CURRENT_YEAR - year
    try:
        from models.depreciation import HybridDepreciationModel
        predictor = HybridDepreciationModel()
        result = predictor.fit_and_predict(
            listings=scraped_data,
            maker=maker,
            model_name=model_name,
            current_age=current_age,
            current_mileage=mileage,
            annual_mileage=annual_mileage,
            prediction_years=10,
            grade=grade,
            repair_history=repair_history,
            condition=condition,
        )
    except Exception as e:
        logger.error(f"prediction error: {e}")
        return jsonify({"error": f"予測エラー: {e}"}), 500

    result["from_cache"] = from_cache
    result["input"] = {
        "maker": maker, "model": model_name, "year": year,
        "mileage": mileage, "annual_mileage": annual_mileage,
        "current_age": current_age, "grade": grade,
        "repair_history": repair_history, "condition": condition,
    }
    return jsonify(result)


@app.route("/api/clear_cache", methods=["POST"])
def clear_cache():
    cache.clear()
    return jsonify({"success": True, "message": "キャッシュをクリアしました"})


if __name__ == "__main__":
    print("=" * 55)
    print("  中古車値下がり予測ツール API")
    print("=" * 55)
    print("  http://localhost:5001")
    print("=" * 55)
    app.run(debug=False, port=5001, host="0.0.0.0")
