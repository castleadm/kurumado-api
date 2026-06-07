"""
グーネット requests+BS4 スクレイパー
JavaScript 不要 — 正しい URL パターンで直接 HTML を取得してリスト抽出
"""
import re
import time
import logging
import random
from urllib.parse import quote
from bs4 import BeautifulSoup

try:
    import requests
    _requests_ok = True
except ImportError:
    _requests_ok = False

logger = logging.getLogger(__name__)
CURRENT_YEAR = 2026

# ──────────────────── goonet コード マッピング ────────────────────────
# (日本語名 or 英語名) → (goonet_maker_code, goonet_model_code)
_GOONET_MAP = {
    # ── ポルシェ ──
    "マカン":         ("PORSCHE", "MACAN"),
    "macan":          ("PORSCHE", "MACAN"),
    "カイエン":       ("PORSCHE", "CAYENNE"),
    "cayenne":        ("PORSCHE", "CAYENNE"),
    "パナメーラ":     ("PORSCHE", "PANAMERA"),
    "panamera":       ("PORSCHE", "PANAMERA"),
    "911":            ("PORSCHE", "911"),
    "ボクスター":     ("PORSCHE", "BOXSTER"),
    "boxster":        ("PORSCHE", "BOXSTER"),
    "ケイマン":       ("PORSCHE", "CAYMAN"),
    "cayman":         ("PORSCHE", "CAYMAN"),
    "タイカン":       ("PORSCHE", "TAYCAN"),
    "taycan":         ("PORSCHE", "TAYCAN"),
    # ── メルセデス ──
    "cクラス":        ("MERCEDES_BENZ", "C-CLASS"),
    "c-class":        ("MERCEDES_BENZ", "C-CLASS"),
    "cclass":         ("MERCEDES_BENZ", "C-CLASS"),
    "eクラス":        ("MERCEDES_BENZ", "E-CLASS"),
    "e-class":        ("MERCEDES_BENZ", "E-CLASS"),
    "eclass":         ("MERCEDES_BENZ", "E-CLASS"),
    "sクラス":        ("MERCEDES_BENZ", "S-CLASS"),
    "s-class":        ("MERCEDES_BENZ", "S-CLASS"),
    "gクラス":        ("MERCEDES_BENZ", "G-CLASS"),
    "g-class":        ("MERCEDES_BENZ", "G-CLASS"),
    "gclass":         ("MERCEDES_BENZ", "G-CLASS"),
    "g wagon":        ("MERCEDES_BENZ", "G-CLASS"),
    "gwagon":         ("MERCEDES_BENZ", "G-CLASS"),
    "aクラス":        ("MERCEDES_BENZ", "A-CLASS"),
    "a-class":        ("MERCEDES_BENZ", "A-CLASS"),
    "bクラス":        ("MERCEDES_BENZ", "B-CLASS"),
    "glc":            ("MERCEDES_BENZ", "GLC-CLASS"),
    "gle":            ("MERCEDES_BENZ", "GLE"),
    "gla":            ("MERCEDES_BENZ", "GLA-CLASS"),
    "glb":            ("MERCEDES_BENZ", "GLB"),
    # ── BMW ──
    "3シリーズ":      ("BMW", "3_SERIES"),
    "3series":        ("BMW", "3_SERIES"),
    "3 series":       ("BMW", "3_SERIES"),
    "5シリーズ":      ("BMW", "5_SERIES"),
    "5series":        ("BMW", "5_SERIES"),
    "5 series":       ("BMW", "5_SERIES"),
    "7シリーズ":      ("BMW", "7_SERIES"),
    "1シリーズ":      ("BMW", "1_SERIES"),
    "2シリーズ":      ("BMW", "2_SERIES"),
    "4シリーズ":      ("BMW", "4_SERIES"),
    "8シリーズ":      ("BMW", "8_SERIES"),
    "x1":             ("BMW", "X1"),
    "x2":             ("BMW", "X2"),
    "x3":             ("BMW", "X3"),
    "x5":             ("BMW", "X5"),
    "x6":             ("BMW", "X6"),
    "x7":             ("BMW", "X7"),
    "m3":             ("BMW", "M3"),
    "m5":             ("BMW", "M5"),
    # ── トヨタ ──
    "プリウス":        ("TOYOTA", "PRIUS"),
    "prius":           ("TOYOTA", "PRIUS"),
    "ランドクルーザー": ("TOYOTA", "LAND_CRUISER"),
    "ランクル":        ("TOYOTA", "LAND_CRUISER"),
    "landcruiser":     ("TOYOTA", "LAND_CRUISER"),
    "land cruiser":    ("TOYOTA", "LAND_CRUISER"),
    "アルファード":    ("TOYOTA", "ALPHARD"),
    "alphard":         ("TOYOTA", "ALPHARD"),
    "ヴェルファイア":  ("TOYOTA", "VELLFIRE"),
    "vellfire":        ("TOYOTA", "VELLFIRE"),
    "ハイエース":      ("TOYOTA", "HIACE_VAN"),
    "hiace":           ("TOYOTA", "HIACE_VAN"),
    "ハリアー":        ("TOYOTA", "HARRIER"),
    "harrier":         ("TOYOTA", "HARRIER"),
    "アクア":          ("TOYOTA", "AQUA"),
    "aqua":            ("TOYOTA", "AQUA"),
    "ヤリス":          ("TOYOTA", "YARIS"),
    "yaris":           ("TOYOTA", "YARIS"),
    "ヤリスクロス":    ("TOYOTA", "YARIS_CROSS"),
    "yariscross":      ("TOYOTA", "YARIS_CROSS"),
    "ノア":            ("TOYOTA", "NOAH"),
    "noah":            ("TOYOTA", "NOAH"),
    "ヴォクシー":      ("TOYOTA", "VOXY"),
    "voxy":            ("TOYOTA", "VOXY"),
    "シエンタ":        ("TOYOTA", "SIENTA"),
    "sienta":          ("TOYOTA", "SIENTA"),
    "rav4":            ("TOYOTA", "RAV4"),
    "スープラ":        ("TOYOTA", "SUPRA"),
    "supra":           ("TOYOTA", "SUPRA"),
    "gr86":            ("TOYOTA", "GR86"),
    "86":              ("TOYOTA", "GR86"),
    # ── ホンダ ──
    "n-box":           ("HONDA", "N-BOX"),
    "nbox":            ("HONDA", "N-BOX"),
    "フィット":        ("HONDA", "FIT"),
    "fit":             ("HONDA", "FIT"),
    "jazz":            ("HONDA", "FIT"),
    "ステップワゴン":  ("HONDA", "STEP_WGN"),
    "stepwgn":         ("HONDA", "STEP_WGN"),
    "フリード":        ("HONDA", "FREED"),
    "freed":           ("HONDA", "FREED"),
    "ヴェゼル":        ("HONDA", "VEZEL"),
    "vezel":           ("HONDA", "VEZEL"),
    "cr-v":            ("HONDA", "CR-V"),
    "オデッセイ":      ("HONDA", "ODYSSEY"),
    "odyssey":         ("HONDA", "ODYSSEY"),
    "シビック":        ("HONDA", "CIVIC"),
    "civic":           ("HONDA", "CIVIC"),
    # ── 日産 ──
    "ノート":          ("NISSAN", "NOTE"),
    "note":            ("NISSAN", "NOTE"),
    "リーフ":          ("NISSAN", "LEAF"),
    "leaf":            ("NISSAN", "LEAF"),
    "セレナ":          ("NISSAN", "SERENA"),
    "serena":          ("NISSAN", "SERENA"),
    "エクストレイル":  ("NISSAN", "X-TRAIL"),
    "x-trail":         ("NISSAN", "X-TRAIL"),
    "xtrail":          ("NISSAN", "X-TRAIL"),
    "キックス":        ("NISSAN", "KICKS"),
    "kicks":           ("NISSAN", "KICKS"),
    # ── マツダ ──
    "cx-5":            ("MAZDA", "CX-5"),
    "cx5":             ("MAZDA", "CX-5"),
    "cx-30":           ("MAZDA", "CX-30"),
    "cx30":            ("MAZDA", "CX-30"),
    "マツダ3":         ("MAZDA", "MAZDA3"),
    "mazda3":          ("MAZDA", "MAZDA3"),
    "アテンザ":        ("MAZDA", "ATENZA"),
    "atenza":          ("MAZDA", "ATENZA"),
    "アクセラ":        ("MAZDA", "AXELA"),
    "axela":           ("MAZDA", "AXELA"),
    "ロードスター":    ("MAZDA", "ROADSTER"),
    "roadster":        ("MAZDA", "ROADSTER"),
    "mx-5":            ("MAZDA", "ROADSTER"),
    "miata":           ("MAZDA", "ROADSTER"),
    # ── スバル ──
    "フォレスター":    ("SUBARU", "FORESTER"),
    "forester":        ("SUBARU", "FORESTER"),
    "アウトバック":    ("SUBARU", "OUTBACK"),
    "outback":         ("SUBARU", "OUTBACK"),
    "インプレッサ":    ("SUBARU", "IMPREZA"),
    "impreza":         ("SUBARU", "IMPREZA"),
    "レヴォーグ":      ("SUBARU", "LEVORG"),
    "levorg":          ("SUBARU", "LEVORG"),
    # ── スズキ ──
    "ジムニー":        ("SUZUKI", "JIMNY"),
    "jimny":           ("SUZUKI", "JIMNY"),
    "スペーシア":      ("SUZUKI", "SPACIA"),
    "spacia":          ("SUZUKI", "SPACIA"),
    "アルト":          ("SUZUKI", "ALTO"),
    "alto":            ("SUZUKI", "ALTO"),
    "エブリイ":        ("SUZUKI", "EVERY"),
    "every":           ("SUZUKI", "EVERY"),
    # ── ダイハツ ──
    "タント":          ("DAIHATSU", "TANTO"),
    "tanto":           ("DAIHATSU", "TANTO"),
    "ムーヴ":          ("DAIHATSU", "MOVE"),
    "move":            ("DAIHATSU", "MOVE"),
    "ミラ":            ("DAIHATSU", "MIRA"),
    "mira":            ("DAIHATSU", "MIRA"),
    # ── レクサス ──
    "rx":              ("LEXUS", "RX"),
    "nx":              ("LEXUS", "NX"),
    "is":              ("LEXUS", "IS"),
    "es":              ("LEXUS", "ES"),
    "ls":              ("LEXUS", "LS"),
    "ux":              ("LEXUS", "UX"),
    "lc":              ("LEXUS", "LC"),
    # ── ランドローバー ──
    "defender":        ("LAND_ROVER", "DEFENDER"),
    "デフェンダー":    ("LAND_ROVER", "DEFENDER"),
    "range rover":     ("LAND_ROVER", "RANGE_ROVER"),
    "rangerover":      ("LAND_ROVER", "RANGE_ROVER"),
    "レンジローバー":  ("LAND_ROVER", "RANGE_ROVER"),
    "discovery":       ("LAND_ROVER", "DISCOVERY"),
    "ディスカバリー":  ("LAND_ROVER", "DISCOVERY"),
    # ── アウディ ──
    "a3":              ("AUDI", "A3"),
    "a4":              ("AUDI", "A4"),
    "a5":              ("AUDI", "A5"),
    "a6":              ("AUDI", "A6"),
    "q5":              ("AUDI", "Q5"),
    "q7":              ("AUDI", "Q7"),
    # ── フォルクスワーゲン ──
    "ゴルフ":          ("VOLKSWAGEN", "GOLF"),
    "golf":            ("VOLKSWAGEN", "GOLF"),
    "ポロ":            ("VOLKSWAGEN", "POLO"),
    "polo":            ("VOLKSWAGEN", "POLO"),
    "ティグアン":      ("VOLKSWAGEN", "TIGUAN"),
    "tiguan":          ("VOLKSWAGEN", "TIGUAN"),
    "t-roc":           ("VOLKSWAGEN", "T-ROC"),
    "troc":            ("VOLKSWAGEN", "T-ROC"),
}

