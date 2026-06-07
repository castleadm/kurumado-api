"use strict";

let chart = null;
const $ = id => document.getElementById(id);

// 年次下落率 → S/A/B/C
function annualRating(rate) {
  if (rate < 7)  return "S";
  if (rate < 11) return "A";
  if (rate < 16) return "B";
  return "C";
}

// 5年累計下落率 → S/A/B/C（サマリー用）
function cumulativeRating(cum5) {
  if (cum5 < 30) return "S";
  if (cum5 < 45) return "A";
  if (cum5 < 60) return "B";
  return "C";
}

const RATING_LABELS = {
  S: "非常に高残価",
  A: "高残価",
  B: "標準的",
  C: "減価が速い",
};

async function predict() {
  const maker = $("maker").value.trim();
  const model = $("model").value.trim();
  const year = $("year").value.trim();
  const mileage = $("mileage").value.trim();
  const annualMileage = $("annual_mileage").value.trim();
  const grade = $("grade").value;
  const repairHistory = $("repair_history").value;
  const condition = $("condition").value;

  if (!maker || !model) { showError("メーカーと車種名を入力してください"); return; }
  if (!year || isNaN(year)) { showError("年式を正しく入力してください"); return; }

  hideError();
  showLoading(true);
  showResults(false);

  try {
    const resp = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        maker, model,
        year: parseInt(year),
        mileage: parseInt(mileage) || 50000,
        annual_mileage: parseInt(annualMileage) || 10000,
        grade,
        repair_history: repairHistory,
        condition,
        use_cache: true,
      }),
    });

    const data = await resp.json();
    if (!resp.ok || data.error) { showError(data.error || "エラーが発生しました"); return; }

    renderResults(data);
    showResults(true);
  } catch (err) {
    showError("通信エラー: " + err.message);
  } finally {
    showLoading(false);
  }
}

