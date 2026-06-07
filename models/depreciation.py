"""
ハイブリッド減価モデル（改訂版）

現在価格の優先順位:
  1. Playwright でスクレイピングした同年式・近走行距離の中央値
  2. スクレイピングデータ（全年式）の補間
  3. ブランド新車価格 × 累積減価率
  4. ブランド標準価格（絶対フォールバック）

モデル:
  - 指数減衰回帰 + Random Forest + Gradient Boosting のアンサンブル
  - グレード・修復歴・車両状態の補正係数を適用
"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)

BASELINE_PATH = Path(__file__).parent.parent / "data" / "baseline.json"
CURRENT_YEAR = 2026
MIN_DATA_FOR_ML = 15
MIN_DATA_FOR_STAT = 5


def _load_baseline() -> dict:
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _exp_decay(age, a, b, c):
    return a * np.exp(-b * age) + c


def _get_brand_key(maker: str, baseline: dict) -> str:
    mapping = {
        "トヨタ": "toyota", "toyota": "toyota",
        "ホンダ": "honda", "honda": "honda",
        "日産": "nissan", "nissan": "nissan",
        "マツダ": "mazda", "mazda": "mazda",
        "スバル": "subaru", "subaru": "subaru",
        "三菱": "mitsubishi", "mitsubishi": "mitsubishi",
        "ダイハツ": "daihatsu", "daihatsu": "daihatsu",
        "スズキ": "suzuki", "suzuki": "suzuki",
        "レクサス": "lexus", "lexus": "lexus",
        "メルセデス": "mercedes", "mercedes": "mercedes",
        "メルセデスベンツ": "mercedes",
        "bmw": "bmw", "BMW": "bmw",
        "アウディ": "audi", "audi": "audi",
        "フォルクスワーゲン": "volkswagen", "volkswagen": "volkswagen", "vw": "volkswagen",
        "ボルボ": "volvo", "volvo": "volvo",
        "ポルシェ": "porsche", "porsche": "porsche",
        "ランドローバー": "land_rover", "land_rover": "land_rover", "landrover": "land_rover",
        "ジャガー": "jaguar", "jaguar": "jaguar",
        "フェラーリ": "ferrari", "ferrari": "ferrari",
        "ランボルギーニ": "lamborghini", "lamborghini": "lamborghini",
    }
    result = mapping.get(maker) or mapping.get(maker.lower())
    if result:
        return result
    maker_l = maker.lower()
    for key, val in mapping.items():
        if key in maker_l or maker_l in key:
            return val
    return "default"


def _get_model_adjustment(model_name: str, baseline: dict) -> tuple[float, str, float | None]:
    model_adj = baseline.get("model_adjustments", {})
    model_norm = model_name.lower().replace(" ", "").replace("-", "")
    for key, val in model_adj.items():
        key_norm = key.lower().replace(" ", "").replace("-", "")
        if key_norm in model_norm or model_norm in key_norm:
            return val["adj"], val.get("note", ""), val.get("min_residual")
    return 0.0, "", None


def _estimate_new_car_price(model_name: str, brand_data: dict, baseline: dict) -> float | None:
    """新車価格テーブルから新車価格を推定"""
    price_table = baseline.get("brand_new_prices", {})
    model_norm = model_name.lower().replace(" ", "").replace("-", "")
    for key, price in price_table.items():
        key_norm = key.lower().replace(" ", "").replace("-", "")
        if key_norm in model_norm or model_norm in key_norm:
            return float(price)
    return None


def _mileage_factor(actual_mileage: int, age: int, avg_annual: int = 10000) -> float:
    """
    実走行距離が平均より少ない場合はプレミアム、多い場合はディスカウント。
    平均: 10,000km/年。±30% の範囲で最大 ±15% の補正。
    """
    if age <= 0:
        return 1.0
    expected = avg_annual * age
    if expected <= 0:
        return 1.0
    ratio = actual_mileage / expected  # 1.0 = 平均, <1 = 低走行, >1 = 高走行
    # 走行距離補正係数: ratio=0.3 → +15%, ratio=1.0 → 0%, ratio=2.0 → -12%
    factor = 1.0 + np.clip((1.0 - ratio) * 0.18, -0.15, 0.18)
    logger.debug(f"Mileage factor: actual={actual_mileage}, expected={expected}, ratio={ratio:.2f}, factor={factor:.3f}")
    return factor


def _get_buyback_ratio(maker: str, model_name: str, brand_key: str, baseline: dict) -> float:
    """売値に対する買取価格の比率を返す"""
    overrides = baseline.get("model_buyback_overrides", {})
    model_norm = model_name.lower().replace(" ", "").replace("-", "")
    for key, ratio in overrides.items():
        key_norm = key.lower().replace(" ", "").replace("-", "")
        if key_norm in model_norm or model_norm in key_norm:
            return ratio
    ratios = baseline.get("buyback_ratios", {})
    return ratios.get(brand_key, ratios.get("default", 0.77))


class HybridDepreciationModel:

    def fit_and_predict(
        self,
        listings: dict,
        maker: str,
        model_name: str,
        current_age: int,
        current_mileage: int,
        annual_mileage: int,
        prediction_years: int = 5,
        # 新規パラメータ
        grade: str = "base",
        repair_history: str = "none",
        condition: str = "good",
    ) -> dict:
        baseline = _load_baseline()
        brand_key = _get_brand_key(maker, baseline)
        brand_data = baseline["brands"].get(brand_key, baseline["brands"]["default"])
        model_adj, model_note, min_residual = _get_model_adjustment(model_name, baseline)

        # ── 補正係数の計算 ──────────────────────────────────────────
        grade_mult = baseline.get("grade_multipliers", {}).get(grade, 1.0)
        condition_adj = baseline.get("condition_adjustments", {}).get(condition, 0.0)
        repair_rate_add = baseline.get("repair_history", {}).get(repair_history, 0.0)

        combined = listings.get("combined", [])
        jp_listings = listings.get("japan", [])
        os_listings = listings.get("overseas", [])

        # ── 現在価格の決定（優先順位に従う）───────────────────────
        current_price = self._determine_current_price(
            listings=listings,
            combined=combined,
            current_age=current_age,
            current_mileage=current_mileage,
            grade_mult=grade_mult,
            condition_adj=condition_adj,
            model_adj=model_adj,
            min_residual=min_residual,
            model_name=model_name,
            brand_data=brand_data,
            baseline=baseline,
        )

        # ── モデル学習・予測 ──────────────────────────────────────
        primary_data = jp_listings if len(jp_listings) >= MIN_DATA_FOR_STAT else combined
        method_used = "baseline"
        stat_result = None
        ml_result = None

        if len(primary_data) >= MIN_DATA_FOR_STAT:
            stat_result = self._fit_statistical(
                primary_data, current_age, current_mileage, annual_mileage, prediction_years
            )
            if stat_result:
                method_used = "statistical"

        if len(primary_data) >= MIN_DATA_FOR_ML:
            ml_result = self._fit_ml(
                primary_data, current_age, current_mileage, annual_mileage, prediction_years
            )
            if ml_result:
                method_used = "hybrid" if stat_result else "ml"

        baseline_result = self._baseline_predict(
            annual_rates=brand_data["annual_rates"],
            model_adj=model_adj,
            repair_rate_add=repair_rate_add,
            current_age=current_age,
            current_mileage=current_mileage,
            annual_mileage=annual_mileage,
            prediction_years=prediction_years,
            current_price=current_price,
        )

        # ── アンサンブル ──────────────────────────────────────────
        if stat_result and ml_result:
            raw_preds = self._ensemble(stat_result, ml_result, baseline_result,
                                       w_stat=0.40, w_ml=0.45, w_base=0.15)
        elif stat_result:
            raw_preds = self._ensemble(stat_result, None, baseline_result,
                                       w_stat=0.60, w_ml=0, w_base=0.40)
        else:
            raw_preds = baseline_result["predictions"]
            method_used = "baseline"

        # ── 現在価格にスケーリング + 減価率計算 ──────────────────
        final_predictions = self._scale_and_annotate(raw_preds, current_price)
        buyback_ratio = _get_buyback_ratio(maker, model_name, brand_key, baseline)
        final_predictions = self.annotate_buyback(final_predictions, buyback_ratio)

        market_summary = self._build_market_summary(combined, current_age)

        # ── 買取価格の決定 ─────────────────────────────────────────────
        # 1. 比率ベース（ブランド別）
        domestic_ratio_buyback = float(current_price) * buyback_ratio

        # 2. グーネット売値P25ベース（国内実勢データ）
        # 売値P25 × 0.87 ≈ ディーラー仕入れコスト ≈ 業者買取価格
        # P25 = 売場の安値帯。ここから逆算するのが国内市況に最も近い
        goonet_p25 = listings.get("goonet_sell_p25")
        if goonet_p25 and float(goonet_p25) > 10:
            p25_derived_buyback = float(goonet_p25) * 0.87
            # 二つの推定のうち低い方を採用（過大評価を防ぐ）
            domestic_buyback = min(domestic_ratio_buyback, p25_derived_buyback)
            buyback_source = "domestic_p25"
            logger.info(
                f"Buyback domestic: ratio={domestic_ratio_buyback:.1f}, "
                f"p25_derived={p25_derived_buyback:.1f} → using {domestic_buyback:.1f}"
            )
        else:
            domestic_buyback = domestic_ratio_buyback
            buyback_source = "domestic_ratio"

        # 3. 海外フロア: 国内推定の105%かつ売値×82%を上限とする
        # （欧州高値が国内買取を不当に引き上げるのを防ぐ）
        overseas_floor = listings.get("overseas_buyback_floor")
        max_buyback = min(current_price * 0.82, domestic_buyback * 1.05)
        if overseas_floor:
            overseas_floor = min(float(overseas_floor), max_buyback)
        if overseas_floor and overseas_floor > domestic_buyback:
            buyback_price = round(overseas_floor, 1)
            buyback_source = "overseas"
            logger.info(
                f"Buyback: overseas floor {overseas_floor:.1f} > domestic {domestic_buyback:.1f} → using overseas"
            )
        else:
            buyback_price = round(domestic_buyback, 1)

        return {
            "success": True,
            "current_price": round(current_price, 1),
            "buyback_price": buyback_price,
            "buyback_ratio": round(buyback_ratio, 2),
            "overseas_buyback_floor": round(float(overseas_floor), 1) if overseas_floor else None,
            "buyback_source": buyback_source,
            "predictions": final_predictions,
            "method": method_used,
            "data_points": len(combined),
            "japan_data_points": len(jp_listings),
            "overseas_data_points": len(os_listings),
            "scraped_current_price": listings.get("scraped_current_price"),
            "current_price_matched_count": listings.get("current_price_matched_count", 0),
            "market_summary": market_summary,
            "source_summary": listings.get("source_summary", {}),
            "brand": brand_data.get("label", maker),
            "brand_note": brand_data.get("note", ""),
            "model_note": model_note,
            "grade": grade,
            "grade_mult": grade_mult,
            "repair_history": repair_history,
            "repair_rate_add": repair_rate_add,
            "condition": condition,
            "condition_adj": condition_adj,
        }

    # ── 現在価格の決定 ─────────────────────────────────────────────────
    def _determine_current_price(
        self, listings, combined, current_age, current_mileage,
        grade_mult, condition_adj, model_adj, min_residual, model_name, brand_data, baseline
    ) -> float:

        # 優先1: Playwright スクレイピング取得価格
        scraped = listings.get("scraped_current_price")
        if scraped and scraped > 0:
            # グレード・状態補正を適用
            price = scraped * grade_mult * (1 + condition_adj)
            logger.info(f"Current price from Playwright scraping: {scraped} → {price:.1f} (grade={grade_mult}, cond={condition_adj})")
            return price

        # 優先2: スクレイピングデータから年式補間
        interpolated = self._estimate_current_price(combined, current_age, current_mileage)
        if interpolated:
            price = interpolated * grade_mult * (1 + condition_adj)
            logger.info(f"Current price from interpolation: {interpolated} → {price:.1f}")
            return price

        # 優先3: 新車価格 × 累積減価率（車種別 model_adj を適用）
        new_car_price = _estimate_new_car_price(model_name, brand_data, baseline)
        if new_car_price:
            rates = brand_data["annual_rates"]
            price = new_car_price
            for i in range(min(current_age, len(rates))):
                rate = max(0.01, min(0.45, rates[i] + model_adj))
                price *= (1 - rate)
            extra_rate = max(0.01, min(0.45, rates[-1] + model_adj))
            for i in range(max(0, current_age - len(rates))):
                price *= (1 - extra_rate)
            # 最低残価フロア（供給制約車種: ジムニー・ランドクルーザー等）
            if min_residual is not None:
                floor_price = new_car_price * min_residual
                if price < floor_price:
                    logger.info(f"min_residual floor applied: {price:.1f} → {floor_price:.1f} (ratio={min_residual})")
                    price = floor_price
            price *= grade_mult * (1 + condition_adj)
            # 走行距離補正（実走行距離 vs 平均）
            price *= _mileage_factor(current_mileage, current_age)
            logger.info(f"Current price from new-car table: new={new_car_price} → after {current_age}yr (adj={model_adj:+.2f}) → {price:.1f}")
            return price

        # 優先4: ブランド別デフォルト（絶対フォールバック）
        brand_defaults = {
            "porsche": 500, "bmw": 350, "mercedes": 350,
            "audi": 300, "volkswagen": 200, "volvo": 280,
            "land_rover": 400, "jaguar": 300,
            "ferrari": 2000, "lamborghini": 2500,
            "toyota": 180, "honda": 170, "nissan": 160,
            "mazda": 160, "subaru": 200, "lexus": 280,
            "default": 200,
        }
        brand_key2 = _get_brand_key(brand_data.get("label", ""), baseline)
        fallback = brand_defaults.get(brand_key2, brand_defaults["default"])
        price = fallback * grade_mult * (1 + condition_adj)
        logger.info(f"Current price from brand default: {fallback} → {price:.1f}")
        return price

    # ── 統計モデル ──────────────────────────────────────────────────
    def _fit_statistical(self, data, current_age, current_mileage, annual_mileage, years):
        try:
            df = pd.DataFrame(data)
            df = df[df["age"] > 0].copy()
            if len(df) < MIN_DATA_FOR_STAT:
                return None
            ages = df["age"].values.astype(float)
            prices = df["price"].values.astype(float)
            p0 = [prices.max(), 0.1, prices.min() * 0.5]
            popt, _ = curve_fit(
                _exp_decay, ages, prices, p0=p0,
                bounds=([0, 0.01, 0], [prices.max() * 3, 1.0, prices.max()]),
                maxfev=5000,
            )
            a, b, c = popt
            return [
                {
                    "year_offset": y,
                    "future_age": current_age + y,
                    "predicted_price": max(_exp_decay(current_age + y, a, b, c), 1.0),
                    "future_mileage": current_mileage + annual_mileage * y,
                }
                for y in range(1, years + 1)
            ]
        except Exception as e:
            logger.warning(f"Statistical fit failed: {e}")
            return None

    # ── ML モデル ────────────────────────────────────────────────────
    def _fit_ml(self, data, current_age, current_mileage, annual_mileage, years):
        try:
            df = pd.DataFrame(data)
            df = df[df["age"] > 0].copy()
            df["mileage_per_year"] = df["mileage"] / df["age"].clip(lower=0.5)
            df = df.dropna()
            if len(df) < MIN_DATA_FOR_ML:
                return None
            X = df[["age", "mileage", "mileage_per_year"]].values
            y = df["price"].values
            rf = Pipeline([("sc", StandardScaler()),
                           ("m", RandomForestRegressor(n_estimators=200, max_depth=6,
                                                        min_samples_leaf=2, random_state=42,
                                                        n_jobs=-1))])
            gb = Pipeline([("sc", StandardScaler()),
                           ("m", GradientBoostingRegressor(n_estimators=150, learning_rate=0.05,
                                                            max_depth=4, random_state=42))])
            rf.fit(X, y)
            gb.fit(X, y)
            preds = []
            for yoff in range(1, years + 1):
                fage = current_age + yoff
                fmile = current_mileage + annual_mileage * yoff
                mpy = fmile / max(fage, 0.5)
                Xp = np.array([[fage, fmile, mpy]])
                price = 0.5 * rf.predict(Xp)[0] + 0.5 * gb.predict(Xp)[0]
                preds.append({
                    "year_offset": yoff,
                    "future_age": fage,
                    "predicted_price": max(price, 1.0),
                    "future_mileage": fmile,
                })
            return preds
        except Exception as e:
            logger.warning(f"ML fit failed: {e}")
            return None

    # ── ベースライン ─────────────────────────────────────────────────
    def _baseline_predict(self, annual_rates, model_adj, repair_rate_add,
                           current_age, current_mileage, annual_mileage,
                           prediction_years, current_price):
        """現在価格から各年の価格を順次計算する"""
        preds = []
        price = current_price
        for y in range(1, prediction_years + 1):
            idx = min(y - 1, len(annual_rates) - 1)
            rate = annual_rates[idx] + model_adj + repair_rate_add
            rate = max(0.02, min(0.45, rate))
            price *= (1 - rate)
            preds.append({
                "year_offset": y,
                "future_age": current_age + y,
                "future_mileage": current_mileage + annual_mileage * y,
                "annual_rate": rate,
                "predicted_price": max(price, 1.0),
            })
        return {"predictions": preds}

    # ── アンサンブル ─────────────────────────────────────────────────
    def _ensemble(self, stat, ml, baseline, w_stat, w_ml, w_base):
        base_preds = baseline["predictions"]
        result = []
        for i, bp in enumerate(base_preds):
            price = 0.0
            total = 0.0
            if stat and i < len(stat):
                price += w_stat * stat[i]["predicted_price"]; total += w_stat
            if ml and i < len(ml):
                price += w_ml * ml[i]["predicted_price"]; total += w_ml
            price += w_base * bp["predicted_price"]; total += w_base
            entry = dict(bp)
            entry["predicted_price"] = max(price / total, 1.0)
            result.append(entry)
        return result

    # ── スケーリング + 減価率計算 ─────────────────────────────────
    def _scale_and_annotate(self, predictions: list[dict], current_price: float) -> list[dict]:
        """
        全予測を current_price をベースにスケーリングし、
        年次・累計の減価率を計算して付与する
        """
        if not predictions:
            return predictions

        first = predictions[0]
        annual_rate = first.get("annual_rate")

        if annual_rate is not None:
            # ベースライン/アンサンブルケース: 1年分逆算でスケールを揃える
            model_implied_now = first["predicted_price"] / max(0.01, 1.0 - annual_rate)
        else:
            # 統計/ML のみケース: 現在価格との比で軽微補正
            model_implied_now = current_price

        scale = np.clip(current_price / max(model_implied_now, 1.0), 0.3, 3.0)

        result = []
        prev = current_price
        for pred in predictions:
            p = max(pred["predicted_price"] * scale, 1.0)
            dep_rate = (prev - p) / prev * 100 if prev > 0 else 0
            cum_rate = (current_price - p) / current_price * 100 if current_price > 0 else 0
            e = dict(pred)
            e["predicted_price"] = round(p, 1)
            e["depreciation_rate"] = round(max(0, dep_rate), 1)
            e["cumulative_depreciation"] = round(max(0, cum_rate), 1)
            e["calendar_year"] = CURRENT_YEAR + pred["year_offset"]
            result.append(e)
            prev = p
        return result

    def annotate_buyback(self, predictions: list[dict], buyback_ratio: float) -> list[dict]:
        """各年の予測に買取価格を付与する"""
        for p in predictions:
            p["buyback_price"] = round(float(p["predicted_price"]) * buyback_ratio, 1)
        return predictions

    # ── 現在価格補間（スクレイピングデータから）─────────────────────
    def _estimate_current_price(self, combined, current_age, current_mileage) -> float | None:
        if not combined:
            return None
        same = [l for l in combined if abs(l["age"] - current_age) <= 1]
        if len(same) >= 2:
            return float(np.median([l["price"] for l in same]))
        return None

    # ── 市場サマリー ─────────────────────────────────────────────────
    def _build_market_summary(self, combined, current_age) -> dict:
        if not combined:
            return {"average_price": None, "median_price": None,
                    "min_price": None, "max_price": None,
                    "total_listings": 0, "age_distribution": {}}
        prices = [l["price"] for l in combined]
        age_dist = {}
        for l in combined:
            k = str(l["age"])
            age_dist[k] = age_dist.get(k, 0) + 1
        return {
            "average_price": round(float(np.mean(prices)), 1),
            "median_price": round(float(np.median(prices)), 1),
            "min_price": round(float(np.min(prices)), 1),
            "max_price": round(float(np.max(prices)), 1),
            "total_listings": len(combined),
            "age_distribution": age_dist,
        }