# メーカー名 → goonet メーカーコード
_MAKER_MAP = {
    "ポルシェ": "PORSCHE", "porsche": "PORSCHE",
    "メルセデス": "MERCEDES_BENZ", "メルセデスベンツ": "MERCEDES_BENZ",
    "mercedes": "MERCEDES_BENZ", "mercedes-benz": "MERCEDES_BENZ",
    "bmw": "BMW", "BMW": "BMW",
    "トヨタ": "TOYOTA", "toyota": "TOYOTA",
    "ホンダ": "HONDA", "honda": "HONDA",
    "日産": "NISSAN", "nissan": "NISSAN",
    "マツダ": "MAZDA", "mazda": "MAZDA",
    "スバル": "SUBARU", "subaru": "SUBARU",
    "スズキ": "SUZUKI", "suzuki": "SUZUKI",
    "ダイハツ": "DAIHATSU", "daihatsu": "DAIHATSU",
    "レクサス": "LEXUS", "lexus": "LEXUS",
    "ランドローバー": "LAND_ROVER", "land_rover": "LAND_ROVER",
    "landrover": "LAND_ROVER", "land rover": "LAND_ROVER",
    "ジャガー": "JAGUAR", "jaguar": "JAGUAR",
    "アウディ": "AUDI", "audi": "AUDI",
    "フォルクスワーゲン": "VOLKSWAGEN", "volkswagen": "VOLKSWAGEN", "vw": "VOLKSWAGEN",
    "ボルボ": "VOLVO", "volvo": "VOLVO",
    "フェラーリ": "FERRARI", "ferrari": "FERRARI",
    "ランボルギーニ": "LAMBORGHINI", "lamborghini": "LAMBORGHINI",
    "三菱": "MITSUBISHI", "mitsubishi": "MITSUBISHI",
}


