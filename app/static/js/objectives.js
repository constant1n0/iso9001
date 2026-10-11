/* Este archivo es parte de "ISO9001 QMS" (GPL-3.0-or-later). */
/* Trend chart of a quality objective: its measurements against the target line.
   The data comes from the JSON script "objective-data" (no inline script: CSP). */
(() => {
  "use strict";

  const source = document.getElementById("objective-data");
  const canvas = document.getElementById("chart-objetivo");
  if (!source || !canvas || typeof Chart === "undefined") { return; }
  const data = JSON.parse(source.textContent);

  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const bone = token("--bone");
  const canary = token("--canary");
  const ok = token("--ok");
  const danger = token("--danger");
  const unit = data.unidad ? ` ${data.unidad}` : "";
  const number = (value) => value.toLocaleString("es-ES", { maximumFractionDigits: 4 });
  const day = (iso) => {
    const [year, month, date] = iso.split("-");
    return `${date}/${month}/${year}`;
  };
  const pointColours = data.cumple.map((met) => (met ? ok : danger));
  const single = data.valores.length === 1;  // a one-point target line needs a visible point

  Chart.defaults.color = token("--bone-mute");
  Chart.defaults.borderColor = token("--ink-3");
  Chart.defaults.font.family = token("--font-mono");
  Chart.defaults.font.size = 12;
  Chart.defaults.maintainAspectRatio = false;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    Chart.defaults.animation = false;
  }

  new Chart(canvas, {
    type: "line",
    data: {
      labels: data.fechas.map(day),
      datasets: [
        {
          label: data.indicador,
          data: data.valores,
          borderColor: bone,
          pointBackgroundColor: pointColours,
          pointBorderColor: pointColours,
          pointRadius: 5,
          tension: 0.2
        },
        {
          label: `Meta ${data.sentido} ${number(data.meta)}${unit}`,
          data: data.valores.map(() => data.meta),
          borderColor: canary,
          backgroundColor: canary,
          borderDash: [6, 4],
          pointRadius: single ? 4 : 0,
          pointStyle: "line"
        }
      ]
    },
    options: {
      plugins: {
        legend: { position: "bottom", labels: { color: bone, boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (item) => `${item.dataset.label}: ${number(item.parsed.y)}${unit}`
          }
        }
      },
      scales: { x: { grid: { display: false } } }
    }
  });
})();
