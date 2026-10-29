// The dashboard's behaviour, run with `node --test` against the real
// web/dashboard.js in a sandbox with a fake page and a fake Leaflet. No
// browser, server, or network needed.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const WEB = path.join(__dirname, "..", "web");
const HOOKS = [
    "state", "els", "init", "cacheElements", "api", "refresh", "applyDashboard", "selectMission",
    "acknowledgeAlert", "acknowledgeAllAlerts", "filteredRows", "visibleReadings", "statusText",
    "escapeHtml", "rowHtml", "tableColumns", "readingFacts", "render", "sortBy", "goLive",
    "initMap", "renderMap", "drawGrid", "formatTime", "formatDateTime"
];

const RISK_LEVELS = [
    { label: "Low", min: 0, color: "#6fae7e" },
    { label: "Moderate", min: 26, color: "#d0a44c" },
    { label: "High", min: 51, color: "#d4834a" },
    { label: "Severe", min: 76, color: "#c85a55" }
];
// What GET /config.js gives the page (the values in Server/config.py;
// test_routes.py checks the server provides every one of these keys).
const SETTINGS = {
    pollSeconds: 9,
    timeoutSeconds: 12,
    alertPageSize: 2,
    pageSize: 25,
    pageSizePhone: 10,
    replayStepSeconds: 0.6,
    maxZoom: 22,
    fitZoom: 21,
    openZoom: 19,
    focusZoom: 20,
    gridMinPixels: 20,
    shadingRadiusMeters: 3
};
const SENSORS = [
    { key: "temperature", label: "Temperature", unit: "°C", digits: 1 },
    { key: "ph", label: "pH", unit: "", digits: 2 },
    { key: "turbidity", label: "Turbidity", unit: "NTU", digits: 1 },
    { key: "conductivity", label: "Conductivity", unit: "µS/cm", digits: 0 },
    { key: "nitrate", label: "Nitrate", unit: "mg/L", digits: 2 },
    { key: "ammonium", label: "Ammonium", unit: "mg/L", digits: 2 }
];

function fakeElement(tag = "div") {
    return {
        tag,
        textContent: "",
        innerHTML: "",
        className: "",
        hidden: false,
        disabled: false,
        value: "",
        max: "0",
        checked: true,
        dataset: {},
        style: {},
        children: [],
        listeners: {},
        setAttribute(name, value) { this[name] = value; },
        addEventListener(type, handler) { this.listeners[type] = handler; },
        replaceChildren(...nodes) { this.children = nodes; },
        append(...nodes) { this.children.push(...nodes); },
        appendChild(node) { this.children.push(node); return node; },
        remove() {},
        click() {},
        closest() { return null; },
        getBoundingClientRect() { return { top: 0, bottom: 100 }; },
        scrollIntoView() {}
    };
}

function response(status, body) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
}

// Just enough of Leaflet to record what is drawn and where the map points.
function fakeLeaflet() {
    const map = {
        views: [],
        fits: [],
        setView(center) { this.views.push(Array.from(center)); return this; },
        fitBounds(bounds) { this.fits.push(bounds); },
        createPane() { return { style: {} }; },
        on() {},
        once() {},
        invalidateSize() {},
        getZoom() { return 15; },
        flyTo() {},
        // The visible area: a test sets it to zoom in or out. The whole world by default.
        view: { south: -60, north: 80, west: -180, east: 180, widthPixels: 800 },
        getBounds() {
            const view = this.view;
            return { getSouth: () => view.south, getNorth: () => view.north, getWest: () => view.west, getEast: () => view.east };
        },
        getSize() { return { x: this.view.widthPixels, y: 400 }; },
        getCenter() { return { lat: (this.view.south + this.view.north) / 2 }; }
    };
    const drawn = (kind, options) => ({
        kind,
        options,
        addTo(group) { group.items.push(this); return this; },
        bindPopup(html) { this.popup = html; return this; },
        on() { return this; },
        getPopup() { return { options: {} }; },
        openPopup() {}
    });
    return {
        map: () => map,
        control: { zoom: () => ({ addTo() {} }), scale: () => ({ addTo() {} }) },
        layerGroup: () => ({ items: [], addTo() { return this; }, clearLayers() { this.items.length = 0; } }),
        rectangle: (bounds, options) => drawn("rectangle", options),
        marker: (point, options) => drawn("marker", options),
        circle: (point, options) => drawn("circle", options),
        polyline: (points, options) => drawn("polyline", options),
        circleMarker: (point, options) => drawn("circleMarker", options),
        divIcon: (options) => options,
        latLngBounds: (points) => ({ points }),
        instance: map
    };
}