def _lookup_goonet_codes(maker: str, model: str) -> tuple[str, str] | None:
    """日本語または英語のメーカー/車種名から goonet コードを返す"""
    model_norm = model.lower().strip().replace(" ", "").replace("　", "")

    # モデル名で直接マッピング
    for key, codes in _GOONET_MAP.items():
        key_norm = key.lower().replace(" ", "").replace("　", "")
        if key_norm == model_norm or key_norm in model_norm or model_norm in key_norm:
            return codes

    # メーカーコードを特定
    maker_norm = maker.lower().strip()
    maker_code = _MAKER_MAP.get(maker_norm)
    if not maker_code:
        for key, code in _MAKER_MAP.items():
            if key in maker_norm or maker_norm in key:
                maker_code = code
                break

    if maker_code:
        # ブランドページから動的にモデルコードを探索
        dynamic = _best_model_code(maker_code, model)
        if dynamic:
            return (maker_code, dynamic)
        # フォールバック: モデル名をそのままコードに
        model_code_guess = model.upper().replace(" ", "_").replace("　", "_")
        return (maker_code, model_code_guess)

    return None


# ─────────────────── HTTP ヘッダー ────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.201 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.goo-net.com/",
}

# ブランドページから取得したモデルコードのキャッシュ（プロセス内）
_brand_model_cache: dict[str, list[str]] = {}