function renderResults(data) {
  const { current_price, predictions, market_summary, input, source_summary } = data;

  // 現在価格（売値）
  $("current-price").textContent = current_price ? `${current_price}万円` : "---";

  // 買取価格
  if (data.buyback_price) {
    $("buyback-price").textContent = `${data.buyback_price}万円`;
    if (data.buyback_source === "overseas" && data.overseas_buyback_floor) {
      const pct = Math.round(data.buyback_price / data.current_price * 100);
      $("buyback-ratio-info").textContent = `欧州市場参照・売値の${pct}%`;
    } else if (data.buyback_source === "domestic_p25") {
      const pct = Math.round(data.buyback_price / data.current_price * 100);
      $("buyback-ratio-info").textContent = `国内相場P25参照・売値の${pct}%`;
    } else {
      $("buyback-ratio-info").textContent = `売値の${Math.round(data.buyback_ratio * 100)}%（国内比率）`;
    }
  }

  // 価格ソースの表示
  const pwPrice = data.scraped_current_price;
  const matchedCount = data.current_price_matched_count || 0;
  if (pwPrice && matchedCount > 0) {
    $("price-source").textContent = `スクレイピング取得（${matchedCount}件の中央値）`;
    $("price-source").style.color = "var(--success)";
  } else if (data.data_points > 0) {
    $("price-source").textContent = `補間推定（${data.data_points}件のデータ）`;
    $("price-source").style.color = "var(--primary)";
  } else {
    $("price-source").textContent = "新車価格×減価率で推定";
    $("price-source").style.color = "var(--text-muted)";
  }

  // 市場価格帯
  if (market_summary && market_summary.total_listings > 0) {
    $("market-median").textContent = `${market_summary.median_price}万円`;
    $("market-range").textContent = `${market_summary.min_price}〜${market_summary.max_price}万円`;
  } else {
    $("market-median").textContent = "---";
    $("market-range").textContent = "業界標準データ使用";
  }

  $("data-points").textContent = data.data_points || 0;
  $("brand-info").textContent = data.brand || input.maker;

  // 車両条件バッジ
  const condBadgeArea = $("condition-badges");
  condBadgeArea.innerHTML = "";

  const gradeLabels = { base: "ベースグレード", mid: "中間グレード", high: "上位グレード", top: "最上位グレード" };
  const repairLabels = { none: "修復歴なし", minor: "軽微修復あり", major: "重大修復あり", unknown: "修復歴不明" };
  const condLabels = { excellent: "状態：良好", good: "状態：普通", fair: "状態：難あり", poor: "状態：要整備" };
  const repairColors = { none: "badge-jp", minor: "badge-method", major: "badge-uk", unknown: "badge-method" };

  const badges = [
    { text: gradeLabels[input.grade] || input.grade, cls: "badge-jp" },
    { text: repairLabels[input.repair_history] || input.repair_history, cls: repairColors[input.repair_history] || "badge-method" },
    { text: condLabels[input.condition] || input.condition, cls: "badge-jp" },
  ];
  if (data.grade_mult !== 1.0) {
    badges.push({ text: `グレード係数 ×${data.grade_mult}`, cls: "badge-method" });
  }
  if (data.repair_rate_add > 0) {
    badges.push({ text: `修復歴補正 +${(data.repair_rate_add * 100).toFixed(0)}%/年`, cls: "badge-uk" });
  }
  badges.forEach(({ text, cls }) => {
    const b = document.createElement("span");
    b.className = `badge ${cls}`;
    b.textContent = text;
    condBadgeArea.appendChild(b);
  });

  // データソースバッジ
  const srcBadgeArea = $("source-badges");
  srcBadgeArea.innerHTML = "";
  const jpSrc = ["goonet", "carsensor", "yahoo_auction", "rakuten_car"];
  const srcLabels = {
    goonet: "グーネット", carsensor: "カーセンサー",
    yahoo_auction: "Yahoo!オークション", rakuten_car: "楽天Car",
    kbb_us: "KBB(米国)", autotrader_uk: "AutoTrader UK",
    autoscout24: "AutoScout24(欧州)",
  };
  let hasAny = false;
  for (const [src, count] of Object.entries(source_summary || {})) {
    if (!count) continue;
    hasAny = true;
    const b = document.createElement("span");
    const cls = jpSrc.includes(src) ? "badge-jp" : src === "autoscout24" ? "badge-eu" : "badge-us";
    b.className = `badge ${cls}`;
    b.textContent = `${srcLabels[src] || src} (${count}件)`;
    srcBadgeArea.appendChild(b);
  }
  // 海外買取フロアバッジ
  if (data.overseas_buyback_floor) {
    const b = document.createElement("span");
    b.className = "badge badge-eu";
    b.textContent = `AutoScout24参照（買取フロア ${data.overseas_buyback_floor}万円）`;
    srcBadgeArea.appendChild(b);
  }
  if (!hasAny && !data.overseas_buyback_floor) {
    const b = document.createElement("span");
    b.className = "badge badge-method";
    b.textContent = "スクレイピングデータなし（新車価格×減価率で推定）";
    srcBadgeArea.appendChild(b);
  }

  // 予測手法
  const methodNames = {
    baseline: "業界標準減価率 + 新車価格テーブル",
    statistical: "指数減衰回帰（統計モデル）",
    ml: "ランダムフォレスト + 勾配ブースティング",
    hybrid: "ハイブリッド（統計 × ML × ベースライン）",
  };
  $("method-info").textContent =
    `予測手法: ${methodNames[data.method] || data.method} | ` +
    `データ数: ${data.data_points}件（日本${data.japan_data_points}件・海外${data.overseas_data_points}件）` +
    (data.model_note ? ` | ${data.model_note}` : "") +
    (data.brand_note ? ` | ${data.brand_note}` : "");

  $("cache-notice").style.display = data.from_cache ? "flex" : "none";

  // 5年後の累計評価（サマリーカード）
  const pred5 = predictions.find(p => p.future_age - input.current_age === 5) || predictions[Math.min(4, predictions.length - 1)];
  if (pred5) {
    const r = cumulativeRating(pred5.cumulative_depreciation);
    const el = $("residual-rating");
    el.textContent = r;
    el.className = `value rating-${r}`;
    $("residual-rating-sub").textContent = RATING_LABELS[r];
  }

  // グラフデータ（全予測年）
  const labels = [`現在 (${2026 - input.current_age}年式)`];
  const prices = [current_price];
  for (const p of predictions) {
    labels.push(`${p.calendar_year}年`);
    prices.push(p.predicted_price);
  }
  renderChart(labels, prices);
  renderTable(predictions, current_price);
}