function load(fetchImpl = async () => response(200, {}), { leaflet, settings = SETTINGS } = {}) {
    const source = fs.readFileSync(path.join(WEB, "dashboard.js"), "utf8").replace(
        /\}\)\(\);\s*$/,
        `globalThis.__hooks = { ${HOOKS.join(", ")} };\n})();`
    );
    const elements = new Map();
    const layerToggles = ["cells", "readings", "track", "hotspots", "targets", "shading"].map((layer) => {
        const toggle = fakeElement("input");
        toggle.dataset.layer = layer;
        toggle.checked = layer !== "shading";
        return toggle;
    });
    const alerts = [];
    const fetchCalls = [];
    const document = {
        hidden: false,
        activeElement: null,
        body: fakeElement("body"),
        documentElement: { clientHeight: 800 },
        addEventListener() {},
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, fakeElement());
            return elements.get(id);
        },
        querySelectorAll: () => layerToggles,
        createElement: (tag) => fakeElement(tag)
    };
    const window = {
        innerHeight: 800,
        setTimeout: () => 1,
        clearTimeout() {},
        setInterval: () => 1,
        clearInterval() {},
        matchMedia: () => ({ matches: false }),
        alert: (message) => alerts.push(message),
        confirm: () => true,
        prompt: () => "",
        BLOOM_SETTINGS: settings
    };
    if (leaflet) window.L = leaflet;
    const context = {
        L: leaflet,
        document,
        window,
        AbortController,
        console,
        fetch: (url, options) => {
            fetchCalls.push({ url, options });
            return fetchImpl(url, options);
        }
    };
    vm.runInNewContext(source, context);
    const hooks = context.__hooks;
    hooks.cacheElements();
    return { hooks, alerts, fetchCalls, elements };
}

