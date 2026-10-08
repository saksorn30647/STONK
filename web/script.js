/*
 * UI LAYER -- this file only manages what's shown/clicked/open in the browser.
 * It does NOT run any DCA math. When you click Run, it sends your inputs to
 * the Python server (app.py -> STKDCA.py) and draws whatever numbers come back.
 */

const COLORS = ["#f0c33b", "#3d8a44", "#e1573f", "#4f90ce", "#efa7c2"];
const MIN_STOCK_PCT = 0.01;

// Line colors for the comparison chart. Darker than the site's --blue/--gold so
// the thin lines stay visible on white (checked for colorblind separation too).
const WEEKLY_COLOR = "#2f6fb0";
const MONTHLY_COLOR = "#c4870a";
const MUTED_INK = "#6b6355";
const GRID_LINE = "#ece6d8";

let portfolio = [
  { ticker: "VOO", pct: 60 },
  { ticker: "AAPL", pct: 40 },
];

let growthChart = null; // the Chart.js chart object, kept so we can replace it on the next run

const donutEl = document.getElementById("donut");
const legendEl = document.getElementById("legend");
const centerLabelEl = document.getElementById("chart-center-label");
const centerValueEl = document.getElementById("chart-center-value");
const budgetInput = document.getElementById("budget");
const startInput = document.getElementById("start-date");
const endInput = document.getElementById("end-date");
const strategyInput = document.getElementById("strategy");
const startBtn = document.getElementById("start-btn");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

const modal = document.getElementById("portfolio-modal");
const stockRowsEl = document.getElementById("stock-rows");
const modalErrorEl = document.getElementById("modal-error");
const newTickerInput = document.getElementById("new-ticker");

function colorFor(index) {
  return COLORS[index % COLORS.length];
}

/* -----------------------------------------------------------------------
   Donut chart + legend rendering (always shows your allocation split)
----------------------------------------------------------------------- */
function renderChart() {
  const r = 80;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  donutEl.innerHTML = "";

  // Faint full-circle track so a single-stock portfolio still reads as a ring.
  const track = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  track.setAttribute("cx", 100);
  track.setAttribute("cy", 100);
  track.setAttribute("r", r);
  track.setAttribute("fill", "none");
  track.setAttribute("stroke", "#e5ddc9");
  track.setAttribute("stroke-width", 32);
  donutEl.appendChild(track);

  const total = portfolio.reduce((sum, s) => sum + s.pct, 0);

  portfolio.forEach((stock, i) => {
    const length = (stock.pct / total) * circumference;

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", 100);
    circle.setAttribute("cy", 100);
    circle.setAttribute("r", r);
    circle.setAttribute("fill", "none");
    circle.setAttribute("stroke", colorFor(i));
    circle.setAttribute("stroke-width", 32);
    circle.setAttribute("stroke-dasharray", `${length} ${circumference - length}`);
    circle.setAttribute("stroke-dashoffset", -offset);
    circle.setAttribute("tabindex", "0");
    circle.setAttribute("role", "img");
    circle.setAttribute("aria-label", `${stock.ticker}, ${stock.pct.toFixed(1)}%`);

    donutEl.appendChild(circle);
    offset += length;
  });

  centerLabelEl.textContent = "Total budget";
  centerValueEl.textContent = `$${budgetInput.value || 0}/mo`;

  renderLegend(total);
}