def _fetch_brand_models(maker_code: str) -> list[str]:
    """ブランドページから全モデルコードを取得（セッションキャッシュ）"""
    if maker_code in _brand_model_cache:
        return _brand_model_cache[maker_code]
    if not _requests_ok:
        return []
    import requests as req
    url = f"https://www.goo-net.com/usedcar/brand-{maker_code}/"
    try:
        time.sleep(random.uniform(0.3, 0.6))
        r = req.get(url, headers=_HEADERS, timeout=12)
        if r.status_code != 200:
            return []
        pattern = re.compile(rf"/usedcar/brand-{maker_code}/car-([^/]+)/")
        codes = list(dict.fromkeys(
            m.group(1)
            for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True)
            for m in [pattern.search(a["href"])]
            if m
        ))
        _brand_model_cache[maker_code] = codes
        logger.debug(f"brand models fetched: {maker_code} → {len(codes)} codes")
        return codes
    except Exception as e:
        logger.warning(f"brand model fetch failed {maker_code}: {e}")
        return []


def _best_model_code(maker_code: str, model_name: str) -> str | None:
    """入力モデル名に最もマッチするgoonetモデルコードをスコアリングで返す"""
    codes = _fetch_brand_models(maker_code)
    if not codes:
        return None

    model_upper = model_name.upper().replace(" ", "").replace("-", "")

    # クラス識別子（先頭の英字部分）を抽出
    m = re.match(r"([A-Z]+)", model_upper)
    class_prefix = m.group(1) if m else ""

    # AMGグレード番号（43/53/63/65）またはAMG文字列を含む場合はAMGフラグ
    is_amg = bool(re.search(r"(43|53|63|65|AMG)", model_upper))

    best_code = None
    best_score = -1

    for code in codes:
        code_upper = code.upper().replace("_", "").replace("-", "")
        score = 0

        # クラス識別子の一致
        if class_prefix and code_upper.startswith(class_prefix):
            score += 5

        # 1文字クラス(C/E/S等)の場合、"CCLASS"形式のコードを優先
        # → "CL"や"CLS"など別シリーズとの混同を防ぐ
        if len(class_prefix) == 1:
            if f"{class_prefix}CLASS" in code_upper:
                score += 3
            elif code_upper.startswith(class_prefix) and "CLASS" not in code_upper:
                score -= 2

        # AMGフラグとコードのAMG含有の一致
        if is_amg and "AMG" in code_upper:
            score += 5
        elif is_amg and "AMG" not in code_upper:
            score -= 2
        elif not is_amg and "AMG" in code_upper:
            score -= 2

        # 完全サブストリング一致ボーナス
        if model_upper in code_upper or code_upper in model_upper:
            score += 8

        # 数字の一致（例: "63" がコードに含まれるか）
        for num in re.findall(r"\d+", model_upper):
            if num in code_upper:
                score += 3

        if score > best_score:
            best_score = score
            best_code = code

    if best_score >= 3:
        logger.info(f"dynamic model match: '{model_name}' → '{best_code}' (score={best_score})")
        return best_code
    return None


