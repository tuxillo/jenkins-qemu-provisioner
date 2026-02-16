(function () {
  const AUTO_REFRESH_KEY = "cp.ui.autoRefreshSec";
  const WRAP_KEY = "cp.ui.wrapMode";
  const DENSITY_KEY = "cp.ui.density";
  const AUTO_REFRESH_VALUES = new Set(["0", "5", "10", "30"]);
  const root = document.getElementById("app");
  const snapshotNode = document.getElementById("cp-snapshot");
  if (!root || !snapshotNode) {
    return;
  }

  let snapshot;
  try {
    snapshot = JSON.parse(snapshotNode.textContent || "{}");
  } catch (_err) {
    root.innerHTML = "<p>Could not parse dashboard snapshot.</p>";
    return;
  }

  const counts = snapshot.counts || {};
  const hosts = snapshot.hosts || [];
  const leases = snapshot.leases || [];
  const events = snapshot.events || [];
  const metrics = snapshot.metrics || {};
  const leaseById = new Map(leases.map((l) => [l.lease_id, l]));

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function parseJson(text) {
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (_err) {
      return { raw_payload: String(text) };
    }
  }

  function toNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function cpuUse(host) {
    const total = toNumber(host.cpu_total);
    const free = toNumber(host.cpu_free);
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round(((total - free) / total) * 100)));
  }

  function ramUse(host) {
    const total = toNumber(host.ram_total_mb);
    const free = toNumber(host.ram_free_mb);
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round(((total - free) / total) * 100)));
  }

  function toIso(value) {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function ageText(isoString) {
    const d = toIso(isoString);
    if (!d) return "-";
    const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h`;
    const day = Math.floor(hr / 24);
    return `${day}d`;
  }

  function fmtState(state) {
    return `<span class="badge state-${esc(state)}">${esc(state)}</span>`;
  }

  function clip(value, cls = "") {
    const text = value == null || value === "" ? "-" : String(value);
    const classAttr = cls ? ` class="${cls}"` : "";
    return `<span${classAttr} title="${esc(text)}">${esc(text)}</span>`;
  }

  function badgeMetric(key, value) {
    return `<span class="metric-chip" title="${esc(key)}"><strong>${esc(value)}</strong> ${esc(
      key
    )}</span>`;
  }

  function stageMetrics(metricsObj) {
    const keys = Object.keys(metricsObj || {});
    if (!keys.length) return "<span class='muted'>No metrics yet.</span>";
    return keys
      .sort((a, b) => toNumber(metricsObj[b]) - toNumber(metricsObj[a]))
      .slice(0, 8)
      .map((key) => badgeMetric(key, metricsObj[key]))
      .join("");
  }

  const generatedAt = snapshot.generated_at || "unknown";
  const generatedDate = toIso(generatedAt);
  const staleSeconds = generatedDate
    ? Math.max(0, Math.floor((Date.now() - generatedDate.getTime()) / 1000))
    : null;
  const staleLabel =
    staleSeconds === null
      ? "snapshot age: unknown"
      : staleSeconds < 15
        ? `snapshot age: ${staleSeconds}s (fresh)`
        : `snapshot age: ${staleSeconds}s`;

  const stateCounts = counts.leases_by_state || {};
  const hottestState = Object.entries(stateCounts).sort((a, b) => b[1] - a[1])[0] || null;
  const topStateLabel = hottestState ? `${hottestState[0]} (${hottestState[1]})` : "-";

  const hostAvailabilityValues = ["ALL", "AVAILABLE", "STALE", "DISABLED", "UNAVAILABLE"];
  const leaseStateValues = ["ALL"]
    .concat(Object.keys(stateCounts).sort())
    .filter((v, idx, arr) => arr.indexOf(v) === idx);

  root.innerHTML = `
    <main class="layout-shell mode-wrap density-compact" id="uiShell">
      <header class="hero">
        <div class="hero-copy">
          <h1>Control Plane Dashboard</h1>
          <p>Snapshot rendered at <strong>${esc(generatedAt)}</strong> · ${esc(staleLabel)}</p>
        </div>
        <div class="hero-controls">
          <label class="control">Search
            <input id="qSearch" type="search" placeholder="Host, lease, event, error" />
          </label>
          <label class="control">Host status
            <select id="hostStatusFilter">
              ${hostAvailabilityValues
                .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
                .join("")}
            </select>
          </label>
          <label class="control">Lease state
            <select id="leaseStateFilter">
              ${leaseStateValues
                .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
                .join("")}
            </select>
          </label>
          <label class="control">Auto refresh
            <select id="autoRefresh">
              <option value="0">Off</option>
              <option value="5">5s</option>
              <option value="10">10s</option>
              <option value="30">30s</option>
            </select>
          </label>
          <button id="toggleWrap" class="btn" type="button">Wrap: On</button>
          <button id="toggleDensity" class="btn" type="button">Density: Compact</button>
          <button id="refreshBtn" class="btn btn-accent" type="button">Refresh</button>
        </div>
      </header>

      <section class="kpi-grid">
        <article class="kpi-card"><h2>Hosts</h2><p>${esc(counts.hosts_total || 0)}</p></article>
        <article class="kpi-card"><h2>Leases</h2><p>${esc(counts.leases_total || 0)}</p></article>
        <article class="kpi-card"><h2>Events</h2><p>${esc(counts.events_total || 0)}</p></article>
        <article class="kpi-card"><h2>Top State</h2><p>${esc(topStateLabel)}</p></article>
      </section>

      <section class="metrics-strip">
        <h2>Hot Metrics</h2>
        <div class="metrics-flow">${stageMetrics(metrics)}</div>
      </section>

      <section class="panels">
        <article class="panel" id="hostsPanel">
          <h2>Hosts <span class="muted" id="hostsCount"></span></h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>Host</th><th>Status</th><th>Platform</th><th>Addr</th><th>CPU</th><th>RAM</th><th>IO</th><th>Last Seen</th></tr>
              </thead>
              <tbody id="hostsBody"></tbody>
            </table>
          </div>
        </article>

        <article class="panel" id="leasesPanel">
          <h2>Leases <span class="muted" id="leasesCount"></span></h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>Lease</th><th>State</th><th>Label</th><th>Host</th><th>VM</th><th>Node</th><th>Build</th><th>Age</th><th>Error</th></tr>
              </thead>
              <tbody id="leasesBody"></tbody>
            </table>
          </div>
        </article>

        <article class="panel" id="eventsPanel">
          <h2>Events <span class="muted" id="eventsCount"></span></h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>ID</th><th>Timestamp</th><th>Type</th><th>Host</th><th>Lease</th><th>Node</th><th>Details</th></tr>
              </thead>
              <tbody id="eventsBody"></tbody>
            </table>
          </div>
        </article>

        <article class="panel" id="metricsPanel">
          <h2>Metrics <span class="muted" id="metricsCount"></span></h2>
          <div class="table-wrap metrics-wrap">
            <table>
              <thead>
                <tr><th>Metric</th><th>Value</th></tr>
              </thead>
              <tbody id="metricsBody"></tbody>
            </table>
          </div>
        </article>
      </section>
    </main>
  `;

  const shell = document.getElementById("uiShell");
  const qSearch = document.getElementById("qSearch");
  const hostStatusFilter = document.getElementById("hostStatusFilter");
  const leaseStateFilter = document.getElementById("leaseStateFilter");
  const refreshBtn = document.getElementById("refreshBtn");
  const autoRefresh = document.getElementById("autoRefresh");
  const toggleWrap = document.getElementById("toggleWrap");
  const toggleDensity = document.getElementById("toggleDensity");

  const hostsBody = document.getElementById("hostsBody");
  const leasesBody = document.getElementById("leasesBody");
  const eventsBody = document.getElementById("eventsBody");
  const metricsBody = document.getElementById("metricsBody");
  const hostsCount = document.getElementById("hostsCount");
  const leasesCount = document.getElementById("leasesCount");
  const eventsCount = document.getElementById("eventsCount");
  const metricsCount = document.getElementById("metricsCount");

  function readStored(key, allowed, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      if (value && allowed.has(value)) return value;
    } catch (_err) {
      return fallback;
    }
    return fallback;
  }

  function writeStored(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_err) {
      // ignore storage failures
    }
  }

  function hostRows(filteredHosts) {
    if (!filteredHosts.length) {
      return "<tr><td colspan='8' class='muted'>No host rows match current filters.</td></tr>";
    }
    return filteredHosts
      .map((h) => {
        const availability = h.availability || (h.enabled ? "AVAILABLE" : "DISABLED");
        return `<tr>
          <td>${clip(h.host_id)}</td>
          <td><span class="badge host-${availability.toLowerCase()}">${esc(availability)}</span></td>
          <td>${clip(`${h.os_family || "-"}/${h.os_flavor || "-"}/${h.cpu_arch || "-"}`)}</td>
          <td>${clip(h.addr)}</td>
          <td>${clip(`${h.cpu_free}/${h.cpu_total} (${cpuUse(h)}%)`)}</td>
          <td>${clip(`${h.ram_free_mb}/${h.ram_total_mb} MB (${ramUse(h)}%)`)}</td>
          <td>${clip(Number(h.io_pressure || 0).toFixed(2))}</td>
          <td>${clip(`${h.last_seen || "-"} (${ageText(h.last_seen)})`)}</td>
        </tr>`;
      })
      .join("");
  }

  function leaseRows(filteredLeases) {
    if (!filteredLeases.length) {
      return "<tr><td colspan='9' class='muted'>No lease rows match current filters.</td></tr>";
    }
    return filteredLeases
      .map((l) => {
        const build = l.bound_build_url
          ? `<a class="mono-link" href="${esc(l.bound_build_url)}" target="_blank" rel="noreferrer">build</a>`
          : "-";
        return `<tr>
          <td>${clip(l.lease_id)}</td>
          <td>${fmtState(l.state)}</td>
          <td>${clip(l.label)}</td>
          <td>${clip(l.host_id)}</td>
          <td>${clip(l.vm_id)}</td>
          <td>${clip(l.jenkins_node)}</td>
          <td>${build}</td>
          <td>${clip(ageText(l.updated_at))}</td>
          <td>${clip(l.last_error)}</td>
        </tr>`;
      })
      .join("");
  }

  function eventRows(filteredEvents) {
    if (!filteredEvents.length) {
      return "<tr><td colspan='7' class='muted'>No event rows match current filters.</td></tr>";
    }
    return filteredEvents
      .map((e) => {
        const payload = parseJson(e.payload_json);
        const lease = e.lease_id ? leaseById.get(e.lease_id) : null;
        const host = payload.host_id || lease?.host_id || "-";
        const node = payload.jenkins_node || lease?.jenkins_node || "-";
        let details = payload.error_detail || payload.error || "-";
        if (details === "-" && payload.reject_reasons) {
          details = JSON.stringify(payload.reject_reasons);
        } else if (details === "-" && payload.current_build_url) {
          details = `bound mismatch: ${payload.current_build_url}`;
        } else if (details === "-" && payload.build_url) {
          details = `bound: ${payload.build_url}`;
        } else if (details === "-" && payload.raw_payload) {
          details = payload.raw_payload;
        }
        const detailSummary = String(details);
        return `<tr>
          <td>${clip(e.id)}</td>
          <td>${clip(`${e.timestamp || "-"} (${ageText(e.timestamp)})`)}</td>
          <td>${clip(e.event_type)}</td>
          <td>${clip(host)}</td>
          <td>${clip(e.lease_id)}</td>
          <td>${clip(node)}</td>
          <td><details><summary>${clip(detailSummary)}</summary><pre>${esc(JSON.stringify(payload, null, 2))}</pre></details></td>
        </tr>`;
      })
      .join("");
  }

  function metricRows(metricEntries) {
    if (!metricEntries.length) {
      return "<tr><td colspan='2' class='muted'>No metrics have been recorded yet.</td></tr>";
    }
    return metricEntries
      .map(([k, v]) => `<tr><td>${clip(k)}</td><td>${clip(v)}</td></tr>`)
      .join("");
  }

  function updateTables() {
    const q = (qSearch && qSearch.value ? qSearch.value : "").toLowerCase().trim();
    const hostStatus = hostStatusFilter && hostStatusFilter.value ? hostStatusFilter.value : "ALL";
    const leaseState = leaseStateFilter && leaseStateFilter.value ? leaseStateFilter.value : "ALL";

    const filteredHosts = hosts.filter((h) => {
      const availability = h.availability || (h.enabled ? "AVAILABLE" : "DISABLED");
      if (hostStatus !== "ALL" && availability !== hostStatus) return false;
      if (!q) return true;
      const haystack = [
        h.host_id,
        availability,
        h.os_family,
        h.os_flavor,
        h.cpu_arch,
        h.addr,
        h.selected_accel,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });

    const filteredLeases = leases.filter((l) => {
      if (leaseState !== "ALL" && l.state !== leaseState) return false;
      if (!q) return true;
      const haystack = [
        l.lease_id,
        l.label,
        l.state,
        l.host_id,
        l.vm_id,
        l.jenkins_node,
        l.last_error,
        l.bound_build_url,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });

    const filteredEvents = events.filter((e) => {
      const payload = parseJson(e.payload_json);
      const lease = e.lease_id ? leaseById.get(e.lease_id) : null;
      if (!q) return true;
      const haystack = [
        e.id,
        e.timestamp,
        e.event_type,
        e.lease_id,
        payload.error,
        payload.error_detail,
        payload.host_id,
        payload.jenkins_node,
        payload.build_url,
        payload.current_build_url,
        lease?.host_id,
        lease?.jenkins_node,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });

    const metricEntries = Object.entries(metrics).sort((a, b) => toNumber(b[1]) - toNumber(a[1]));
    const filteredMetrics = metricEntries.filter(([k]) => !q || k.toLowerCase().includes(q));

    if (hostsBody) hostsBody.innerHTML = hostRows(filteredHosts);
    if (leasesBody) leasesBody.innerHTML = leaseRows(filteredLeases.slice(0, 250));
    if (eventsBody) eventsBody.innerHTML = eventRows(filteredEvents.slice(0, 150));
    if (metricsBody) metricsBody.innerHTML = metricRows(filteredMetrics);

    if (hostsCount) hostsCount.textContent = `(${filteredHosts.length}/${hosts.length})`;
    if (leasesCount) leasesCount.textContent = `(${filteredLeases.length}/${leases.length})`;
    if (eventsCount) eventsCount.textContent = `(${filteredEvents.length}/${events.length})`;
    if (metricsCount) metricsCount.textContent = `(${filteredMetrics.length}/${metricEntries.length})`;
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => window.location.reload());
  }

  let timer = null;
  function applyAutoRefresh(seconds) {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
    if (seconds > 0) {
      timer = window.setInterval(() => window.location.reload(), seconds * 1000);
    }
  }

  const autoRefreshValue = readStored(AUTO_REFRESH_KEY, AUTO_REFRESH_VALUES, "0");
  if (autoRefresh) {
    autoRefresh.value = autoRefreshValue;
    applyAutoRefresh(Number(autoRefreshValue));
    autoRefresh.addEventListener("change", () => {
      const next = autoRefresh.value || "0";
      writeStored(AUTO_REFRESH_KEY, next);
      applyAutoRefresh(Number(next));
    });
  }

  const wrapValues = new Set(["wrap", "compact"]);
  const wrapMode = readStored(WRAP_KEY, wrapValues, "wrap");
  function applyWrap(mode) {
    if (!shell) return;
    shell.classList.remove("mode-wrap", "mode-compact");
    shell.classList.add(mode === "compact" ? "mode-compact" : "mode-wrap");
    if (toggleWrap) toggleWrap.textContent = `Wrap: ${mode === "compact" ? "Off" : "On"}`;
  }
  applyWrap(wrapMode);
  if (toggleWrap) {
    toggleWrap.addEventListener("click", () => {
      const next = shell && shell.classList.contains("mode-wrap") ? "compact" : "wrap";
      writeStored(WRAP_KEY, next);
      applyWrap(next);
    });
  }

  const densityValues = new Set(["compact", "cozy"]);
  const density = readStored(DENSITY_KEY, densityValues, "compact");
  function applyDensity(mode) {
    if (!shell) return;
    shell.classList.remove("density-compact", "density-cozy");
    shell.classList.add(mode === "cozy" ? "density-cozy" : "density-compact");
    if (toggleDensity) {
      toggleDensity.textContent = `Density: ${mode === "cozy" ? "Cozy" : "Compact"}`;
    }
  }
  applyDensity(density);
  if (toggleDensity) {
    toggleDensity.addEventListener("click", () => {
      const next = shell && shell.classList.contains("density-compact") ? "cozy" : "compact";
      writeStored(DENSITY_KEY, next);
      applyDensity(next);
    });
  }

  if (qSearch) qSearch.addEventListener("input", updateTables);
  if (hostStatusFilter) hostStatusFilter.addEventListener("change", updateTables);
  if (leaseStateFilter) leaseStateFilter.addEventListener("change", updateTables);

  updateTables();
})();