function renderLegend(total) {
  legendEl.innerHTML = "";
  portfolio.forEach((stock, i) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="swatch" style="background:${colorFor(i)}"></span>
      <span class="ticker">${stock.ticker}</span>
      <span class="pct">${((stock.pct / total) * 100).toFixed(1)}%</span><span></span>
    `;
    legendEl.appendChild(li);
  });
}

/* -----------------------------------------------------------------------
   Portfolio editor modal
----------------------------------------------------------------------- */
function openModal() {
  hideResults(); // results were for the OLD portfolio -- don't leave them up looking current
  modalErrorEl.textContent = "";
  renderStockRows();
  modal.showModal();
}

function renderStockRows() {
  stockRowsEl.innerHTML = "";

  portfolio.forEach((stock, i) => {
    const isLast = i === portfolio.length - 1;
    const li = document.createElement("li");

    li.innerHTML = `
      <span class="ticker">${stock.ticker}</span>
      <input
        type="number" min="0" max="100" step="0.1"
        value="${stock.pct.toFixed(2)}"
        data-index="${i}"
        ${isLast ? "disabled" : ""}
        aria-label="${stock.ticker} percentage"
      >
      <button type="button" class="row-delete" data-index="${i}" aria-label="Remove ${stock.ticker}" ${portfolio.length <= 1 ? "disabled" : ""}>&times;</button>
    `;
    stockRowsEl.appendChild(li);
  });

  stockRowsEl.querySelectorAll("input[data-index]").forEach((input) => {
    input.addEventListener("input", onPctEdit);
  });
  stockRowsEl.querySelectorAll(".row-delete").forEach((btn) => {
    btn.addEventListener("click", onDeleteStock);
  });
}

function recomputeLastRow() {
  if (portfolio.length === 0) return;
  const lastIndex = portfolio.length - 1;
  const sumOthers = portfolio.slice(0, lastIndex).reduce((sum, s) => sum + s.pct, 0);
  portfolio[lastIndex].pct = Math.round((100 - sumOthers) * 100) / 100;
}

function onPctEdit(e) {
  const i = Number(e.target.dataset.index);
  const value = Math.max(0, Math.min(100, Number(e.target.value) || 0));
  portfolio[i].pct = value;
  recomputeLastRow();

  const lastPct = portfolio[portfolio.length - 1].pct;
  modalErrorEl.textContent = lastPct < MIN_STOCK_PCT
    ? "Percentages add up to over 100% -- reduce another stock's share."
    : "";

  renderStockRows();
  renderChart();
}

function onDeleteStock(e) {
  if (portfolio.length <= 1) return;
  const i = Number(e.target.dataset.index);
  portfolio.splice(i, 1);
  recomputeLastRow();
  renderStockRows();
  renderChart();
}

function onAddStock() {
  const ticker = newTickerInput.value.trim().toUpperCase();
  if (!ticker) {
    modalErrorEl.textContent = "Enter a ticker symbol first.";
    return;
  }
  if (portfolio.some((s) => s.ticker === ticker)) {
    modalErrorEl.textContent = `${ticker} is already in your portfolio.`;
    return;
  }

  // Give the new stock a slice of whatever the current last row has,
  // mirroring the CLI's "everyone gets a positive minimum" rule.
  const lastIndex = portfolio.length - 1;
  const available = portfolio[lastIndex].pct;
  const grant = available > MIN_STOCK_PCT ? Math.max(MIN_STOCK_PCT, available / 2) : MIN_STOCK_PCT;

  portfolio.splice(lastIndex, 0, { ticker, pct: grant });
  recomputeLastRow();

  newTickerInput.value = "";
  modalErrorEl.textContent = "";
  renderStockRows();
  renderChart();
}

/* -----------------------------------------------------------------------
   Talking to the Python server
----------------------------------------------------------------------- */
async function runSimulation() {
  // Package the form + portfolio into one object; the server (app.py) checks it all.
  const payload = {
    budget: Number(budgetInput.value),
    start: startInput.value,
    end: endInput.value,
    strategy: strategyInput.value,
    portfolio: portfolio.map((s) => ({ ticker: s.ticker, pct: s.pct })),
  };
  const portfolioSnapshot = portfolio.map((s) => ({ ...s })); // what these results belong to

  setBusy(true, "Downloading prices and running both simulations…");

  // Render's free tier "sleeps" when nobody's visited for a while; the first
  // request then waits for it to wake up. Warn the user instead of looking frozen.
  const slowTimer = setTimeout(() => {
    statusEl.textContent = "Still working… if the server was asleep, waking it up can take up to a minute.";
  }, 8000);

  try {
    // fetch() sends an HTTP request to our own server, at the /api/simulate address
    const response = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // .catch: if the server crashed it may send back an HTML error page, not JSON
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      // The server sends back {error: "..."} with a human-readable reason
      throw new Error(data.error || "Something went wrong on the server.");
    }

    renderResults(data, portfolioSnapshot);
    setBusy(false, "");
  } catch (err) {
    // A TypeError here means the request never reached the server at all
    const message = err instanceof TypeError
      ? "Couldn't reach the server. Is app.py running?"
      : err.message;
    setBusy(false, message, true);
  } finally {
    clearTimeout(slowTimer);
  }
}

function setBusy(isBusy, message, isError = false) {
  startBtn.disabled = isBusy;
  startBtn.textContent = isBusy ? "Running…" : "Run weekly vs monthly";
  statusEl.textContent = message;
  statusEl.classList.toggle("status--error", isError);
}

function hideResults() {
  resultsEl.hidden = true;
}

/* -----------------------------------------------------------------------
   Results rendering
----------------------------------------------------------------------- */
function money(value) {
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function signedPct(value) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function growthClass(value) {
  return value >= 0 ? "growth--up" : "growth--down";
}

function renderResults(data, portfolioSnapshot) {
  const { weekly, monthly } = data;

  // Verdict: compare PERFORMANCE %, not raw $ -- under the "dip" strategy the two
  // can invest different totals, so raw final value alone wouldn't be fair.
  const gap = monthly.performance_pct - weekly.performance_pct;
  const valueGap = Math.abs(monthly.final_value - weekly.final_value);
  const verdictEl = document.getElementById("verdict");
  if (Math.abs(gap) < 0.05) {
    verdictEl.textContent = `Dead heat: both finished within 0.05 percentage points (${data.start} to ${data.end}).`;
  } else {
    const winner = gap > 0 ? "Monthly" : "Weekly";
    verdictEl.textContent = `${winner} came out ahead by ${Math.abs(gap).toFixed(2)} percentage points, a ${money(valueGap)} difference in final value (${data.start} to ${data.end}).`;
  }

  renderCard(document.getElementById("card-weekly"), "Weekly", weekly);
  renderCard(document.getElementById("card-monthly"), "Monthly", monthly);
  renderStockTable(weekly, monthly, portfolioSnapshot);

  resultsEl.hidden = false;
  renderGrowthChart(data.chart); // after un-hiding, so Chart.js can measure the space it has
  resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderCard(cardEl, label, r) {
  cardEl.innerHTML = `
    <h3 class="compare__title"><span class="compare__swatch" aria-hidden="true"></span>${label}</h3>
    <p class="compare__value">${money(r.final_value)}</p>
    <p class="compare__perf ${growthClass(r.performance_pct)}">${signedPct(r.performance_pct)}</p>
    <dl class="compare__facts">
      <dt>Invested</dt><dd>${money(r.total_invested)}</dd>
      <dt>Fees</dt><dd>${money(r.total_fees)}</dd>
      <dt>Dividends</dt><dd>${money(r.total_dividends)}</dd>
      <dt>Buys</dt><dd>${r.num_buys.toLocaleString()}</dd>
    </dl>
  `;
}

function renderStockTable(weekly, monthly, portfolioSnapshot) {
  const body = document.getElementById("stock-table-body");
  body.innerHTML = "";
  portfolioSnapshot.forEach((stock, i) => {
    const w = weekly.per_stock[stock.ticker];
    const m = monthly.per_stock[stock.ticker];
    const row = document.createElement("tr");
    row.innerHTML = `
      <th scope="row"><span class="swatch" style="background:${colorFor(i)}"></span>${stock.ticker}</th>
      <td>${stock.pct.toFixed(1)}%</td>
      <td><span class="${growthClass(w.performance_pct)}">${signedPct(w.performance_pct)}</span> <span class="muted">${money(w.final_value)}</span></td>
      <td><span class="${growthClass(m.performance_pct)}">${signedPct(m.performance_pct)}</span> <span class="muted">${money(m.final_value)}</span></td>
    `;
    body.appendChild(row);
  });
}

// Small Chart.js plugin: draws a thin vertical line at the hovered date,
// so it's easy to read all lines at the same moment in time.
const crosshairPlugin = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip && chart.tooltip.getActiveElements();
    if (!active || !active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = MUTED_INK;
    ctx.stroke();
    ctx.restore();
  },
};

function renderGrowthChart(chart) {
  // Under "flat" both schedules invest the exact same cash, so one shared
  // "cash invested" line is enough. Under "dip" they can differ -> draw both.
  const sameInvested = chart.weekly_invested.every(
    (v, i) => Math.abs(v - chart.monthly_invested[i]) < 0.01
  );

  const valueLine = (label, data, color) => ({
    label, data, borderColor: color, backgroundColor: color,
    borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0,
  });
  const cashLine = (label, data, color) => ({
    label, data, borderColor: color, backgroundColor: color,
    borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 3,
  });

  const datasets = [
    valueLine("Weekly value", chart.weekly_value, WEEKLY_COLOR),
    valueLine("Monthly value", chart.monthly_value, MONTHLY_COLOR),
  ];
  if (sameInvested) {
    datasets.push(cashLine("Cash invested", chart.weekly_invested, MUTED_INK));
  } else {
    datasets.push(cashLine("Weekly cash in", chart.weekly_invested, WEEKLY_COLOR));
    datasets.push(cashLine("Monthly cash in", chart.monthly_invested, MONTHLY_COLOR));
  }

  const monthYear = (iso) =>
    new Date(iso).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });

  // Chart.js can't reuse a canvas that already has a chart on it -- remove the old one first
  if (growthChart) growthChart.destroy();

  Chart.defaults.font.family = '"Inter Tight", sans-serif';
  Chart.defaults.color = MUTED_INK;

  growthChart = new Chart(document.getElementById("growth-chart"), {
    type: "line",
    data: { labels: chart.dates, datasets },
    plugins: [crosshairPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false, // let the CSS container decide the height
      interaction: { mode: "index", intersect: false }, // hover anywhere above a date -> show all lines
      plugins: {
        legend: {
          position: "top",
          align: "start",
          labels: { usePointStyle: true, pointStyle: "line", boxWidth: 28, color: "#302302" },
        },
        tooltip: {
          callbacks: {
            title: (items) => monthYear(items[0].label),
            label: (item) => ` ${item.dataset.label}: ${money(item.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 7,
            callback(value) { return monthYear(this.getLabelForValue(value)); },
          },
        },
        y: {
          border: { display: false },
          grid: { color: GRID_LINE },
          ticks: {
            callback: (v) => (v >= 1000 ? `$${(v / 1000).toLocaleString()}k` : `$${v}`),
          },
        },
      },
    },
  });
}

/* -----------------------------------------------------------------------
   Wiring
----------------------------------------------------------------------- */
document.getElementById("edit-portfolio-btn").addEventListener("click", openModal);
document.getElementById("modal-close-btn").addEventListener("click", () => modal.close());
document.getElementById("add-stock-btn").addEventListener("click", onAddStock);

document.getElementById("portfolio-modal").addEventListener("submit", (e) => {
  const lastPct = portfolio[portfolio.length - 1].pct;
  if (lastPct < MIN_STOCK_PCT) {
    e.preventDefault();
    modalErrorEl.textContent = "Fix the percentages above 0% before closing.";
  }
});

document.getElementById("sim-form").addEventListener("submit", (e) => {
  e.preventDefault(); // stop the browser's default "reload the page" form behavior
  runSimulation();
});

budgetInput.addEventListener("input", renderChart);

// Can't pick an end date in the future (the server would clamp it anyway)
endInput.max = new Date().toISOString().slice(0, 10);

renderChart();