def _fetch_goonet(maker_code: str, model_code: str, page: int = 1) -> list[dict]:
    """グーネットの1ページを取得してパース"""
    if not _requests_ok:
        return []
    import requests as req

    base = f"https://www.goo-net.com/usedcar/brand-{maker_code}/car-{model_code}/"
    url = base if page == 1 else f"{base}?p={page}"
    try:
        time.sleep(random.uniform(0.3, 0.8))
        r = req.get(url, headers=_HEADERS, timeout=12)
        if r.status_code != 200:
            logger.debug(f"goonet {url} → {r.status_code}")
            return []
        return _parse_goonet_html(r.text)
    except Exception as e:
        logger.warning(f"goonet fetch error {url}: {e}")
        return []


def _parse_goonet_html(html: str, target_year: int | None = None) -> list[dict]:
    """グーネット HTML からリスト抽出"""
    soup = BeautifulSoup(html, "lxml")
    listings = []

    for card in soup.select("div.databox-column"):
        txt = card.get_text(separator=" ", strip=True)

        # 車両本体価格: div.hontai-adaption 内の最初の p.num
        # (外側の div.hontai-price > p.num.num-red は支払総額なので使わない)
        price = None
        body_el = card.select_one("div.hontai-adaption div.hontai-price p.num")
        if body_el:
            m = re.search(r"(\d+(?:\.\d+)?)", body_el.get_text())
            if m:
                price = float(m.group(1))
        if price is None:
            cp = card.select_one("div.carmodel_price")
            if cp:
                m = re.search(r"(\d+(?:\.\d+)?)\s*万", cp.get_text())
                if m:
                    price = float(m.group(1))

        # 年式: テキストから抽出のみ（デフォルト値を使わない）
        year = None
        m = re.search(r"年\s*(20\d{2}|19\d{2})\s*年", txt)
        if not m:
            m = re.search(r"(20\d{2}|19\d{2})\s*年", txt)
        if m:
            year = int(m.group(1))

        # 走行距離
        mileage = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*[Kk][Mm]", txt)
        if m:
            mileage = int(float(m.group(1)) * 10000)
        else:
            m = re.search(r"(\d{3,7})\s*[Kk][Mm]", txt)
            if m:
                mileage = int(m.group(1))

        # 年式が取れた場合のみ追加（デフォルト年式への落とし込みをしない）
        if price and year and 10 <= price <= 9000:
            listings.append({
                "price": price,
                "year": year,
                "age": max(0, CURRENT_YEAR - year),
                "mileage": mileage or max(0, CURRENT_YEAR - year) * 10000,
                "source": "goonet",
            })

    logger.info(f"goonet parsed {len(listings)} listings")
    return listings


def scrape_goonet(maker: str, model: str, year: int) -> list[dict]:
    """
    グーネットから全年式の中古車リストを取得（複数ページ）
    年式フィルタは aggregator._calc_current_price 側で実施。
    Returns: list of {price, year, age, mileage, source}
    """
    codes = _lookup_goonet_codes(maker, model)
    if not codes:
        logger.info(f"goonet: no code mapping for {maker} {model}")
        return []

    maker_code, model_code = codes
    logger.info(f"goonet scraping: brand-{maker_code}/car-{model_code}/ pages=1-4")

    all_listings = []
    for page in range(1, 5):
        listings = _fetch_goonet(maker_code, model_code, page)
        if not listings:
            break
        all_listings.extend(listings)

    # 重複排除（同価格・同年式）
    seen = set()
    result = []
    for l in all_listings:
        key = (round(l["price"], 0), l["year"])
        if key not in seen:
            seen.add(key)
            result.append(l)

    year_matched = sum(1 for l in result if abs(l["year"] - year) <= 1)
    logger.info(f"goonet total: {len(result)} unique listings ({year_matched} within ±1yr of {year})")
    return result
