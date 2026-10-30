/* Admin reports: draws the charts from the JSON embedded in the page. */
(function () {
    "use strict";
    var dataEl = document.getElementById("report-data");
    if (!dataEl || !window.Chart) { return; }
    var data = JSON.parse(dataEl.textContent);
    // Draw once the web font is in, so Chart.js measures labels at their real width.
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(draw);

    function draw() {

    // Categorical slots in fixed order, and the reserved status colours.
    var SERIES = ["#2a78d6", "#eb6834", "#1baf7a"];
    var STATUS = { Confirmed: "#0ca30c", Pending: "#fab219", Cancelled: "#d03b3b" };
    var CLASS_ORDER = ["Economy", "Business", "First Class"];
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = "#6b7280";
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.tooltip.padding = 10;

    var ksh = function (n) { return "KSh " + Math.round(n).toLocaleString("en-KE"); };
    var num = function (n) { return Number(n).toLocaleString("en-KE"); };
    var grid = { color: "#e5e7eb" };
    var barStyle = { backgroundColor: SERIES[0], hoverBackgroundColor: "#1c5cab", borderRadius: 4,
                     borderSkipped: "start", maxBarThickness: 36 };

    function bucketLabel(b) {
        if (/^\d{4}-\d{2}$/.test(b)) { return MONTHS[Number(b.slice(5)) - 1] + " " + b.slice(0, 4); }
        if (/W/.test(b)) { return "Week " + b.split("W")[1]; }
        return Number(b.slice(8)) + " " + MONTHS[Number(b.slice(5, 7)) - 1];
    }

    new Chart(document.getElementById("chart-trend"), {
        type: "bar",
        data: {
            labels: data.trend.map(function (r) { return bucketLabel(r.bucket); }),
            datasets: [Object.assign({ label: "Confirmed revenue", data: data.trend.map(function (r) { return r.revenue; }) }, barStyle)]
        },
        options: {
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    label: function (c) { return ksh(c.raw); },
                    afterLabel: function (c) { return num(data.trend[c.dataIndex].passengers) + " passengers"; }
                } }
            },
            scales: {
                x: { grid: { display: false } },
                y: { grid: grid, border: { display: false }, ticks: { callback: function (v) {
                    return v >= 1e6 ? "KSh " + v / 1e6 + "M" : v >= 1e3 ? "KSh " + v / 1e3 + "K" : v; } } }
            }
        }
    });

    function horizontalBar(id, rows, value, format, tick, max) {
        new Chart(document.getElementById(id), {
            type: "bar",
            data: {
                labels: rows.map(function (r) { return r.city; }),
                datasets: [Object.assign({ data: rows.map(function (r) { return r[value]; }) }, barStyle,
                                         { borderSkipped: "left" })]
            },
            options: {
                indexAxis: "y",
                layout: { padding: { left: 4 } },
                plugins: { legend: { display: false },
                           tooltip: { callbacks: { label: function (c) { return format(c.raw); } } } },
                scales: {
                    x: { grid: grid, border: { display: false }, max: max, ticks: { maxRotation: 0, callback: function (v) { return tick(v); } } },
                    y: { grid: { display: false }, ticks: { autoSkip: false } }
                }
            }
        });
    }
    horizontalBar("chart-destinations", data.destinations, "passengers", function (v) { return num(v) + (v === 1 ? " passenger" : " passengers"); }, num);
    var percent = function (v) { return v + "%"; };
    horizontalBar("chart-occupancy", data.occupancy, "percent", percent, percent, 100);

    function doughnut(id, labels, values, colors, unit) {
        var total = values.reduce(function (a, b) { return a + b; }, 0);
        var pct = function (v) { return total ? Math.round(100 * v / total) + "%" : "0%"; };
        new Chart(document.getElementById(id), {
            type: "doughnut",
            data: { labels: labels, datasets: [{ data: values, backgroundColor: colors,
                                                 borderColor: "#ffffff", borderWidth: 2, hoverOffset: 6 }] },
            options: {
                cutout: "60%",
                plugins: {
                    // Legend carries the label, count and share so colour is never the only cue.
                    legend: { position: "right", labels: { usePointStyle: true, pointStyle: "rectRounded",
                        generateLabels: function (chart) {
                            return Chart.overrides.doughnut.plugins.legend.labels.generateLabels(chart).map(function (item) {
                                item.text = labels[item.index] + ": " + num(values[item.index]) + " (" + pct(values[item.index]) + ")";
                                return item;
                            });
                        } } },
                    tooltip: { callbacks: { label: function (c) { return " " + num(c.raw) + " " + unit + " (" + pct(c.raw) + ")"; } } }
                }
            }
        });
    }
    var statuses = ["Confirmed", "Pending", "Cancelled"].filter(function (s) {
        return data.status.some(function (r) { return r.status === s; });
    });
    doughnut("chart-status", statuses,
             statuses.map(function (s) { return data.status.filter(function (r) { return r.status === s; })[0].bookings; }),
             statuses.map(function (s) { return STATUS[s]; }), "bookings");
    var classes = CLASS_ORDER.filter(function (c) { return data.classes.some(function (r) { return r.travel_class === c; }); });
    doughnut("chart-classes", classes,
             classes.map(function (c) { return data.classes.filter(function (r) { return r.travel_class === c; })[0].passengers; }),
             classes.map(function (c) { return SERIES[CLASS_ORDER.indexOf(c)]; }), "passengers");
    }
})();
