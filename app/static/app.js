// Wake + reachability polling for the machine table.
// Buttons are progressive: with JS they do fetch calls; the Delete form and the
// add form still work as plain server-rendered POSTs.

const POLL_INTERVAL_MS = 3000;    // fast poll cadence right after a Wake
const POLL_DURATION_MS = 30000;   // how long that fast poll lasts
const AUTO_REFRESH_MS = 20000;    // background refresh cadence for all rows

// MACs with a status request currently in flight — prevents the background
// refresh and wake-polling from stacking duplicate checks on the same row.
const inFlight = new Set();

function rowFor(el) {
  return el.closest("tr[data-mac]");
}

function setStatus(row, state, detail) {
  const badge = row.querySelector("[data-status]");
  if (!badge) return;
  const labels = {
    unknown: "unknown",
    checking: "checking…",
    online: "online",
    offline: "offline",
  };
  badge.textContent = labels[state] || state;
  badge.className = "status status-" + state;
  if (detail) badge.title = detail;
}

function describe(data) {
  const bits = [];
  if (data.ip) bits.push(data.discovered ? `IP ${data.ip} (auto)` : `IP ${data.ip}`);
  if (data.arp) {
    bits.push(data.arp.reachable ? `ARP: ${data.arp.mac}` : "ARP: no reply");
    if (data.arp.mac_matches === false) bits.push("⚠ MAC mismatch");
  }
  if (data.tcp) bits.push(`TCP ${data.tcp.port}: ${data.tcp.open ? "open" : "closed"}`);
  if (!data.ip && data.reason) bits.push(data.reason);
  return bits.join(" · ");
}

// Separate TCP-port indicator shown beside the ARP status. State is one of
// open | closed | unknown; the port comes from the badge's data attribute.
function setTcp(row, state) {
  const badge = row.querySelector("[data-tcp]");
  if (!badge) return;
  const port = badge.dataset.tcpPort;
  const text = { open: `:${port} open`, closed: `:${port} closed`, unknown: `:${port} ?` };
  const cls = { open: "online", closed: "offline", unknown: "unknown" };
  badge.textContent = text[state] || text.unknown;
  badge.className = "status tcp-badge status-" + (cls[state] || "unknown");
}

// Show the address in the IP column: a resolved hostname as `host (ip)`, an
// auto-discovered IP as `ip (auto)`, or a plain explicit IP as-is.
function fillIp(row, ip, discovered, host) {
  const cell = row.querySelector("[data-ip-cell]");
  if (!cell) return;
  if (host) {
    cell.innerHTML = ip
      ? `<code>${host}</code> <span class="hint">(${ip})</span>`
      : `<code>${host}</code>`;
  } else if (ip) {
    const suffix = discovered ? ' <span class="hint">(auto)</span>' : "";
    cell.innerHTML = `<code>${ip}</code>${suffix}`;
  }
}

async function checkOnce(row) {
  const mac = row.dataset.mac;
  if (inFlight.has(mac)) return false; // a check is already running for this row
  inFlight.add(mac);
  setStatus(row, "checking");
  try {
    const res = await fetch(`/machines/${encodeURIComponent(mac)}/status`);
    const data = await res.json();
    fillIp(row, data.ip, data.discovered, data.host);
    setStatus(row, data.online ? "online" : "offline", describe(data));
    setTcp(row, data.tcp ? (data.tcp.open ? "open" : "closed") : "unknown");
    return data.online;
  } catch (err) {
    setStatus(row, "offline", String(err));
    return false;
  } finally {
    inFlight.delete(mac);
  }
}

// Refresh every machine's status (and discovered IP) in the background.
// Sequential so we don't fire many subnet sweeps at once; cached IPs are fast.
let refreshing = false;
async function refreshAll() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    for (const row of document.querySelectorAll("tr[data-mac]")) {
      await checkOnce(row);
    }
  } finally {
    refreshing = false;
  }
}

// Poll until the machine reports online or the time budget runs out.
async function pollUntilOnline(row) {
  const stopAt = performance.now() + POLL_DURATION_MS;
  // First check immediately, then on an interval.
  if (await checkOnce(row)) return;
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      if (performance.now() >= stopAt) {
        clearInterval(timer);
        resolve();
        return;
      }
      if (await checkOnce(row)) {
        clearInterval(timer);
        resolve();
      }
    }, POLL_INTERVAL_MS);
  });
}

async function wake(row, btn) {
  const mac = row.dataset.mac;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    const res = await fetch(`/machines/${encodeURIComponent(mac)}/wake`, {
      method: "POST",
      headers: { "X-Requested-With": "fetch" },
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "wake failed");
    btn.textContent = "Sent ✓";
    if (data.can_poll) {
      pollUntilOnline(row).finally(() => {
        btn.disabled = false;
        btn.textContent = original;
      });
      return;
    }
  } catch (err) {
    btn.textContent = "Failed";
    setStatus(row, "offline", String(err));
  }
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = original;
  }, 1500);
}

document.addEventListener("click", (ev) => {
  const wakeBtn = ev.target.closest("[data-wake]");
  if (wakeBtn) {
    wake(rowFor(wakeBtn), wakeBtn);
    return;
  }
  const checkBtn = ev.target.closest("[data-check]");
  if (checkBtn && !checkBtn.disabled) {
    checkOnce(rowFor(checkBtn));
  }
});

// Auto-refresh: check everything on load, then on a timer, and again whenever
// the tab becomes visible (so it's fresh when you come back to it).
refreshAll();
setInterval(refreshAll, AUTO_REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshAll();
});
