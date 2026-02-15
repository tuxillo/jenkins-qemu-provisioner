(function () {
  const AUTO_REFRESH_KEY = "cp.ui.autoRefreshSec";
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
  const generatedAt = snapshot.generated_at || "unknown";
  const generatedDate = snapshot.generated_at ? new Date(snapshot.generated_at) : null;
  const ageSec = generatedDate ? Math.max(0, Math.floor((Date.now() - generatedDate.getTime()) / 1000)) : null;
  const staleLabel =
    ageSec === null
      ? "staleness: unknown"
      : ageSec < 15
        ? `staleness: ${ageSec}s (fresh)`
        : `staleness: ${ageSec}s`;

  function fmtState(state) {
    return `<span class=\"badge state-${state}\">${state}</span>`;
  }

  function hostRows() {
    if (!hosts.length) {
      return "<tr><td colspan='8' class='muted'>No hosts registered yet.</td></tr>";
    }
    return hosts
      .map((h) => {
        const cpuUse = h.cpu_total > 0 ? Math.round(((h.cpu_total - h.cpu_free) / h.cpu_total) * 100) : 0;
        const ramUse = h.ram_total_mb > 0 ? Math.round(((h.ram_total_mb - h.ram_free_mb) / h.ram_total_mb) * 100) : 0;
        const availability = h.availability || (h.enabled ? "AVAILABLE" : "DISABLED");
        return `<tr>
          <td>${h.host_id}</td>
          <td><span class="badge host-${availability.toLowerCase()}">${availability}</span></td>
          <td>${h.os_family || "-"}/${h.os_flavor || "-"}/${h.cpu_arch || "-"}</td>
          <td>${h.addr || "-"}</td>
          <td>${h.cpu_free}/${h.cpu_total} (${cpuUse}%)</td>
          <td>${h.ram_free_mb}/${h.ram_total_mb} MB (${ramUse}%)</td>
          <td>${h.io_pressure.toFixed(2)}</td>
          <td>${h.last_seen || "-"}</td>
        </tr>`;
      })
      .join("");
  }

  function leaseRows() {
    if (!leases.length) {
      return "<tr><td colspan='7' class='muted'>No leases yet.</td></tr>";
    }
    return leases
      .slice(0, 200)
      .map(
        (l) => `<tr>
        <td>${l.lease_id}</td>
        <td>${l.label}</td>
        <td>${fmtState(l.state)}</td>
        <td>${l.host_id || "-"}</td>
        <td>${l.vm_id}</td>
        <td>${l.jenkins_node}</td>
        <td>${l.last_error || "-"}</td>
      </tr>`
      )
      .join("");
  }

  function eventRows() {
    if (!events.length) {
      return "<tr><td colspan='5' class='muted'>No events yet.</td></tr>";
    }
    return events
      .map((e) => {
        let details = "-";
        try {
          const payload = e.payload_json ? JSON.parse(e.payload_json) : {};
          if (payload.error_detail || payload.error) {
            details = payload.error_detail || payload.error;
          } else if (payload.reject_reasons) {
            details = JSON.stringify(payload.reject_reasons);
          } else if (payload.node_agent_url) {
            details = `node: ${payload.node_agent_url}`;
          }
        } catch (_err) {
          details = e.payload_json || "-";
        }
        return `<tr>
        <td>${e.id}</td>
        <td>${e.timestamp || "-"}</td>
        <td>${e.event_type}</td>
        <td>${e.lease_id || "-"}</td>
        <td>${details}</td>
      </tr>`;
      })
      .join("");
  }

  function hotStates() {
    const byState = counts.leases_by_state || {};
    return Object.entries(byState)
      .slice(0, 4)
      .map(([state, count]) => `<span class=\"badge state-${state}\">${state}: ${count}</span>`)
      .join(" ");
  }

  root.innerHTML = `
    <main class="wrap">
      <section class="head">
        <div>
          <h1 class="title">Control Plane Dashboard</h1>
          <p class="sub">Read-only snapshot generated at ${generatedAt}</p>
          <p class="sub">${staleLabel}</p>
        </div>
        <div class="actions">
          <select id="autoRefresh">
            <option value="0">Auto refresh: off</option>
            <option value="5">Every 5s</option>
            <option value="10">Every 10s</option>
            <option value="30">Every 30s</option>
          </select>
          <button id="refreshBtn">Refresh now</button>
        </div>
      </section>

      <section class="grid">
        <article class="card"><h3>Hosts</h3><div class="value">${counts.hosts_total || 0}</div></article>
        <article class="card"><h3>Leases</h3><div class="value">${counts.leases_total || 0}</div></article>
        <article class="card"><h3>Recent Events</h3><div class="value">${counts.events_total || 0}</div></article>
        <article class="card"><h3>Hot States</h3><div class="value" style="font-size:0.95rem">${hotStates() || "-"}</div></article>
      </section>

      <section class="layout">
        <article class="panel">
          <h2>Leases</h2>
          <div class="scroll">
            <table>
              <thead>
                <tr><th>Lease</th><th>Label</th><th>State</th><th>Host</th><th>VM</th><th>Jenkins Node</th><th>Error</th></tr>
              </thead>
              <tbody>${leaseRows()}</tbody>
            </table>
          </div>
        </article>
        <article class="panel">
          <h2>Hosts</h2>
          <div class="scroll">
            <table>
              <thead>
                <tr><th>Host</th><th>Status</th><th>Platform</th><th>Addr</th><th>CPU</th><th>RAM</th><th>IO</th><th>Last Seen</th></tr>
              </thead>
              <tbody>${hostRows()}</tbody>
            </table>
          </div>
        </article>
      </section>

      <section class="panel" style="margin-top:12px">
        <h2>Recent Events</h2>
        <div class="scroll">
          <table>
            <thead>
               <tr><th>ID</th><th>Timestamp</th><th>Type</th><th>Lease</th><th>Details</th></tr>
            </thead>
            <tbody>${eventRows()}</tbody>
          </table>
        </div>
      </section>
    </main>
  `;

  const refreshBtn = document.getElementById("refreshBtn");
  const autoRefresh = document.getElementById("autoRefresh");

  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => window.location.reload());
  }

  let timer = null;

  function readStoredAutoRefreshSec() {
    try {
      const value = window.localStorage.getItem(AUTO_REFRESH_KEY);
      if (value && AUTO_REFRESH_VALUES.has(value)) {
        return value;
      }
    } catch (_err) {
      return "0";
    }
    return "0";
  }

  function writeStoredAutoRefreshSec(value) {
    try {
      window.localStorage.setItem(AUTO_REFRESH_KEY, value);
    } catch (_err) {
      // ignore storage failures
    }
  }

  function applyAutoRefresh(seconds) {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
    if (seconds > 0) {
      timer = window.setInterval(() => window.location.reload(), seconds * 1000);
    }
  }

  if (autoRefresh) {
    autoRefresh.value = readStoredAutoRefreshSec();
    applyAutoRefresh(Number(autoRefresh.value || 0));

    autoRefresh.addEventListener("change", () => {
      writeStoredAutoRefreshSec(autoRefresh.value || "0");
      applyAutoRefresh(Number(autoRefresh.value || 0));
    });
  }
})();
