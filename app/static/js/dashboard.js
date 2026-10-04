/* Este archivo es parte de "ISO9001 QMS" (GPL-3.0-or-later). */
(function () {
  "use strict";

  var source = document.getElementById("dashboard-data");
  if (!source || typeof Chart === "undefined") { return; }
  var data = JSON.parse(source.textContent);

  var css = getComputedStyle(document.documentElement);
  function token(name) { return css.getPropertyValue(name).trim(); }
  var canary = token("--canary");
  var bone = token("--bone");
  var boneMute = token("--bone-mute");
  var ink3 = token("--ink-3");
  var danger = token("--danger");
  var MONTHS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

  Chart.defaults.color = boneMute;
  Chart.defaults.borderColor = ink3;
  Chart.defaults.font.family = token("--font-mono");
  Chart.defaults.font.size = 12;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : Chart.defaults.animation;

  var nc = data.no_conformidades;
  new Chart(document.getElementById("chart-nc"), {
    type: "doughnut",
    data: {
      labels: ["Abiertas", "Cerradas"],
      datasets: [{
        data: [nc.abiertas, nc.cerradas],
        backgroundColor: [danger, bone],
        borderColor: token("--ink-1"),
        borderWidth: 3
      }]
    },
    options: {
      cutout: "68%",
      plugins: { legend: { position: "bottom", labels: { color: bone, boxWidth: 12 } } }
    }
  });

  var sat = data.satisfaccion;
  new Chart(document.getElementById("chart-satisfaccion"), {
    type: "bar",
    data: {
      labels: sat.meses.map(function (ym) {
        var parts = ym.split("-");
        return MONTHS[Number(parts[1]) - 1] + " " + parts[0];
      }),
      datasets: [{ label: "Media", data: sat.promedios, backgroundColor: canary, maxBarThickness: 36 }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, max: 10, ticks: { stepSize: 2 } },
        x: { grid: { display: false } }
      }
    }
  });
})();
