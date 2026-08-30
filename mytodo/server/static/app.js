const statusEl = document.getElementById("status");
const loginEl = document.getElementById("login");
const appEl = document.getElementById("app");
const listEl = document.getElementById("list");
const tokenEl = document.getElementById("token");

function token() {
  return localStorage.getItem("mytodo_token") || "";
}
function setStatus(msg) {
  statusEl.textContent = msg || "";
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      Authorization: "Bearer " + token(),
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function taskStatus(t) {
  const due = t.due;
  if (!due) return "not_due";
  try {
    const d = new Date(due);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dd = new Date(d);
    dd.setHours(0, 0, 0, 0);
    return dd <= today ? "due" : "not_due";
  } catch {
    return "not_due";
  }
}

function activityKind(t) {
  if (t.show_on_calendar) return "calendar";
  if (t.passive) return "passive";
  return "active";
}

function stars(p) {
  p = Math.max(0, Math.min(3, parseInt(p || 0, 10) || 0));
  return p ? "★".repeat(p) : "";
}

function icons(t) {
  let s = stars(t.priority);
  if (t.type === "recurring") s += "↻";
  if (t.show_on_calendar) s += "📅";
  if (t.passive) s += "💤";
  if ((t.due_anchor || "sticky") === "flexi") s += "🔀";
  if ((t.notes || "").trim()) s += "📝";
  return s;
}

function formatDue(due) {
  if (!due) return "";
  const d = new Date(due);
  if (Number.isNaN(d.getTime())) return due;
  const midnight = d.getHours() === 0 && d.getMinutes() === 0;
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yy = String(d.getFullYear()).slice(-2);
  if (midnight) return `${dd}/${mm}/${yy}`;
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${dd}/${mm}/${yy} ${hh}:${mi}`;
}

let doc = { active: [], history: [] };

function visible(t) {
  const st = taskStatus(t);
  if (st === "due" && !document.getElementById("f-due").checked) return false;
  if (st !== "due" && !document.getElementById("f-notdue").checked) return false;
  const kind = activityKind(t);
  if (kind === "passive" && !document.getElementById("f-passive").checked) return false;
  if (kind === "active" && !document.getElementById("f-active").checked) return false;
  if (kind === "calendar" && !document.getElementById("f-cal").checked) return false;
  return true;
}

function render() {
  const items = (doc.active || []).filter(visible);
  items.sort((a, b) => {
    const pa = 3 - (parseInt(a.priority || 0, 10) || 0);
    const pb = 3 - (parseInt(b.priority || 0, 10) || 0);
    if (pa !== pb) return pa - pb;
    return String(a.due || "9999").localeCompare(String(b.due || "9999"));
  });
  listEl.innerHTML = items
    .map((t) => {
      const st = taskStatus(t);
      return `<article class="task ${st}" data-id="${t.id}">
        <div class="name">${icons(t)} ${escapeHtml(t.text || "(unnamed)")}</div>
        <div class="meta">${st === "due" ? "Due" : "Not due"}${t.due ? " · " + formatDue(t.due) : ""}</div>
        <div class="actions">
          <button class="ok" data-op="complete">Complete</button>
          <button data-op="skip">Skip</button>
        </div>
      </article>`;
    })
    .join("") || `<p class="status">No tasks match these filters.</p>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function reload() {
  setStatus("Loading…");
  const payload = await api("GET", "/api/document");
  doc = payload.document || { active: [], history: [] };
  setStatus(`Online · rev ${payload.rev} · ${ (doc.active || []).length } active`);
  render();
}

async function runOp(op, extra) {
  setStatus("Saving…");
  const payload = await api("POST", "/api/action", { op, ...extra });
  doc = payload.document || doc;
  setStatus(`Saved · rev ${payload.rev}`);
  render();
}

function showApp() {
  loginEl.classList.add("hidden");
  appEl.classList.remove("hidden");
}

function showLogin() {
  appEl.classList.add("hidden");
  loginEl.classList.remove("hidden");
}

document.getElementById("save-token").onclick = async () => {
  const t = tokenEl.value.trim();
  if (!t) return;
  localStorage.setItem("mytodo_token", t);
  try {
    showApp();
    await reload();
  } catch (e) {
    localStorage.removeItem("mytodo_token");
    showLogin();
    setStatus(e.message);
  }
};

document.getElementById("logout").onclick = () => {
  localStorage.removeItem("mytodo_token");
  showLogin();
  setStatus("");
};
document.getElementById("reload").onclick = () => reload().catch((e) => setStatus(e.message));
["f-due", "f-notdue", "f-passive", "f-active", "f-cal"].forEach((id) => {
  document.getElementById(id).onchange = render;
});

listEl.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-op]");
  if (!btn) return;
  const card = ev.target.closest(".task");
  runOp(btn.dataset.op, { task_id: card.dataset.id }).catch((e) => setStatus(e.message));
});

document.getElementById("add").onclick = () => {
  const text = document.getElementById("new-text").value.trim();
  if (!text) return;
  const date = document.getElementById("new-due").value;
  const cal = document.getElementById("new-cal").checked;
  const passive = document.getElementById("new-passive").checked;
  const priority = parseInt(document.getElementById("new-pri").value, 10) || 0;
  const due = date ? `${date}T00:00:00` : null;
  runOp("add", {
    task: {
      text,
      due,
      show_on_calendar: cal && !!due,
      passive: passive && !cal,
      priority,
    },
  })
    .then(() => {
      document.getElementById("new-text").value = "";
    })
    .catch((e) => setStatus(e.message));
};

if (token()) {
  showApp();
  reload().catch((e) => {
    setStatus(e.message);
    showLogin();
  });
}
