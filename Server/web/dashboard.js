// The dashboard: polls GET /dashboard and draws it. The server sends plain
// values plus the sensor list and the risk ladder (Server/config.py); this
// file formats and draws them, and defines neither.
(() => {
    "use strict";

    // Every setting (timings, page sizes, zoom levels, sizes on the water)
    // comes from Server/config.py, through /config.js.
    const SETTINGS = window.BLOOM_SETTINGS;

    // How things are drawn, in screen pixels and opacities. (The risk colours
    // come from the server, and the page's look from style.css.)
    const STYLE = {
        cell: { weight: 1.5, opacity: 0.9, fillOpacity: 0.25, popupWidth: 320 },
        hotspot: { weight: 2, dashArray: "6 6", fillOpacity: 0.08 },
        target: { size: 26, popupWidth: 300 },
        shading: { fillOpacity: 0.2 },
        track: { color: "#3c4650", weight: 3, opacity: 0.75 },
        boat: { size: 18 },
        reading: {
            radius: 6, radiusTouch: 8, weight: 1.5, outline: "#ffffff", fillOpacity: 1,
            selectedRadius: 9, selectedRadiusTouch: 11, selectedWeight: 3, selectedOutline: "#111111",
            popupWidth: 300
        },
        grid: { weight: 1 },
        fitPaddingPixels: 36,
        flySeconds: 0.6,
        zoomSnap: 0.5 // half-step zoom lets "fit to mission" fill a narrow phone map
    };

    const NEUTRAL_COLOR = "#8a8f96";
    const PHONE_MEDIA = "(max-width: 700px)"; // the phone layout's width in style.css
    const WORLD_VIEW = { center: [20, 0], zoom: 2 }; // until the server says where the mission is

    // Facts, as in Server/analysis.py, so grid lines meet the cells' edges.
    const METERS_PER_DEGREE_LATITUDE = 111320;
    const MAX_GRID_LATITUDE = 85;

    const GPS_LABELS = {
        fix: "Fix",
        stale: "Last known position",
        simulated: "Simulated",
        no_fix: "No fix",
        unavailable: "No GPS"
    };
    const HARDWARE_LABELS = {
        ok: "All probes answering",
        partial: "Some probes not answering",
        simulated: "Simulated readings",
        unknown: "Unknown"
    };

    // Map panes, bottom to top. Only cells, readings, and targets take clicks.
    const GRID_PANE = ["grid", 350, "none"];
    const PANES = [
        ["shading", 380, "none"],
        ["cells", 400, "auto"],
        ["track", 410, "none"],
        ["hotspots", 420, "none"],
        ["readings", 440, "auto"],
        ["targets", 450, "auto"]
    ];

    const state = {
        dashboard: null,
        readings: [],
        missions: [],
        viewingMissionId: null,
        activeMissionId: undefined,
        lastSuccessAt: null,
        lastError: null,
        loading: false,
        reloadQueued: false,
        requestSeq: 0,
        pollTimer: null,
        shutDown: false,
        replayIndex: null,
        replayTimer: null,
        replayView: null, // the server's cells, hotspots, headline, and alerts at the replayed reading
        replayWanted: null,
        replayLoading: false,
        selectedId: null,
        sortKey: null, // null: no column chosen, so newest first
        sortDir: "desc",
        search: "",
        levelFilter: "",
        page: 1,
        alertPage: 1,
        dismissed: new Set(),
        busy: false,
        map: null,
        layers: {},
        gridLayer: null,
        markers: new Map(),
        popupSources: new Map(),
        openPopupKey: null,
        redrawing: false,
        mapSignature: null,
        mapFitted: false,
        mapCentered: false,
        missionSignature: null,
        levelsSignature: null
    };

    const els = {};

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        cacheElements();
        if (!SETTINGS) {
            // config.js comes from the same server as this page.
            els.statusLine.textContent = "Could not load the dashboard's settings. Reload the page.";
            return;
        }
        bindEvents();
        initMap();
        render();
        refresh();
        loadMissions();
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) refresh();
        });
        // Polling pauses in a background tab; a phone in a pocket should not
        // keep pulling the whole mission every few seconds.
        state.pollTimer = window.setInterval(() => {
            if (!document.hidden) refresh();
        }, SETTINGS.pollSeconds * 1000);
    }

    function cacheElements() {
        [
            "statusLine", "missionSelect", "newMissionButton", "endMissionButton", "reviewBanner",
            "backToCurrentButton", "map", "mapUnavailable", "layersPanel", "legendList", "fitButton", "gridLabel", "playButton",
            "timeline", "replayTime", "liveButton", "riskNumber", "riskBadge", "riskBasis", "distribution",
            "riskFacts", "boatFacts", "latestReading", "alertCount", "acknowledgeAllButton", "alertList",
            "alertPager", "prevAlerts", "alertPageInfo", "nextAlerts",
            "readingCount", "searchInput", "levelFilter", "exportButton", "readingsHeaderRow", "readingsBody",
            "readingsEmpty", "prevPage", "pageInfo", "nextPage", "modelInfo", "shutdownButton"
        ].forEach((id) => {
            els[id] = document.getElementById(id);
        });
        els.layerToggles = Array.from(document.querySelectorAll("[data-layer]"));
    }

    function bindEvents() {
        els.missionSelect.addEventListener("change", () => {
            const value = els.missionSelect.value;
            selectMission(value === "" ? null : Number(value));
        });
        els.backToCurrentButton.addEventListener("click", () => selectMission(null));
        els.newMissionButton.addEventListener("click", startNewMission);
        els.endMissionButton.addEventListener("click", endMission);
        els.shutdownButton.addEventListener("click", shutDownServer);
        els.fitButton.addEventListener("click", () => fitToMission(true));
        els.layerToggles.forEach((toggle) => toggle.addEventListener("change", renderMap));

        els.playButton.addEventListener("click", toggleReplay);
        els.liveButton.addEventListener("click", goLive);
        els.timeline.addEventListener("input", () => {
            stopReplayTimer();
            state.replayIndex = Number(els.timeline.value);
            render();
        });

        els.acknowledgeAllButton.addEventListener("click", acknowledgeAllAlerts);
        els.alertList.addEventListener("click", (event) => {
            const button = event.target.closest("[data-acknowledge]");
            if (button) acknowledgeAlert(button.dataset.acknowledge);
        });

        els.prevAlerts.addEventListener("click", () => {
            state.alertPage -= 1;
            renderAlerts();
        });
        els.nextAlerts.addEventListener("click", () => {
            state.alertPage += 1;
            renderAlerts();
        });

        els.searchInput.addEventListener("input", () => {
            state.search = els.searchInput.value.trim().toLowerCase();
            state.page = 1;
            renderTable();
        });
        els.levelFilter.addEventListener("change", () => {
            state.levelFilter = els.levelFilter.value;
            state.page = 1;
            renderTable();
        });
        els.readingsHeaderRow.addEventListener("click", (event) => {
            const button = event.target.closest("[data-sort]");
            if (button) sortBy(button.dataset.sort);
        });
        els.readingsBody.addEventListener("click", (event) => {
            const row = event.target.closest("tr[data-id]");
            if (row) selectReading(Number(row.dataset.id), { focusMap: true });
        });
        els.readingsBody.addEventListener("keydown", (event) => {
            const row = event.target.closest("tr[data-id]");
            if (row && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                selectReading(Number(row.dataset.id), { focusMap: true });
            }
        });
        els.prevPage.addEventListener("click", () => {
            state.page -= 1;
            renderTable();
        });
        els.nextPage.addEventListener("click", () => {
            state.page += 1;
            renderTable();
        });
        els.exportButton.addEventListener("click", exportCsv);
    }

    // ------------------------------------------------------------------ API

    async function api(path, { method = "GET", body, timeoutMs = SETTINGS.timeoutSeconds * 1000 } = {}) {
        // Always the host that served this page.
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
            let response;
            try {
                response = await fetch(path, {
                    method,
                    cache: "no-store",
                    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
                    body: body === undefined ? undefined : JSON.stringify(body),
                    signal: controller.signal
                });
            } catch (error) {
                throw new Error(error?.name === "AbortError"
                    ? "The server took too long to answer."
                    : "Can't reach the server.");
            }
            const payload = await response.json().catch(() => null);
            if (!response.ok) {
                const error = new Error(payload?.error || `The server answered ${response.status}.`);
                error.status = response.status;
                throw error;
            }
            return payload;
        } finally {
            window.clearTimeout(timer);
        }
    }

    async function refresh() {
        if (state.shutDown) return;
        if (state.loading) {
            // Never two at once, but never drop one either: a mission switch
            // during a slow poll must still load.
            state.reloadQueued = true;
            return;
        }

        state.loading = true;
        const seq = ++state.requestSeq;
        const missionId = state.viewingMissionId;

        try {
            const data = await api(missionId === null ? "dashboard" : `missions/${missionId}/dashboard`);
            // A response for a view the operator has already left is dropped.
            if (seq === state.requestSeq && missionId === state.viewingMissionId) {
                applyDashboard(data);
            }
        } catch (error) {
            if (seq === state.requestSeq && missionId === state.viewingMissionId) {
                state.lastError = error.message;
                if (error.status === 404 && missionId !== null) {
                    state.lastError = null;
                    selectMission(null);
                }
            }
        } finally {
            state.loading = false;
            renderStatus();
            if (state.reloadQueued) {
                state.reloadQueued = false;
                refresh();
            }
        }
    }

    function applyDashboard(data) {
        const payload = data && typeof data === "object" ? data : {};
        state.dashboard = payload;
        state.readings = Array.isArray(payload.readings) ? payload.readings : [];
        payload.cellsById = new Map((payload.cells || []).map((cell) => [cell.id, cell]));
        state.lastSuccessAt = new Date();
        state.lastError = null;

        if (state.viewingMissionId === null) {
            const activeId = payload.mission?.id ?? null;
            if (activeId !== state.activeMissionId) {
                const firstLoad = state.activeMissionId === undefined;
                state.activeMissionId = activeId;
                if (!firstLoad) {
                    // A new mission was started (here or from another device).
                    resetViewState();
                    loadMissions();
                }
            }
            // Forget locally acknowledged alerts once the server stops sending them.
            const serverIds = new Set((payload.alerts || []).map((alert) => String(alert.id)));
            state.dismissed.forEach((id) => {
                if (!serverIds.has(id)) state.dismissed.delete(id);
            });
        }

        if (state.replayIndex !== null) {
            state.replayIndex = Math.min(state.replayIndex, Math.max(0, state.readings.length - 1));
        }
        if (state.selectedId !== null && !state.readings.some((reading) => reading.id === state.selectedId)) {
            state.selectedId = null;
        }
        render();
    }

    async function loadMissions() {
        try {
            const missions = await api("missions");
            state.missions = Array.isArray(missions) ? missions : [];
        } catch (error) {
            state.missions = [];
        }
        renderMissionControls();
    }

    // ------------------------------------------------------------- missions

    function selectMission(missionId) {
        if (missionId === state.viewingMissionId) return;
        state.viewingMissionId = missionId;
        state.requestSeq += 1;
        state.dashboard = null;
        state.readings = [];
        resetViewState();
        render();
        refresh();
    }

    function resetViewState() {
        stopReplayTimer();
        state.replayIndex = null;
        state.replayView = null;
        state.selectedId = null;
        state.page = 1;
        state.mapFitted = false;
        state.mapCentered = false;
        state.mapSignature = null;
        state.openPopupKey = null;
        state.dismissed.clear();
    }

    async function startNewMission() {
        const current = state.dashboard?.mission;
        const ending = current?.active ? ` "${current.name}" will end.` : "";
        const name = window.prompt(`Name for the new mission (leave blank to use the date and time).${ending}`, "");
        if (name === null) return;

        await runAction(async () => {
            await api("missions", { method: "POST", body: { name: name.trim() } });
            state.activeMissionId = undefined;
            await loadMissions();
            if (state.viewingMissionId === null) {
                resetViewState();
                state.requestSeq += 1;
                refresh();
            } else {
                selectMission(null);
            }
        }, "Could not start a new mission");
    }

    async function endMission() {
        const mission = state.dashboard?.mission;
        if (state.viewingMissionId !== null || !mission?.active) return;
        const confirmed = window.confirm(
            `End "${mission.name}"? The boat's next reading will start a new mission automatically.`
        );
        if (!confirmed) return;

        await runAction(async () => {
            await api("missions/end", { method: "POST" });
            await loadMissions();
            refresh();
        }, "Could not end the mission");
    }

    async function shutDownServer() {
        const confirmed = window.confirm(
            "Shut down the server? The boat keeps its readings until the server is started again."
        );
        if (!confirmed) return;

        await runAction(async () => {
            await api("shutdown", { method: "POST" });
            state.shutDown = true;
            window.clearInterval(state.pollTimer);
            stopReplayTimer();
            render();
        }, "Could not shut down the server");
    }

    async function runAction(action, failureMessage) {
        if (state.busy) return;
        state.busy = true;
        renderMissionControls();
        try {
            await action();
        } catch (error) {
            window.alert(`${failureMessage}: ${error.message}`);
        } finally {
            state.busy = false;
            renderMissionControls();
        }
    }

    // --------------------------------------------------------------- alerts

    function visibleAlerts() {
        const alerts = Array.isArray(missionView()?.alerts) ? missionView().alerts : [];
        if (state.viewingMissionId !== null || state.replayIndex !== null) return alerts;
        return alerts.filter((alert) => !state.dismissed.has(String(alert.id)));
    }

    async function acknowledgeAlert(alertId) {
        const id = String(alertId);
        state.dismissed.add(id);
        renderAlerts();
        try {
            await api(`alerts/${encodeURIComponent(id)}/acknowledge`, { method: "POST" });
        } catch (error) {
            // A 404 means it is already gone; anything else, put it back.
            if (error.status !== 404) {
                state.dismissed.delete(id);
                renderAlerts();
                window.alert(`Could not acknowledge the alert: ${error.message}`);
            }
        }
    }

    async function acknowledgeAllAlerts() {
        const ids = visibleAlerts().filter((alert) => !alert.acknowledged).map((alert) => String(alert.id));
        if (!ids.length) return;

        ids.forEach((id) => state.dismissed.add(id));
        renderAlerts();
        try {
            await api("alerts/acknowledge", { method: "POST" });
        } catch (error) {
            // Nothing stays hidden that the server did not acknowledge.
            ids.forEach((id) => state.dismissed.delete(id));
            renderAlerts();
            window.alert(`Could not acknowledge the alerts: ${error.message}`);
        }
    }

    // --------------------------------------------------------------- replay

    function visibleReadings() {
        if (state.replayIndex === null) return state.readings;
        return state.readings.slice(0, state.replayIndex + 1);
    }

    function currentReading() {
        const visible = visibleReadings();
        return visible[visible.length - 1] || null;
    }

    // What the mission-wide parts (cells, hotspots, revisit targets, the
    // headline, alerts) show: live, the dashboard; in replay, the mission as
    // it stood at the replayed reading, worked out by the server exactly as
    // the live view is. Null until the first replay answer arrives.
    function missionView() {
        return state.replayIndex === null ? state.dashboard : state.replayView;
    }

    // Asks for the replayed reading's view whenever replay moves on. One
    // request at a time; if replay has moved again by the time it returns,
    // the newest position is asked for next, so quick steps skip ahead.
    function syncReplayView() {
        const reading = state.replayIndex === null ? null : state.readings[state.replayIndex];
        const missionId = state.dashboard?.mission?.id;
        if (!reading || missionId == null) {
            state.replayWanted = null;
            if (state.replayIndex === null) state.replayView = null;
            return;
        }
        state.replayWanted = `missions/${missionId}/replay/${reading.id}`;
        if (state.replayLoading || state.replayView?.path === state.replayWanted) return;
        loadReplayView(state.replayWanted);
    }

    async function loadReplayView(path) {
        state.replayLoading = true;
        try {
            const view = await api(path);
            if (state.replayWanted === path) {
                view.path = path;
                view.cellsById = new Map((view.cells || []).map((cell) => [cell.id, cell]));
                state.replayView = view;
            }
        } catch (error) {
            // Keep the last view; the live poll reports a lost connection.
        } finally {
            state.replayLoading = false;
        }
        if (state.replayWanted && state.replayWanted !== path) {
            loadReplayView(state.replayWanted);
        } else if (state.replayWanted === path) {
            render();
        }
    }

    function toggleReplay() {
        if (state.replayTimer) {
            stopReplayTimer();
            renderReplay();
            return;
        }
        if (state.readings.length < 2) return;
        const last = state.readings.length - 1;
        if (state.replayIndex === null || state.replayIndex >= last) state.replayIndex = 0;
        state.replayTimer = window.setInterval(() => {
            if (state.replayIndex >= state.readings.length - 1) {
                stopReplayTimer();
            } else {
                state.replayIndex += 1;
            }
            render();
        }, SETTINGS.replayStepSeconds * 1000);
        render();
    }

    function stopReplayTimer() {
        if (state.replayTimer) {
            window.clearInterval(state.replayTimer);
            state.replayTimer = null;
        }
    }

    function goLive() {
        stopReplayTimer();
        state.replayIndex = null;
        state.replayView = null;
        render();
    }

    // ------------------------------------------------------------ rendering

    function render() {
        syncReplayView();
        renderStatus();
        renderMissionControls();
        renderLevels();
        renderRisk();
        renderBoat();
        renderAlerts();
        renderReplay();
        renderTable();
        renderMap();
        renderFooter();
    }

    function statusText() {
        if (state.shutDown) return { state: "error", text: "The server has been shut down." };

        const data = state.dashboard;
        if (state.lastError) {
            const since = state.lastSuccessAt ? ` Showing data from ${formatClock(state.lastSuccessAt)}.` : "";
            return { state: "error", text: `${state.lastError} Retrying.${since}` };
        }
        if (!data) return { state: "idle", text: "Loading…" };

        if (state.viewingMissionId !== null) {
            const mission = data.mission;
            return {
                state: "idle",
                text: mission ? `Past mission: ${mission.name} (${formatDuration(mission.startedAt, mission.endedAt)})` : "Past mission"
            };
        }
        if (state.replayIndex !== null) {
            return { state: "idle", text: `Replay: ${formatDateTime(currentReading()?.takenAt)}` };
        }

        const boat = data.boat || {};
        if (!boat.lastHeard) return { state: "idle", text: "Waiting for the boat's first reading." };
        if (boat.online) {
            return { state: "live", text: `Boat online, last reading ${formatTime(currentReading()?.takenAt || boat.lastHeard)}` };
        }
        return { state: "stale", text: `Boat offline since ${formatDateTime(boat.lastHeard)}` };
    }

    function renderStatus() {
        const { state: tone, text } = statusText();
        els.statusLine.dataset.state = tone;
        els.statusLine.textContent = text;
    }

    function renderMissionControls() {
        const reviewing = state.viewingMissionId !== null;
        const mission = state.dashboard?.mission;
        els.reviewBanner.hidden = !reviewing;
        els.newMissionButton.disabled = state.busy || state.shutDown;
        els.endMissionButton.disabled = state.busy || state.shutDown || reviewing || !mission?.active;
        els.shutdownButton.disabled = state.busy || state.shutDown;

        // Rebuilding the options while the list is open closes it under the
        // operator's finger, so leave a focused select alone.
        if (document.activeElement === els.missionSelect) return;
        const past = state.missions.filter((item) => !item.active);
        // The live option is named after the mission that is running.
        const active = state.missions.find((item) => item.active);
        const live = active ? active.name : "No mission running";
        const signature = JSON.stringify([state.viewingMissionId, live, past.map((item) => [item.id, item.name])]);
        if (signature === state.missionSignature) return;
        state.missionSignature = signature;

        const options = [`<option value="">${escapeHtml(live)}</option>`].concat(
            past.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`)
        );
        if (reviewing && !past.some((item) => item.id === state.viewingMissionId)) {
            options.push(`<option value="${escapeHtml(state.viewingMissionId)}">Mission ${escapeHtml(state.viewingMissionId)}</option>`);
        }
        els.missionSelect.innerHTML = options.join("");
        els.missionSelect.value = reviewing ? String(state.viewingMissionId) : "";
    }

    // The legend and the level filter both come from the server's ladder.
    function renderLevels() {
        const levels = riskLevels();
        const signature = JSON.stringify(levels);
        if (signature === state.levelsSignature) return;
        state.levelsSignature = signature;

        els.legendList.innerHTML = levels.map((level, index) => {
            const top = index + 1 < levels.length ? levels[index + 1].min - 1 : 100;
            return `<li><span class="swatch" style="background:${safeColor(level.color)}"></span>${escapeHtml(level.label)} ${level.min}–${top}</li>`;
        }).join("");
        els.levelFilter.innerHTML = [`<option value="">All risk levels</option>`]
            .concat(levels.map((level) => `<option value="${escapeHtml(level.label)}">${escapeHtml(level.label)}</option>`))
            .join("");
        els.levelFilter.value = state.levelFilter;
    }

    function renderRisk() {
        const summary = missionView()?.summary;
        const hasData = summary && summary.basis !== "none";

        els.riskNumber.textContent = hasData ? String(summary.risk) : "--";
        els.riskNumber.className = `risk-number ${hasData ? levelClass(summary.level) : ""}`;
        els.riskBadge.textContent = hasData ? `${summary.level} risk` : "No data";
        els.riskBadge.className = `badge ${hasData ? levelClass(summary.level) : ""}`;
        els.riskBasis.textContent = riskBasisText(summary);

        const distribution = hasData ? summary.distribution || [] : [];
        const total = distribution.reduce((sum, item) => sum + (Number(item.count) || 0), 0);
        els.distribution.innerHTML = total
            ? `<div class="distribution-bar">${distribution.map((item) => (
                item.count ? `<span style="width:${(item.count / total) * 100}%;background:${levelColor(item.label)}" title="${escapeHtml(item.label)}: ${item.count}"></span>` : ""
            )).join("")}</div>
            <ul class="distribution-key">${distribution.map((item) => (
                `<li><span class="swatch" style="background:${levelColor(item.label)}"></span>${escapeHtml(item.label)} ${Number(item.count) || 0}</li>`
            )).join("")}</ul>`
            : "";

        const rows = [];
        if (hasData) {
            const latest = currentReading();
            if (latest) rows.push(["Latest reading", `${latest.risk} (${latest.level})`]);
            if (summary.basis === "cells") {
                rows.push(["Peak cell", `${summary.peak}/100`]);
                rows.push(["Cells at alert level", String(summary.alertCells)]);
            } else {
                rows.push(["Peak reading", `${summary.peak}/100`]);
            }
            rows.push(["Hotspots", String(summary.hotspots)]);
        }
        setFacts(els.riskFacts, rows);
    }

    function riskBasisText(summary) {
        if (!summary || summary.basis === "none") return "No readings yet.";
        const count = summary.counted;
        if (summary.basis === "cells") return `Roll-up of ${count} map cell${count === 1 ? "" : "s"}`;
        return `From ${count} reading${count === 1 ? "" : "s"} without a GPS fix`;
    }

    function renderBoat() {
        const data = state.dashboard;
        const boat = data?.boat;
        const reviewing = state.viewingMissionId !== null;
        const rows = [];
        if (boat) {
            if (!reviewing) {
                rows.push(boat.lastHeard
                    ? ["Status", boat.online ? "Online" : "Offline", boat.online ? "good" : "warn"]
                    : ["Status", "No readings yet"]);
            }
            rows.push(["Last heard", boat.lastHeard ? formatDateTime(boat.lastHeard) : "--"]);
            rows.push(["GPS", GPS_LABELS[boat.gps] || "--", !boat.gps || boat.gps === "fix" ? "" : "warn"]);
            rows.push(["Probes", HARDWARE_LABELS[boat.hardware] || "--", !boat.hardware || boat.hardware === "ok" ? "" : "warn"]);
            rows.push(["Readings", String(data.mission?.readingCount ?? 0)]);
        }
        setFacts(els.boatFacts, rows);

        const reading = currentReading();
        setFacts(els.latestReading, reading ? readingFacts(reading) : [["Readings", "None yet"]]);
    }

    function readingFacts(reading) {
        return measuredSensors().map((sensor) => [sensor.label, formatValue(reading[sensor.key], sensor)]);
    }

    function renderAlerts() {
        const reviewing = state.viewingMissionId !== null;
        const replaying = state.replayIndex !== null;
        const readOnly = reviewing || replaying;
        const alerts = visibleAlerts();
        const open = alerts.filter((alert) => !alert.acknowledged);
        els.alertCount.textContent = open.length ? `(${open.length})` : "";
        els.acknowledgeAllButton.hidden = readOnly || open.length < 2;

        const pages = Math.max(1, Math.ceil(alerts.length / SETTINGS.alertPageSize));
        state.alertPage = Math.min(pages, Math.max(1, state.alertPage));
        els.alertPager.hidden = pages < 2;
        els.alertPageInfo.textContent = `Page ${state.alertPage} of ${pages}`;
        els.prevAlerts.disabled = state.alertPage <= 1;
        els.nextAlerts.disabled = state.alertPage >= pages;

        if (!alerts.length) {
            const message = replaying
                ? "No alerts had been raised by this point."
                : reviewing ? "No alerts were raised during this mission." : "No open alerts.";
            els.alertList.innerHTML = `<li class="empty">${message}</li>`;
            return;
        }

        const start = (state.alertPage - 1) * SETTINGS.alertPageSize;
        els.alertList.innerHTML = alerts.slice(start, start + SETTINGS.alertPageSize).map((alert) => {
            const action = alert.acknowledged
                ? `<span class="alert-time">Acknowledged</span>`
                : readOnly
                    ? ""
                    : `<button type="button" class="small" data-acknowledge="${escapeHtml(alert.id)}">Acknowledge</button>`;
            return `
                <li class="${alert.acknowledged ? "is-acknowledged" : ""}" style="border-left-color:${levelColor(alert.level)}">
                    <span class="alert-title">${escapeHtml(alert.title)}</span>
                    <span class="alert-message">${escapeHtml(alert.message)}</span>
                    <span class="alert-time">${escapeHtml(formatDateTime(alert.raisedAt))}</span>
                    ${action}
                </li>`;
        }).join("");
    }

    function renderReplay() {
        const count = state.readings.length;
        const max = Math.max(0, count - 1);
        const replaying = state.replayIndex !== null;
        els.timeline.max = String(max);
        els.timeline.value = String(replaying ? state.replayIndex : max);
        els.timeline.disabled = count < 2;
        els.playButton.disabled = count < 2;
        els.playButton.textContent = state.replayTimer ? "Pause" : replaying ? "Resume" : "Replay";
        els.liveButton.hidden = !replaying;
        els.replayTime.textContent = replaying
            ? formatTime(currentReading()?.takenAt)
            : count ? "Live" : "--";
    }

    function renderFooter() {
        const model = state.dashboard?.model;
        els.modelInfo.textContent = model
            ? `Scored by ${model.scoredBy}${model.model ? "" : " (the built-in rules, not a trained model)"}`
            : "";
    }

    function setFacts(element, rows) {
        element.replaceChildren(...rows.map(([label, value, tone]) => {
            const row = document.createElement("div");
            const term = document.createElement("dt");
            const detail = document.createElement("dd");
            term.textContent = label;
            detail.textContent = value;
            if (tone) detail.className = tone;
            row.append(term, detail);
            return row;
        }));
    }

    // ---------------------------------------------------------------- table

    function tableColumns() {
        return [
            { key: "takenAt", label: "Time", col: "time" },
            ...measuredSensors().map((sensor) => ({ ...sensor, col: sensor.key, numeric: true })),
            { key: "risk", label: "Risk", col: "risk", numeric: true },
            { key: "confidence", label: "Confidence", unit: "%", col: "confidence", numeric: true, digits: 0 },
            { key: "position", label: "Position", col: "position" }
        ];
    }

    function filteredRows() {
        const term = state.search;
        return visibleReadings()
            .filter((reading) => {
                if (state.levelFilter && reading.level !== state.levelFilter) return false;
                if (!term) return true;
                const haystack = [
                    formatDateTime(reading.takenAt),
                    formatPosition(reading),
                    `${reading.level} risk`,
                    reading.risk,
                    ...sensors().map((sensor) => formatValue(reading[sensor.key], sensor))
                ].join(" ").toLowerCase();
                return haystack.includes(term);
            })
            .sort(compareRows);
    }

    function compareRows(a, b) {
        const key = state.sortKey || "takenAt";
        const left = sortValue(a, key);
        const right = sortValue(b, key);
        // Missing values always sort last, whichever way the column is sorted.
        if (left === null && right === null) return 0;
        if (left === null) return 1;
        if (right === null) return -1;
        const direction = state.sortDir === "asc" ? 1 : -1;
        if (left < right) return -direction;
        if (left > right) return direction;
        return 0;
    }

    function sortValue(reading, key) {
        if (key === "takenAt") {
            const time = Date.parse(reading.takenAt);
            return Number.isFinite(time) ? time : null;
        }
        if (key === "position") return isNumber(reading.lat) ? Number(reading.lat) : null;
        return isNumber(reading[key]) ? Number(reading[key]) : null;
    }

    // A column's first click sorts it, the second reverses it, and the third
    // clears the sort (back to newest first).
    function sortBy(key) {
        const first = key === "takenAt" || key === "risk" ? "desc" : "asc";
        if (state.sortKey !== key) {
            state.sortKey = key;
            state.sortDir = first;
        } else if (state.sortDir === first) {
            state.sortDir = first === "asc" ? "desc" : "asc";
        } else {
            state.sortKey = null;
            state.sortDir = "desc";
        }
        state.page = 1;
        renderTable();
    }

    function renderTable() {
        const columns = tableColumns();
        const headerSignature = columns.map((column) => column.key).join(",") + state.sortKey + state.sortDir;
        if (els.readingsHeaderRow.dataset.signature !== headerSignature) {
            els.readingsHeaderRow.dataset.signature = headerSignature;
            els.readingsHeaderRow.innerHTML = columns.map((column) => {
                const sort = column.key === state.sortKey ? (state.sortDir === "asc" ? "ascending" : "descending") : "none";
                const unit = column.unit ? ` <span class="unit">${escapeHtml(column.unit)}</span>` : "";
                return `<th scope="col" class="${column.numeric ? "num" : ""}" aria-sort="${sort}"><button type="button" data-sort="${column.key}">${escapeHtml(column.label)}${unit}</button></th>`;
            }).join("");
        }

        const rows = filteredRows();
        const size = pageSize();
        const pages = Math.max(1, Math.ceil(rows.length / size));
        state.page = Math.min(pages, Math.max(1, state.page));
        const start = (state.page - 1) * size;
        const pageRows = rows.slice(start, start + size);
        const showDate = spansSeveralDays();

        els.readingsBody.innerHTML = pageRows.map((reading) => rowHtml(reading, columns, showDate)).join("");
        const loaded = state.readings.length;
        els.readingsEmpty.hidden = pageRows.length > 0;
        els.readingsEmpty.textContent = loaded
            ? "No readings match the search or filter."
            : "No readings yet. They appear here as the boat sends them.";
        const total = state.dashboard?.mission?.readingCount ?? loaded;
        els.readingCount.textContent = loaded ? `(${total})` : "";
        els.pageInfo.textContent = rows.length
            ? `${start + 1}–${start + pageRows.length} of ${rows.length}${total > loaded ? ` (newest ${loaded} loaded)` : ""}`
            : "";
        els.prevPage.disabled = state.page <= 1;
        els.nextPage.disabled = state.page >= pages;
        els.exportButton.disabled = !state.dashboard?.mission?.id || loaded === 0;
    }

    function pageSize() {
        return window.matchMedia?.(PHONE_MEDIA).matches ? SETTINGS.pageSizePhone : SETTINGS.pageSize;
    }

    // The time alone is enough while everything in view is from one day; the
    // popup and the CSV always carry the full date.
    function spansSeveralDays() {
        const days = new Set(state.readings.map((reading) => String(reading.takenAt).slice(0, 10)));
        return days.size > 1;
    }

    function rowHtml(reading, columns, showDate = true) {
        const located = isLocated(reading);
        const classes = [
            located ? "is-located" : "",
            reading.id === state.selectedId ? "is-selected" : ""
        ].filter(Boolean).join(" ");
        const cells = columns.map((column) => {
            let content;
            if (column.key === "takenAt") {
                const text = showDate ? formatDateTime(reading.takenAt) : formatTime(reading.takenAt);
                content = `<span title="${escapeHtml(formatDateTime(reading.takenAt))}">${escapeHtml(text)}</span>`;
            } else if (column.key === "risk") {
                content = `<span class="badge ${levelClass(reading.level)}">${escapeHtml(reading.risk)} ${escapeHtml(reading.level)}</span>`;
            } else if (column.key === "position") {
                content = located ? escapeHtml(formatPosition(reading)) : `<span class="missing">No GPS fix</span>`;
            } else {
                const value = reading[column.key];
                // The unit rides along for the phone card layout, where there
                // is no column header to carry it; the table hides it.
                const unit = column.unit
                    ? `<span class="card-unit">${column.unit === "%" ? "" : " "}${escapeHtml(column.unit)}</span>`
                    : "";
                content = isNumber(value) ? `${escapeHtml(Number(value).toFixed(column.digits ?? 1))}${unit}` : `<span class="missing">--</span>`;
            }
            return `<td data-col="${column.col}" data-label="${escapeHtml(column.label)}"${column.numeric ? ` class="num"` : ""}>${content}</td>`;
        }).join("");
        const interactive = located ? ` tabindex="0" aria-label="Show the reading from ${escapeHtml(formatTime(reading.takenAt))} on the map"` : "";
        return `<tr data-id="${escapeHtml(reading.id)}" class="${classes}"${interactive}>${cells}</tr>`;
    }

    function exportCsv() {
        // The server streams every reading in the mission, not just the
        // newest ones loaded here.
        const missionId = state.dashboard?.mission?.id;
        if (!missionId) return;
        const link = document.createElement("a");
        link.href = `missions/${encodeURIComponent(missionId)}/readings.csv`;
        link.download = "";
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    // ------------------------------------------------------------------ map

    function initMap() {
        if (!window.L) {
            els.map.hidden = true;
            els.mapUnavailable.hidden = false;
            els.layersPanel.hidden = true;
            els.fitButton.hidden = true;
            return;
        }

        // No street map underneath: the dashboard must work on the Pi's own
        // network, with no internet, so nothing is ever downloaded. The
        // mission's own layers are drawn over a metre grid instead, with a
        // scale bar for distances.
        state.map = L.map(els.map, {
            zoomControl: false,
            zoomSnap: STYLE.zoomSnap,
            maxZoom: SETTINGS.maxZoom,
            attributionControl: false
        }).setView(WORLD_VIEW.center, WORLD_VIEW.zoom);
        L.control.zoom({ position: "topright" }).addTo(state.map);
        L.control.scale({ position: "bottomright", imperial: false }).addTo(state.map);

        const [gridName, gridZIndex, gridPointerEvents] = GRID_PANE;
        const gridPane = state.map.createPane(gridName);
        gridPane.style.zIndex = String(gridZIndex);
        gridPane.style.pointerEvents = gridPointerEvents;
        state.gridLayer = L.layerGroup().addTo(state.map);
        state.map.on("zoomend moveend", drawGrid);

        PANES.forEach(([name, zIndex, pointerEvents]) => {
            const pane = state.map.createPane(name);
            pane.style.zIndex = String(zIndex);
            pane.style.pointerEvents = pointerEvents;
            state.layers[name] = L.layerGroup().addTo(state.map);
        });

        state.map.on("popupopen", (event) => {
            state.openPopupKey = event.popup?._source?._bloomKey || null;
        });
        state.map.on("popupclose", (event) => {
            if (state.redrawing) return;
            if (event.popup?._source?._bloomKey === state.openPopupKey) state.openPopupKey = null;
        });
        window.setTimeout(() => state.map.invalidateSize(), 0);
    }

    function layerOn(name) {
        const toggle = els.layerToggles.find((element) => element.dataset.layer === name);
        return toggle ? toggle.checked : true;
    }

    function mapSignature(located) {
        const view = missionView() || {};
        return JSON.stringify([
            located.map((reading) => [reading.id, reading.lat, reading.lng, reading.risk]),
            (view.cells || []).map((cell) => [cell.id, cell.risk, cell.readings, cell.confidence]),
            (view.hotspots || []).map((spot) => [spot.id, spot.lat, spot.lng, spot.radius, spot.risk, spot.revisit]),
            riskLevels(),
            els.layerToggles.map((toggle) => toggle.checked),
            state.selectedId
        ]);
    }

    function renderMap() {
        if (!state.map) return;
        const located = visibleReadings().filter(isLocated);
        const signature = mapSignature(located);
        if (signature !== state.mapSignature) {
            state.mapSignature = signature;
            redrawMap(located);
        }

        // Outside the redraw check on purpose: a mission with no fix yet draws
        // exactly what the empty page drew, but must still move onto the
        // server's configured area.
        if (!state.mapFitted && state.dashboard) {
            state.mapFitted = fitToMission(false);
            if (!state.mapFitted && !state.mapCentered) {
                // Open on the configured area once, then leave the view alone
                // so it does not jump back on every reading.
                state.mapCentered = centerOnDefault();
            }
        }
    }

    function redrawMap(located) {
        const reopenKey = state.openPopupKey;
        state.redrawing = true;
        Object.values(state.layers).forEach((layer) => layer.clearLayers());
        state.markers.clear();
        state.popupSources.clear();
        state.redrawing = false;

        // In replay these are the replayed moment's (missionView).
        if (layerOn("cells")) drawCells();
        if (layerOn("hotspots")) drawHotspots();
        if (layerOn("targets")) drawTargets();
        if (layerOn("shading")) drawShading(located);
        if (layerOn("track")) drawTrack(located);
        if (layerOn("readings")) drawReadings(located);
        reopenPopup(reopenKey);
        drawGrid();
    }

    // A metre grid under everything, redrawn whenever the view moves. Its
    // spacing is the finest of the server's MAP_GRID_METERS whose lines are
    // still SETTINGS.gridMinPixels apart: 5 m right in close, coarser further out. Lines
    // on a risk cell's edge are drawn stronger.
    function drawGrid() {
        if (!state.gridLayer) return;
        state.gridLayer.clearLayers();
        const grid = state.dashboard?.grid;
        const step = grid && layerOn("grid") ? gridStep(grid) : null;
        els.gridLabel.hidden = step === null;
        if (step === null) return;
        els.gridLabel.textContent = `Grid ${formatMeters(step)}`;

        const bounds = state.map.getBounds();
        const perDegreeLongitude = metersPerDegreeLongitude(grid);
        const line = (points, meters) => {
            const onCellEdge = step < grid.cellSize && Math.abs(meters / grid.cellSize - Math.round(meters / grid.cellSize)) < 1e-9;
            L.polyline(points, {
                pane: "grid",
                interactive: false,
                weight: STYLE.grid.weight,
                className: onCellEdge ? "map-grid-line cell-edge" : "map-grid-line"
            }).addTo(state.gridLayer);
        };
        const south = bounds.getSouth(), north = bounds.getNorth(), west = bounds.getWest(), east = bounds.getEast();
        for (let k = Math.floor(south * METERS_PER_DEGREE_LATITUDE / step); k * step / METERS_PER_DEGREE_LATITUDE <= north; k++) {
            const latitude = k * step / METERS_PER_DEGREE_LATITUDE;
            line([[latitude, west], [latitude, east]], k * step);
        }
        for (let k = Math.floor(west * perDegreeLongitude / step); k * step / perDegreeLongitude <= east; k++) {
            const longitude = k * step / perDegreeLongitude;
            line([[south, longitude], [north, longitude]], k * step);
        }
    }

    function gridStep(grid) {
        const steps = (grid.steps || []).filter((value) => isNumber(value) && value > 0).sort((a, b) => a - b);
        const bounds = state.map.getBounds();
        const widthMeters = (bounds.getEast() - bounds.getWest()) * metersPerDegreeLongitude(grid);
        const metersPerPixel = widthMeters / Math.max(1, state.map.getSize().x);
        const step = steps.find((value) => value / metersPerPixel >= SETTINGS.gridMinPixels);
        // Zoomed far out (a whole country) no spacing fits, and no grid is drawn.
        return step === undefined ? null : step;
    }

    // Longitude degrees shrink away from the equator. Sized for the mission's
    // first fix, exactly as the server sizes the cells.
    function metersPerDegreeLongitude(grid) {
        const latitude = isNumber(grid.latitude) ? grid.latitude : state.map.getCenter().lat;
        const clamped = Math.min(MAX_GRID_LATITUDE, Math.max(-MAX_GRID_LATITUDE, latitude));
        return METERS_PER_DEGREE_LATITUDE * Math.cos(clamped * Math.PI / 180);
    }

    function formatMeters(meters) {
        return meters >= 1000 ? `${meters / 1000} km` : `${meters} m`;
    }

    function drawCells() {
        (missionView()?.cells || []).forEach((cell) => {
            if (!Array.isArray(cell.bounds) || cell.bounds.length !== 2) return;
            const color = levelColor(cell.level);
            const rectangle = L.rectangle(cell.bounds, {
                pane: "cells",
                color,
                weight: STYLE.cell.weight,
                opacity: STYLE.cell.opacity,
                fillColor: color,
                fillOpacity: STYLE.cell.fillOpacity,
                bubblingMouseEvents: false
            });
            rectangle.bindPopup(cellPopupHtml(cell), { maxWidth: STYLE.cell.popupWidth });
            track(rectangle, `cell:${cell.id}`).addTo(state.layers.cells);
        });
    }

    function drawHotspots() {
        (missionView()?.hotspots || []).forEach((spot) => {
            const color = levelColor(spot.level);
            L.circle([spot.lat, spot.lng], {
                pane: "hotspots",
                radius: spot.radius,
                color,
                weight: STYLE.hotspot.weight,
                dashArray: STYLE.hotspot.dashArray,
                fillColor: color,
                fillOpacity: STYLE.hotspot.fillOpacity,
                interactive: false
            }).addTo(state.layers.hotspots);
        });
    }

    function drawTargets() {
        (missionView()?.hotspots || []).forEach((spot) => {
            if (!spot.revisit) return;
            const marker = L.marker([spot.lat, spot.lng], {
                pane: "targets",
                title: `Revisit target ${spot.revisit}`,
                icon: L.divIcon({
                    className: "",
                    html: `<span class="target-marker">${spot.revisit}</span>`,
                    iconSize: [STYLE.target.size, STYLE.target.size],
                    iconAnchor: [STYLE.target.size / 2, STYLE.target.size / 2]
                })
            });
            marker.bindPopup(targetPopupHtml(spot), { maxWidth: STYLE.target.popupWidth });
            track(marker, `target:${spot.id}`).addTo(state.layers.targets);
        });
    }

    function drawShading(located) {
        located.forEach((reading) => {
            L.circle([reading.lat, reading.lng], {
                pane: "shading",
                radius: SETTINGS.shadingRadiusMeters,
                stroke: false,
                fillColor: levelColor(reading.level),
                fillOpacity: STYLE.shading.fillOpacity,
                interactive: false
            }).addTo(state.layers.shading);
        });
    }

    function drawTrack(located) {
        if (!located.length) return;
        const points = located.map((reading) => [reading.lat, reading.lng]);
        if (points.length > 1) {
            L.polyline(points, {
                pane: "track",
                color: STYLE.track.color,
                weight: STYLE.track.weight,
                opacity: STYLE.track.opacity,
                interactive: false
            }).addTo(state.layers.track);
        }
        L.marker(points[points.length - 1], {
            pane: "track",
            interactive: false,
            icon: L.divIcon({ className: "", html: '<span class="boat-marker"></span>', iconSize: [STYLE.boat.size, STYLE.boat.size], iconAnchor: [STYLE.boat.size / 2, STYLE.boat.size / 2] })
        }).addTo(state.layers.track);
    }

    function drawReadings(located) {
        const touch = touchScreen();
        located.forEach((reading) => {
            const selected = reading.id === state.selectedId;
            const marker = L.circleMarker([reading.lat, reading.lng], {
                pane: "readings",
                radius: selected
                    ? (touch ? STYLE.reading.selectedRadiusTouch : STYLE.reading.selectedRadius)
                    : (touch ? STYLE.reading.radiusTouch : STYLE.reading.radius),
                color: selected ? STYLE.reading.selectedOutline : STYLE.reading.outline,
                weight: selected ? STYLE.reading.selectedWeight : STYLE.reading.weight,
                fillColor: levelColor(reading.level),
                fillOpacity: STYLE.reading.fillOpacity,
                bubblingMouseEvents: false
            });
            marker.bindPopup(readingPopupHtml(reading), { maxWidth: STYLE.reading.popupWidth });
            marker.on("click", () => selectReading(reading.id, { focusMap: false }));
            track(marker, `reading:${reading.id}`).addTo(state.layers.readings);
            state.markers.set(reading.id, marker);
        });
    }

    function track(layer, key) {
        layer._bloomKey = key;
        state.popupSources.set(key, layer);
        return layer;
    }

    function reopenPopup(key) {
        const source = key ? state.popupSources.get(key) : null;
        if (!source) {
            state.openPopupKey = null;
            return;
        }
        // Reopening after a redraw must not pan the map out from under the
        // operator every time a new reading arrives.
        const popup = source.getPopup?.();
        const autoPan = popup?.options?.autoPan;
        if (popup) popup.options.autoPan = false;
        source.openPopup();
        if (popup) popup.options.autoPan = autoPan;
    }

    function fitToMission(animate) {
        if (!state.map || !window.L) return false;
        const points = visibleReadings().filter(isLocated).map((reading) => [reading.lat, reading.lng]);
        if (state.replayIndex === null && layerOn("cells")) {
            (state.dashboard?.cells || []).forEach((cell) => {
                (cell.bounds || []).forEach((corner) => points.push([corner[0], corner[1]]));
            });
        }

        if (!points.length) {
            if (animate) centerOnDefault();
            return false;
        }
        const padding = [STYLE.fitPaddingPixels, STYLE.fitPaddingPixels];
        state.map.fitBounds(L.latLngBounds(points), { padding, maxZoom: SETTINGS.fitZoom, animate });
        return true;
    }

    function centerOnDefault() {
        const center = state.dashboard?.mapCenter;
        if (!state.map || !center || !isNumber(center.lat) || !isNumber(center.lng)) return false;
        state.map.setView([center.lat, center.lng], SETTINGS.openZoom, { animate: false });
        return true;
    }

    function selectReading(id, { focusMap }) {
        const reading = state.readings.find((item) => item.id === id);
        if (!reading) return;
        state.selectedId = id;
        renderTable();

        if (!isLocated(reading) || !state.map) {
            renderMap();
            return;
        }
        const readingsToggle = els.layerToggles.find((toggle) => toggle.dataset.layer === "readings");
        if (readingsToggle && !readingsToggle.checked) readingsToggle.checked = true;
        renderMap();

        if (focusMap) {
            if (!isInViewport(els.map)) {
                els.map.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" });
            }
            const marker = state.markers.get(id);
            state.map.flyTo([reading.lat, reading.lng], Math.max(state.map.getZoom(), SETTINGS.focusZoom), {
                duration: prefersReducedMotion() ? 0 : STYLE.flySeconds
            });
            state.map.once("moveend", () => marker?.openPopup());
        }
    }

    // --------------------------------------------------------------- popups

    function readingPopupHtml(reading) {
        const cell = missionView()?.cellsById?.get(reading.cell);
        const drivers = reading.drivers?.length
            ? `<p class="popup-note">Driven by ${escapeHtml(reading.drivers.join(", "))}.</p>`
            : "";
        const cellNote = cell && cell.readings > 1
            ? `<p class="popup-note">Its map cell rolls up to ${escapeHtml(cell.risk)}/100 over ${escapeHtml(cell.readings)} readings.</p>`
            : "";
        return `
            <div class="popup">
                <p class="popup-title">Reading at ${escapeHtml(formatDateTime(reading.takenAt))}</p>
                <p class="popup-line">
                    <span class="badge ${levelClass(reading.level)}">${escapeHtml(reading.risk)} ${escapeHtml(reading.level)}</span>
                    <span>${escapeHtml(reading.confidence)}% confidence</span>
                </p>
                ${factsHtml(readingFacts(reading))}
                ${drivers}
                ${cellNote}
            </div>`;
    }

    function cellPopupHtml(cell) {
        const averages = sensors()
            .filter((sensor) => isNumber(cell.averages?.[sensor.key]))
            .map((sensor) => [sensor.label, formatValue(cell.averages[sensor.key], sensor)]);
        return `
            <div class="popup">
                <p class="popup-title">Map cell</p>
                <p class="popup-line">
                    <span class="badge ${levelClass(cell.level)}">${escapeHtml(cell.risk)} ${escapeHtml(cell.level)}</span>
                    <span>peak ${escapeHtml(cell.peak)}, ${escapeHtml(cell.confidence)}% confidence</span>
                </p>
                ${factsHtml([["Readings", String(cell.readings)]])}
                ${averages.length ? `<p class="popup-note">Averages of its readings:</p>${factsHtml(averages)}` : ""}
            </div>`;
    }

    function targetPopupHtml(spot) {
        return `
            <div class="popup">
                <p class="popup-title">Revisit target ${escapeHtml(spot.revisit)}</p>
                <p class="popup-note">${escapeHtml(spot.reason)}</p>
                ${factsHtml([
                    ["Priority", `${spot.priority}/100`],
                    ["Readings in this hotspot", String(spot.readings)]
                ])}
            </div>`;
    }

    function factsHtml(rows) {
        return `<dl class="facts">${rows.map(([label, value]) => (
            `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
        )).join("")}</dl>`;
    }

    // -------------------------------------------------------------- helpers

    function riskLevels() {
        return Array.isArray(state.dashboard?.riskLevels) ? state.dashboard.riskLevels : [];
    }

    function sensors() {
        return Array.isArray(state.dashboard?.sensors) ? state.dashboard.sensors : [];
    }

    // The sensors worth a column: any that a loaded reading has a value for,
    // so probes that are not wired yet do not fill every row with dashes.
    function measuredSensors() {
        if (!state.readings.length) return sensors();
        return sensors().filter((sensor) => state.readings.some((reading) => isNumber(reading[sensor.key])));
    }

    function levelColor(label) {
        return safeColor(riskLevels().find((level) => level.label === label)?.color);
    }

    // The badge's readable text and tint come from style.css.
    function levelClass(label) {
        return label ? `risk-${String(label).toLowerCase().replace(/[^a-z]/g, "")}` : "";
    }

    function isNumber(value) {
        return value !== null && value !== "" && typeof value !== "boolean" && Number.isFinite(Number(value));
    }

    function isLocated(reading) {
        return isNumber(reading?.lat) && isNumber(reading?.lng);
    }

    function formatValue(value, sensor) {
        if (!isNumber(value)) return "--";
        return `${Number(value).toFixed(sensor.digits)}${sensor.unit ? ` ${sensor.unit}` : ""}`;
    }

    function formatPosition(reading) {
        return isLocated(reading) ? `${Number(reading.lat).toFixed(5)}, ${Number(reading.lng).toFixed(5)}` : "No GPS fix";
    }

    function formatTime(value) {
        const date = new Date(value);
        return Number.isFinite(date.getTime()) ? formatClock(date) : "--";
    }

    function formatDateTime(value) {
        const date = new Date(value);
        if (!Number.isFinite(date.getTime())) return "--";
        return `${date.toLocaleDateString([], { month: "short", day: "numeric" })}, ${formatClock(date)}`;
    }

    function formatClock(date) {
        return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
    }

    function formatDuration(start, end) {
        const from = Date.parse(start);
        const to = end ? Date.parse(end) : Date.now();
        if (!Number.isFinite(from) || !Number.isFinite(to)) return "--";
        const minutes = Math.max(0, Math.round((to - from) / 60000));
        const hours = Math.floor(minutes / 60);
        return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
    }

    function safeColor(value) {
        return /^#[0-9a-f]{3,8}$/i.test(String(value || "")) ? value : NEUTRAL_COLOR;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function touchScreen() {
        return Boolean(window.matchMedia?.("(pointer: coarse)").matches);
    }

    function prefersReducedMotion() {
        return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
    }

    function isInViewport(element) {
        const rect = element.getBoundingClientRect();
        return rect.top >= 0 && rect.bottom <= (window.innerHeight || document.documentElement.clientHeight);
    }
})();