function renderChart(labels, prices) {
  const ctx = $("price-chart").getContext("2d");
  if (chart) { chart.destroy(); chart = null; }

  const gradient = ctx.createLinearGradient(0, 0, 0, 280);
  gradient.addColorStop(0, "rgba(26,115,232,0.3)");
  gradient.addColorStop(1, "rgba(26,115,232,0.02)");

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "予測価格（万円）",
        data: prices,
        borderColor: "#1a73e8",
        backgroundColor: gradient,
        borderWidth: 2.5,
        pointRadius: 5,
        pointBackgroundColor: "#1a73e8",
        pointBorderColor: "#fff",
        pointBorderWidth: 2,
        tension: 0.3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => `  ${ctx.parsed.y.toFixed(1)}万円` },
          backgroundColor: "#202124",
          titleFont: { size: 13 },
          bodyFont: { size: 13 },
          padding: 10,
        },
      },
      scales: {
        y: {
          beginAtZero: false,
          grid: { color: "#f0f0f0" },
          ticks: { callback: v => `${v}万`, font: { size: 11 } },
          title: { display: true, text: "価格（万円）", font: { size: 11 } },
        },
        x: { grid: { display: false }, ticks: { font: { size: 11 } } },
      },
    },
  });
}

function renderTable(predictions, currentPrice) {
  const tbody = $("pred-tbody");
  tbody.innerHTML = "";
  for (const pred of predictions) {
    const color = pred.depreciation_rate > 15 ? "#ea4335"
                : pred.depreciation_rate > 8  ? "#f57c00" : "#34a853";
    const r = annualRating(pred.depreciation_rate);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${pred.calendar_year}年</td>
      <td>${pred.future_age}年</td>
      <td>${(pred.future_mileage / 10000).toFixed(1)}万km</td>
      <td class="price-cell">${pred.predicted_price.toFixed(1)}万円</td>
      <td class="buyback-cell">${pred.buyback_price != null ? pred.buyback_price.toFixed(1) + "万円" : "---"}</td>
      <td class="dep-cell" style="color:${color}">-${pred.depreciation_rate.toFixed(1)}%</td>
      <td class="cum-cell">-${pred.cumulative_depreciation.toFixed(1)}%</td>
      <td><span class="rating-badge rating-${r}">${r}</span></td>
    `;
    tbody.appendChild(tr);
  }
}

async function clearCache() {
  await fetch("/api/clear_cache", { method: "POST" });
  $("cache-notice").style.display = "none";
  alert("キャッシュをクリアしました。次回は最新データを取得します。");
}

function showLoading(visible) {
  $("loading").className = visible ? "loading visible" : "loading";
  $("predict-btn").disabled = visible;
  $("predict-btn").textContent = visible ? "分析中..." : "値下がりを予測する";
}

function showResults(visible) {
  $("result-panel").className = visible ? "result-panel visible" : "result-panel";
  $("empty-state").style.display = visible ? "none" : "block";
}

function showError(msg) {
  const el = $("error-box");
  el.textContent = msg;
  el.className = "error-box visible";
}
function hideError() { $("error-box").className = "error-box"; }

document.addEventListener("DOMContentLoaded", () => {
  ["maker", "model", "year", "mileage", "annual_mileage"].forEach(id => {
    $(id)?.addEventListener("keydown", e => { if (e.key === "Enter") predict(); });
  });

  document.querySelectorAll("[data-preset]").forEach(btn => {
    btn.addEventListener("click", () => {
      const [maker, model, year, mileage] = btn.dataset.preset.split("|");
      $("maker").value = maker;
      $("model").value = model;
      $("year").value = year;
      $("mileage").value = mileage;
    });
  });
});