async function settle() {
    for (let index = 0; index < 5; index += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

function reading(id, overrides = {}) {
    return {
        id,
        takenAt: `2026-08-15T12:00:${String(id).padStart(2, "0")}-04:00`,
        lat: 42.28,
        lng: -83.74,
        gps: "fix",
        temperature: 21.5,
        ph: 7.5,
        turbidity: 5,
        conductivity: 500,
        nitrate: null,
        ammonium: null,
        risk: 20,
        level: "Low",
        confidence: 72,
        drivers: [],
        alert: false,
        cell: "1:1",
        scoredBy: "rules-v2",
        ...overrides
    };
}

function dashboard(overrides = {}) {
    return {
        mission: { id: 1, name: "Run", active: true, startedAt: "2026-08-15T11:00:00-04:00", endedAt: null, readingCount: 1 },
        mapCenter: { lat: 42.3601, lng: -71.0589 },
        riskLevels: RISK_LEVELS,
        sensors: SENSORS,
        readings: [],
        cells: [],
        hotspots: [],
        alerts: [],
        boat: { online: false, lastHeard: null, gps: null, hardware: null },
        model: { model: false, scoredBy: "rules-v2" },
        ...overrides
    };
}

// ------------------------------------------------------------------ the page

test("without its settings the page says so instead of half-working", () => {
    const { hooks, fetchCalls } = load(undefined, { settings: null });
    hooks.init();
    assert.match(hooks.els.statusLine.textContent, /settings/);
    assert.equal(fetchCalls.length, 0);
});

test("every element the script looks up exists in the page", () => {
    const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
    const script = fs.readFileSync(path.join(WEB, "dashboard.js"), "utf8");
    const block = script.match(/\[\s*("statusLine"[\s\S]*?)\]\.forEach/);
    assert.ok(block, "cacheElements id list not found");
    const ids = [...block[1].matchAll(/"([A-Za-z]+)"/g)].map((match) => match[1]);
    assert.ok(ids.length > 20);
    for (const id of ids) {
        assert.match(html, new RegExp(`id="${id}"`), `index.html has no #${id}`);
    }
});

test("touch screens get 16px inputs so iOS does not zoom on focus", () => {
    const css = fs.readFileSync(path.join(WEB, "style.css"), "utf8");
    const coarse = css.slice(css.indexOf("@media (pointer: coarse)"));
    assert.match(coarse, /font-size:\s*16px/);
    assert.match(coarse, /min-height:\s*44px/);
});

test("the legend and the level filter come from the server's ladder", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard());
    assert.match(hooks.els.legendList.innerHTML, /Low 0–25.*Moderate 26–50.*High 51–75.*Severe 76–100/s);
    assert.match(hooks.els.legendList.innerHTML, /#c85a55/);
    assert.match(hooks.els.levelFilter.innerHTML, /value="Severe"/);
});

// ----------------------------------------------------------------------- API

test("API calls stay on the page's own host and surface the server's message", async () => {
    const { hooks, fetchCalls } = load(async () => response(409, { error: "A mission named 'x' already exists." }));

    await assert.rejects(hooks.api("missions", { method: "POST", body: { name: "x" } }), (error) => {
        assert.equal(error.message, "A mission named 'x' already exists.");
        assert.equal(error.status, 409);
        return true;
    });
    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, "missions");
    assert.equal(fetchCalls[0].options.headers["Content-Type"], "application/json");
});

test("a network failure reads as plain language", async () => {
    const { hooks } = load(async () => { throw new TypeError("Failed to fetch"); });
    await assert.rejects(hooks.api("dashboard"), /Can't reach the server/);
});

test("an error page that is not JSON still gives a useful error", async () => {
    const { hooks } = load(async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError("<html>"); } }));
    await assert.rejects(hooks.api("dashboard"), /answered 502/);
});

// ------------------------------------------------------------------- polling

test("a slow poll cannot overwrite a mission the operator switched to", async () => {
    let releaseLive;
    const responses = [
        new Promise((resolve) => { releaseLive = resolve; }),
        Promise.resolve(response(200, dashboard({ mission: { id: 5, name: "Past", active: false } })))
    ];
    const { hooks, fetchCalls } = load(() => responses.shift());

    hooks.refresh();
    hooks.selectMission(5);
    releaseLive(response(200, dashboard({ mission: { id: 9, name: "Live", active: true }, readings: [reading(1)] })));
    await settle();

    assert.equal(fetchCalls.length, 2, "the switch is queued, not dropped");
    assert.equal(fetchCalls[1].url, "missions/5/dashboard");
    assert.equal(hooks.state.dashboard.mission.id, 5);
    assert.equal(hooks.state.readings.length, 0);
});

