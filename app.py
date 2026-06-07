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


def _calc_median_price(listings: list[dict], year: int, mileage_km: float) -> tuple:
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
    price_min = float(min(prices))
    price_max = float(max(prices))
    return median, p25, len(km_filtered), price_min, price_max


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


@app.route("/api/ping")
def ping():
    """サーバー死活確認用エンドポイント（Renderのスリープ解除に使用）"""
    return jsonify({"status": "ok"})


@app.route("/api/resale-score")
def resale_score():
    """
    クエリパラメータ:
      maker     - メーカー名（日本語）
      model     - 車種名（日本語 or NHTSA英語名）
      new_price - グレードの新車価格（万円）
      grade     - グレード名（表示用）
    """
    maker = request.args.get("maker", "").strip()
    model = request.args.get("model", "").strip()
    grade = request.args.get("grade", "").strip()
    try:
        new_price = float(request.args.get("new_price", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "パラメータ不正"}), 400

    if not maker or not model:
        return jsonify({"error": "maker・modelは必須"}), 400
    if new_price <= 0:
        return jsonify({"error": "new_priceは必須（グレードを選択してください）"}), 400

    # キャッシュ確認（グーネット全件を6時間キャッシュ）
    cache_key = f"resale_all_{maker}_{model}"
    listings = cache.get(cache_key)
    if listings is None:
        listings = scrape_goonet(maker, model, CURRENT_YEAR)
        if listings:
            cache.set(cache_key, listings)

    listings = listings or []

    # 車齢1〜7年ごとに中央値・残価率を集計
    residuals = []
    for age in range(1, 8):
        target_year = CURRENT_YEAR - age
        age_listings = [l for l in listings if l.get("year") == target_year]
        if not age_listings:
            # ±1年まで許容
            age_listings = [l for l in listings if l.get("year") and abs(l["year"] - target_year) <= 1]
        if len(age_listings) < 2:
            residuals.append({"age": age, "year": target_year, "median": None,
                               "rate": None, "count": len(age_listings)})
            continue

        prices = sorted(l["price"] for l in age_listings if l.get("price"))
        # IQR外れ値除去
        if len(prices) >= 4:
            q1, q3 = prices[len(prices)//4], prices[3*len(prices)//4]
            iqr = q3 - q1
            prices = [p for p in prices if q1 - 1.5*iqr <= p <= q3 + 1.5*iqr]
        if not prices:
            continue

        median = round(float(np.median(prices)), 1)
        rate   = round(median / new_price * 100, 1)
        residuals.append({
            "age": age, "year": target_year,
            "median": median, "rate": rate,
            "count": len(age_listings),
            "price_min": round(min(prices), 1),
            "price_max": round(max(prices), 1),
            "baseline": _BASELINE_RESALE.get(age),
        })

    # ランク判定: 3年データ優先、なければ最も近い年のデータを使用
    valid = [r for r in residuals if r.get("rate") is not None]
    if not valid:
        return jsonify({
            "residuals": residuals,
            "rank": None, "score": None, "reason": None,
            "sample_count": len(listings),
            "new_price_used": new_price,
            "grade": grade,
            "source": "グーネット（goo-net.com）",
            "error": "市場データが不足しています（掲載件数が少ない車種の可能性があります）",
        })

    primary = next((r for r in valid if r["age"] == 3), None) or \
              min(valid, key=lambda r: abs(r["age"] - 3))

    rank, score, reason = _score_resale(primary["rate"], primary["age"])

    return jsonify({
        "residuals": residuals,
        "rank": rank,
        "score": score,
        "reason": reason,
        "primary_age": primary["age"],
        "primary_rate": primary["rate"],
        "sample_count": len(listings),
        "new_price_used": new_price,
        "grade": grade,
        "source": "グーネット（goo-net.com）",
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
