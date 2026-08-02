const bridge = window.AstrBotPluginPage;
const statusEl = document.getElementById("status");
const tournamentRows = document.getElementById("tournament-rows");
const linkRows = document.getElementById("link-rows");
const activeHint = document.getElementById("active-tournament-hint");

function setStatus(message, type = "") {
  statusEl.textContent = message || "";
  statusEl.className = `status ${type}`.trim();
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderTournaments(data) {
  const items = data?.items || [];
  const activeCode = data?.activeCode || "";
  if (!items.length) {
    tournamentRows.innerHTML =
      '<tr><td colspan="5" class="empty">暂无已绑定赛事</td></tr>';
  } else {
    tournamentRows.innerHTML = items
      .map((item) => {
        const isActive = item.isActive;
        const badge = isActive ? '<span class="badge">当前</span>' : "";
        return `
          <tr class="${isActive ? "active-row" : ""}">
            <td>${escapeHtml(item.code)}${badge}</td>
            <td>${escapeHtml(item.name)}</td>
            <td>${escapeHtml(item.eventId)}</td>
            <td>${escapeHtml(item.phaseGroupId ?? "-")}</td>
            <td class="actions">
              ${
                isActive
                  ? ""
                  : `<button type="button" class="btn secondary small" data-action="activate" data-code="${escapeHtml(item.code)}">设为当前</button>`
              }
              <button type="button" class="btn danger small" data-action="unbind" data-code="${escapeHtml(item.code)}">解绑</button>
            </td>
          </tr>`;
      })
      .join("");
  }
  activeHint.textContent = activeCode
    ? `当前赛事：${activeCode}`
    : "当前赛事：未设置（将使用配置中的默认简码或第一个已绑定赛事）";
}

function renderLinks(items) {
  if (!items.length) {
    linkRows.innerHTML =
      '<tr><td colspan="7" class="empty">暂无已关联账号</td></tr>';
    return;
  }
  linkRows.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.qq_id)}</td>
        <td>${escapeHtml(item.startgg_code)}</td>
        <td>${escapeHtml(item.startgg_user_id)}</td>
        <td>${escapeHtml(item.startgg_player_id || "-")}</td>
        <td>${escapeHtml(item.gamer_tag || "-")}</td>
        <td>${escapeHtml(item.linked_at_text || "-")}</td>
        <td class="actions">
          <button type="button" class="btn danger small" data-action="delete-link" data-qq="${escapeHtml(item.qq_id)}">删除</button>
        </td>
      </tr>`
    )
    .join("");
}

async function loadTournaments() {
  const data = await bridge.apiGet("tournaments");
  renderTournaments(data);
}

async function loadLinks() {
  const data = await bridge.apiGet("account-links");
  renderLinks(data?.items || []);
}

async function bindTournament() {
  const reference = document.getElementById("bind-reference").value.trim();
  const code = document.getElementById("bind-code").value.trim();
  const pgidRaw = document.getElementById("bind-pgid").value.trim();
  if (!reference) {
    setStatus("请填写 eventId 或 start.gg 链接。", "err");
    return;
  }
  const body = { reference };
  if (code) body.code = code;
  if (pgidRaw) body.phaseGroupId = pgidRaw;
  await bridge.apiPost("tournaments/bind", body);
  document.getElementById("bind-reference").value = "";
  document.getElementById("bind-code").value = "";
  document.getElementById("bind-pgid").value = "";
  setStatus("赛事绑定成功。", "ok");
  await loadTournaments();
}

async function activateTournament(code) {
  await bridge.apiPost("tournaments/active", { code });
  setStatus(`已切换当前赛事为 ${code}。`, "ok");
  await loadTournaments();
}

async function unbindTournament(code) {
  if (!window.confirm(`确定解绑赛事「${code}」吗？`)) return;
  await bridge.apiPost("tournaments/unbind", { code });
  setStatus(`已解绑赛事 ${code}。`, "ok");
  await loadTournaments();
}

async function addLink() {
  const qq_id = document.getElementById("link-qq").value.trim();
  const startgg_code = document.getElementById("link-code").value.trim();
  if (!qq_id || !startgg_code) {
    setStatus("请填写 QQ 号与 start.gg 唯一代码。", "err");
    return;
  }
  await bridge.apiPost("account-links", { qq_id, startgg_code });
  document.getElementById("link-qq").value = "";
  document.getElementById("link-code").value = "";
  setStatus("账号关联已保存。", "ok");
  await loadLinks();
}

async function deleteLink(qq_id) {
  if (!window.confirm(`确定删除 QQ ${qq_id} 的关联吗？`)) return;
  await bridge.apiPost("account-links/remove", { qq_id });
  setStatus(`已删除 QQ ${qq_id} 的关联。`, "ok");
  await loadLinks();
}

async function exportLinks(format) {
  await bridge.download(
    "account-links/export",
    { format },
    `startgg_account_links.${format}`
  );
  setStatus(`已导出 ${format.toUpperCase()} 文件。`, "ok");
}

tournamentRows.addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  const code = btn.dataset.code;
  try {
    if (btn.dataset.action === "activate") {
      await activateTournament(code);
    } else if (btn.dataset.action === "unbind") {
      await unbindTournament(code);
    }
  } catch (error) {
    setStatus(error.message || "操作失败。", "err");
  }
});

linkRows.addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-action='delete-link']");
  if (!btn) return;
  try {
    await deleteLink(btn.dataset.qq);
  } catch (error) {
    setStatus(error.message || "删除失败。", "err");
  }
});

document.getElementById("btn-refresh-tournaments").addEventListener("click", () =>
  loadTournaments().catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-refresh-links").addEventListener("click", () =>
  loadLinks().catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-bind-tournament").addEventListener("click", () =>
  bindTournament().catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-add-link").addEventListener("click", () =>
  addLink().catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-export-txt").addEventListener("click", () =>
  exportLinks("txt").catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-export-csv").addEventListener("click", () =>
  exportLinks("csv").catch((e) => setStatus(e.message, "err"))
);
document.getElementById("btn-export-json").addEventListener("click", () =>
  exportLinks("json").catch((e) => setStatus(e.message, "err"))
);

await bridge.ready();
document.title = bridge.t("pages.manage.title", "赛事与账号管理");
document.getElementById("page-title").textContent = document.title;

try {
  await Promise.all([loadTournaments(), loadLinks()]);
  setStatus("就绪。");
} catch (error) {
  setStatus(error.message || "加载失败。", "err");
}