test("a failed poll keeps the last good data and says so", async () => {
    const { hooks } = load(async () => { throw new TypeError("offline"); });
    hooks.applyDashboard(dashboard({ readings: [reading(1)], boat: { online: true, lastHeard: "2026-08-15T12:00:01-04:00" } }));

    await hooks.refresh();

    assert.equal(hooks.state.readings.length, 1);
    const status = hooks.statusText();
    assert.equal(status.state, "error");
    assert.match(status.text, /Can't reach the server\. Retrying\. Showing data from/);
});

// -------------------------------------------------------------------- alerts

test("acknowledge all puts the alerts back if the server refuses", async () => {
    const { hooks, alerts } = load(async () => response(500, { error: "disk full" }));
    hooks.applyDashboard(dashboard({ alerts: [{ id: 10, title: "A", level: "High" }, { id: 11, title: "B", level: "Severe" }] }));

    await hooks.acknowledgeAllAlerts();

    assert.equal(hooks.state.dismissed.size, 0, "nothing stays hidden that the server did not acknowledge");
    assert.deepEqual(alerts, ["Could not acknowledge the alerts: disk full"]);
});

test("an acknowledged alert stays hidden until the server stops sending it", async () => {
    const { hooks } = load(async () => response(200, { status: "acknowledged" }));
    hooks.applyDashboard(dashboard({ alerts: [{ id: 10, title: "A", level: "High" }] }));

    await hooks.acknowledgeAlert("10");
    // A poll computed before the acknowledgement landed still lists it.
    hooks.applyDashboard(dashboard({ alerts: [{ id: 10, title: "A", level: "High" }] }));
    assert.equal(hooks.state.dismissed.has("10"), true);

    hooks.applyDashboard(dashboard({ alerts: [] }));
    assert.equal(hooks.state.dismissed.size, 0);
});

test("alerts are paged, and the page stays valid as alerts come and go", () => {
    const { hooks } = load();
    const alerts = [1, 2, 3, 4, 5].map((id) => ({ id, title: `Alert ${id}`, message: "m", level: "High" }));
    hooks.applyDashboard(dashboard({ alerts }));
    const shown = () => (hooks.els.alertList.innerHTML.match(/Alert \d/g) || []).join();
    assert.equal(shown(), "Alert 1,Alert 2");
    assert.equal(hooks.els.alertPager.hidden, false);
    assert.equal(hooks.els.alertPageInfo.textContent, "Page 1 of 3");
    assert.equal(hooks.els.prevAlerts.disabled, true);

    hooks.state.alertPage = 3;
    hooks.applyDashboard(dashboard({ alerts }));
    assert.equal(shown(), "Alert 5");
    assert.equal(hooks.els.nextAlerts.disabled, true);

    // Fewer alerts than the page we were on: back to the last page that exists.
    hooks.applyDashboard(dashboard({ alerts: alerts.slice(0, 3) }));
    assert.equal(hooks.els.alertPageInfo.textContent, "Page 2 of 2");
    hooks.applyDashboard(dashboard({ alerts: alerts.slice(0, 2) }));
    assert.equal(hooks.els.alertPager.hidden, true);
});

test("each alert is drawn in its level's colour", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ alerts: [{ id: 1, title: "Severe bloom risk", message: "m", level: "Severe" }] }));
    assert.match(hooks.els.alertList.innerHTML, /border-left-color:#c85a55/);
});

// --------------------------------------------------------------------- table

test("a third click on a column clears its sort", () => {
    const { hooks } = load();
    const sorted = () => [hooks.state.sortKey, hooks.state.sortDir];
    assert.deepEqual(sorted(), [null, "desc"]);
    hooks.sortBy("turbidity");
    assert.deepEqual(sorted(), ["turbidity", "asc"]);
    hooks.sortBy("turbidity");
    assert.deepEqual(sorted(), ["turbidity", "desc"]);
    hooks.sortBy("turbidity");
    assert.deepEqual(sorted(), [null, "desc"]);
    hooks.sortBy("risk");
    assert.deepEqual(sorted(), ["risk", "desc"]);
    hooks.sortBy("risk");
    assert.deepEqual(sorted(), ["risk", "asc"]);
    hooks.sortBy("risk");
    assert.deepEqual(sorted(), [null, "desc"]);
});

test("missing values sort last in both directions", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({
        readings: [reading(1, { turbidity: 9 }), reading(2, { turbidity: null }), reading(3, { turbidity: 2 })]
    }));

    hooks.state.sortKey = "turbidity";
    hooks.state.sortDir = "asc";
    assert.deepEqual(Array.from(hooks.filteredRows(), (row) => row.id), [3, 1, 2]);
    hooks.state.sortDir = "desc";
    assert.deepEqual(Array.from(hooks.filteredRows(), (row) => row.id), [1, 3, 2]);
});

test("search and the level filter narrow the table", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({
        readings: [reading(1, { risk: 60, level: "High" }), reading(2), reading(3, { lat: null, lng: null })]
    }));

    hooks.state.levelFilter = "High";
    assert.deepEqual(Array.from(hooks.filteredRows(), (row) => row.id), [1]);
    hooks.state.levelFilter = "";
    hooks.state.search = "no gps";
    assert.deepEqual(Array.from(hooks.filteredRows(), (row) => row.id), [3]);
    hooks.state.search = "21.5 °c";
    assert.equal(hooks.filteredRows().length, 3);
});

test("a reading without a fix is listed but not offered as a map link", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ readings: [reading(1, { lat: null, lng: null })] }));
    const html = hooks.rowHtml(hooks.state.readings[0], hooks.tableColumns());

    assert.match(html, /No GPS fix/);
    assert.doesNotMatch(html, /tabindex/);
    assert.doesNotMatch(html, /is-located/);
});

test("table cells are escaped", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ readings: [reading(1, { level: "<img src=x onerror=alert(1)>" })] }));
    const html = hooks.rowHtml(hooks.state.readings[0], hooks.tableColumns());
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
});

test("a sensor column appears once any reading has measured it", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ readings: [reading(1)] }));
    assert.equal(hooks.tableColumns().some((column) => column.key === "nitrate"), false);
    hooks.applyDashboard(dashboard({ readings: [reading(1), reading(2, { nitrate: 1.25 })] }));
    assert.equal(hooks.tableColumns().some((column) => column.key === "nitrate"), true);
});

test("the latest reading lists measured values with their units", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ readings: [reading(1)] }));
    const rows = hooks.readingFacts(hooks.state.readings[0]);
    // Arrays built inside the sandbox have another realm's prototype.
    assert.deepEqual(Array.from(rows[0]), ["Temperature", "21.5 °C"]);
    assert.deepEqual(Array.from(rows[1]), ["pH", "7.50"]);
    assert.equal(rows.some(([label]) => label === "Nitrate"), false);
});

// -------------------------------------------------------------------- replay

test("replay keeps the table on the same timeline as the map", () => {
    const { hooks } = load();
    hooks.applyDashboard(dashboard({ readings: [reading(1), reading(2), reading(3)] }));
    hooks.state.replayIndex = 1;

    assert.deepEqual(Array.from(hooks.visibleReadings(), (row) => row.id), [1, 2]);
    assert.deepEqual(Array.from(hooks.filteredRows(), (row) => row.id).sort(), [1, 2]);
    assert.match(hooks.statusText().text, /^Replay: /);

    hooks.goLive();
    assert.equal(hooks.visibleReadings().length, 3);
});

// -------------------------------------------------------------------- status

test("the status line says what the boat is doing in plain words", () => {
    const { hooks } = load();
    const heard = "2026-08-15T12:00:01-04:00";

    hooks.applyDashboard(dashboard({ readings: [reading(1)], boat: { online: true, lastHeard: heard } }));
    assert.deepEqual({ ...hooks.statusText() }, { state: "live", text: `Boat online, last reading ${hooks.formatTime(reading(1).takenAt)}` });

    hooks.applyDashboard(dashboard({ readings: [reading(1)], boat: { online: false, lastHeard: heard } }));
    assert.deepEqual({ ...hooks.statusText() }, { state: "stale", text: `Boat offline since ${hooks.formatDateTime(heard)}` });

    hooks.applyDashboard(dashboard());
    assert.match(hooks.statusText().text, /Waiting for the boat's first reading/);
});

test("times are formatted, and a bad time is a dash", () => {
    const { hooks } = load();
    assert.match(hooks.formatTime("2026-08-15T12:00:01-04:00"), /\d:\d\d:\d\d/);
    // The browser's own locale and time zone decide the wording.
    assert.match(hooks.formatDateTime("2026-08-15T12:00:01-04:00"), /^\S+ \d+, .*\d:\d\d:\d\d/);
    assert.equal(hooks.formatTime(undefined), "--");
    assert.equal(hooks.formatDateTime("not a time"), "--");
});

// ----------------------------------------------------------------------- map

test("a mission with no fix yet opens on the server's configured area", () => {
    const leaflet = fakeLeaflet();
    const { hooks } = load(undefined, { leaflet });
    hooks.initMap();
    hooks.render();

    hooks.applyDashboard(dashboard({ readings: [reading(1, { lat: null, lng: null })] }));
    assert.deepEqual(leaflet.instance.views.at(-1), [42.3601, -71.0589]);
    assert.equal(leaflet.instance.fits.length, 0);

    // The first real fix fits the map to the readings, once.
    hooks.applyDashboard(dashboard({ readings: [reading(1, { lat: null, lng: null }), reading(2)] }));
    assert.equal(leaflet.instance.fits.length, 1);
    hooks.applyDashboard(dashboard({ readings: [reading(1, { lat: null, lng: null }), reading(2), reading(3)] }));
    assert.equal(leaflet.instance.fits.length, 1, "later readings leave the operator's view alone");
});

test("only located readings get markers, and replay waits for its own cells", () => {
    const leaflet = fakeLeaflet();
    const { hooks } = load(undefined, { leaflet });
    hooks.initMap();
    hooks.applyDashboard(dashboard({
        readings: [reading(1), reading(2, { lat: null, lng: null }), reading(3, { risk: 80, level: "Severe" })],
        cells: [{ id: "1:1", risk: 50, level: "Moderate", readings: 2, bounds: [[42.27, -83.75], [42.29, -83.73]] }],
        hotspots: [{ id: 1, lat: 42.28, lng: -83.74, radius: 80, risk: 80, level: "Severe", readings: 1, revisit: 1, priority: 60, reason: "r" }]
    }));

    assert.equal(hooks.state.layers.readings.items.length, 2);
    assert.equal(hooks.state.layers.cells.items.length, 1);
    assert.equal(hooks.state.layers.cells.items[0].options.color, "#d0a44c");
    assert.equal(hooks.state.layers.hotspots.items[0].options.radius, 80);
    assert.equal(hooks.state.layers.targets.items.length, 1);

    hooks.state.replayIndex = 0;
    hooks.renderMap();
    assert.equal(hooks.state.layers.readings.items.length, 1);
    assert.equal(hooks.state.layers.cells.items.length, 0);
    assert.equal(hooks.state.layers.hotspots.items.length, 0);
});

test("replay draws the cells, hotspots, headline, and alerts as they stood then", async () => {
    const leaflet = fakeLeaflet();
    const then = {
        readingId: 1,
        cells: [{ id: "1:1", risk: 30, level: "Moderate", readings: 1, bounds: [[42.27, -83.75], [42.29, -83.73]] }],
        hotspots: [],
        summary: { risk: 30, level: "Moderate", basis: "cells", counted: 1, peak: 30, distribution: [], alertCells: 0, hotspots: 0 },
        alerts: [{ id: 7, title: "Raised then", message: "m", level: "High", acknowledged: false }]
    };
    const { hooks, fetchCalls } = load(async (url) => response(200, url.includes("/replay/") ? then : {}), { leaflet });
    hooks.initMap();
    hooks.applyDashboard(dashboard({
        readings: [reading(1), reading(2, { risk: 90, level: "Severe", alert: true })],
        cells: [{ id: "1:1", risk: 80, level: "Severe", readings: 2, bounds: [[42.27, -83.75], [42.29, -83.73]] }],
        hotspots: [{ id: 1, lat: 42.28, lng: -83.74, radius: 10, risk: 90, level: "Severe", readings: 1, revisit: 1, priority: 60, reason: "r" }],
        summary: { risk: 80, level: "Severe", basis: "cells", counted: 1, peak: 80, distribution: [], alertCells: 1, hotspots: 1 },
        alerts: [{ id: 8, title: "Raised now", message: "m", level: "Severe", acknowledged: false }]
    }));

    hooks.state.replayIndex = 0;
    hooks.render();
    await settle();

    assert.ok(fetchCalls.some((call) => call.url === "missions/1/replay/1"));
    assert.equal(hooks.state.layers.cells.items[0].options.color, "#d0a44c");
    assert.equal(hooks.state.layers.hotspots.items.length, 0);
    assert.equal(hooks.els.riskNumber.textContent, "30");
    assert.match(hooks.els.alertList.innerHTML, /Raised then/);
    assert.doesNotMatch(hooks.els.alertList.innerHTML, /Raised now|data-acknowledge/, "replayed alerts are read-only");

    hooks.goLive();
    assert.equal(hooks.state.layers.cells.items[0].options.color, "#c85a55");
    assert.equal(hooks.state.layers.hotspots.items.length, 1);
    assert.equal(hooks.els.riskNumber.textContent, "80");
    assert.match(hooks.els.alertList.innerHTML, /Raised now/);
});

test("the mission picker names the running mission", async () => {
    const missions = [
        { id: 2, name: "North pond, morning", active: true },
        { id: 1, name: "First run", active: false }
    ];
    const { hooks, elements } = load(async (url) => response(200, url === "missions" ? missions : dashboard()));
    hooks.init();
    await settle();
    const options = hooks.els.missionSelect.innerHTML;
    assert.match(options, /<option value="">North pond, morning<\/option>/);
    assert.match(options, /<option value="1">First run<\/option>/);
    assert.doesNotMatch(options, /Current mission/);
});

test("the grid is 5 m close in, coarser further out, and gone across a whole country", () => {
    const leaflet = fakeLeaflet();
    const { hooks } = load(undefined, { leaflet });
    hooks.initMap();
    const metersPerDegreeLongitude = 111320 * Math.cos(42.28 * Math.PI / 180);
    const showWidth = (meters) => {
        const halfWidth = meters / metersPerDegreeLongitude / 2, halfHeight = meters / 4 / 111320;
        leaflet.instance.view = { south: 42.28 - halfHeight, north: 42.28 + halfHeight, west: -83.74 - halfWidth, east: -83.74 + halfWidth, widthPixels: 800 };
        hooks.applyDashboard(dashboard({ grid: { steps: [5, 15, 25, 75, 150, 375, 750, 1500, 3750, 7500], cellSize: 75, latitude: 42.28 } }));
        hooks.drawGrid();
        return hooks.state.gridLayer.items;
    };

    // 100 m across 800 px: 5 m lines are 40 px apart. Lines on a cell's edge are stronger.
    const close = showWidth(100);
    assert.equal(hooks.els.gridLabel.textContent, "Grid 5 m");
    assert.equal(hooks.els.gridLabel.hidden, false);
    assert.ok(close.length > 20 && close.length < 40, `${close.length} lines`);
    assert.ok(close.every((line) => line.kind === "polyline" && line.options.pane === "grid" && line.options.interactive === false));
    assert.ok(close.some((line) => line.options.className.includes("cell-edge")));
    assert.ok(close.filter((line) => line.options.className.includes("cell-edge")).length < close.length / 4);

    // 2.5 km across: 75 m lines (24 px apart), on the cells' own edges.
    showWidth(2500);
    assert.equal(hooks.els.gridLabel.textContent, "Grid 75 m");

    // 1000 km across: no spacing is readable, so no grid at all.
    assert.equal(showWidth(1000000).length, 0);
    assert.equal(hooks.els.gridLabel.hidden, true);
});
