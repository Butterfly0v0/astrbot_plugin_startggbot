const bridge = window.AstrBotPluginPage;
const statusEl = document.getElementById("status");
const tournamentRows = document.getElementById("tournament-rows");
const linkRows = document.getElementById("link-rows");
const backupRows = document.getElementById("backup-rows");
const dataDirHint = document.getElementById("data-dir-hint");
const activeHint = document.getElementById("active-tournament-hint");
const activeSelect = document.getElementById("active-tournament-select");
const confirmDialog = document.getElementById("confirm-dialog");
const confirmMessage = document.getElementById("confirm-message");
const confirmOkBtn = document.getElementById("confirm-ok");
const confirmCancelBtn = document.getElementById("confirm-cancel");

const DEFAULT_DOWNLOAD_LOCATION =
  "系统默认下载目录（Windows 通常为「用户\\下载」，macOS 为「下载」）";

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

function confirmAction(message) {
  return new Promise((resolve) => {
    confirmMessage.textContent = message;
    confirmDialog.classList.remove("hidden");

    const finish = (result) => {
      confirmDialog.classList.add("hidden");
      document.removeEventListener("keydown", onKeyDown);
      confirmOkBtn.removeEventListener("click", onOk);
      confirmCancelBtn.removeEventListener("click", onCancel);
      confirmDialog.removeEventListener("click", onBackdropClick);
      resolve(result);
    };

    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        finish(false);
      }
    };
    const onBackdropClick = (event) => {
      if (event.target?.dataset?.action === "cancel") {
        finish(false);
      }
    };

    confirmOkBtn.addEventListener("click", onOk);
    confirmCancelBtn.addEventListener("click", onCancel);
    confirmDialog.addEventListener("click", onBackdropClick);
    document.addEventListener("keydown", onKeyDown);
    confirmCancelBtn.focus();
  });
}

function formatDownloadStatus(filename, saveLocation) {
  return `已保存 ${filename}，保存位置：${saveLocation}`;
}

function triggerBrowserDownload(filename, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function base64ToBlob(base64, contentType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: contentType || "application/octet-stream" });
}

async function downloadViaBridge(endpoint, params, fallbackFilename) {
  if (typeof bridge.download === "function") {
    try {
      const result = await bridge.download(endpoint, params, fallbackFilename);
      return {
        filename: result?.filename || fallbackFilename,
        saveLocation: DEFAULT_DOWNLOAD_LOCATION,
      };
    } catch (error) {
      const message = String(error?.message || "");
      if (!message.includes("Unsupported plugin bridge action")) {
        throw error;
      }
    }
  }

  const contentEndpoint = `${endpoint}/content`;
  const data = await bridge.apiGet(contentEndpoint, params);
  const filename = data?.filename || fallbackFilename;
  let blob;
  if (data?.contentBase64) {
    blob = base64ToBlob(data.contentBase64, data.contentType);
  } else {
    blob = new Blob([data?.content ?? ""], {
      type: data?.contentType || "text/plain;charset=utf-8",
    });
  }
  triggerBrowserDownload(filename, blob);
  return {
    filename,
    saveLocation: DEFAULT_DOWNLOAD_LOCATION,
  };
}

function renderTournamentSelect(items, activeCode) {
  if (!items.length) {
    activeSelect.innerHTML = '<option value="">暂无已绑定赛事</option>';
    activeSelect.disabled = true;
    return;
  }
  activeSelect.disabled = false;
  activeSelect.innerHTML = items
    .map((item) => {
      const label = `${item.code} — ${item.name}`;
      const selected = item.code === activeCode ? " selected" : "";
      return `<option value="${escapeHtml(item.code)}"${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");
  if (activeCode && !items.some((item) => item.code === activeCode)) {
    activeSelect.value = items[0].code;
  }
}

function renderTournaments(data) {
  const items = data?.items || [];
  const activeCode = data?.activeCode || "";
  renderTournamentSelect(items, activeCode);
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
      '<tr><td colspan="8" class="empty">暂无已关联账号</td></tr>';
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
        <td>${escapeHtml(item.prefix || "-")}</td>
        <td>${escapeHtml(item.gamer_tag || "-")}</td>
        <td>${escapeHtml(item.linked_at_text || "-")}</td>
        <td class="actions">
          <button type="button" class="btn secondary small" data-action="refresh-link" data-qq="${escapeHtml(item.qq_id)}">更新选手信息</button>
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

function renderBackups(data) {
  const items = data?.items || [];
  if (data?.dataDir) {
    dataDirHint.textContent = `数据目录：${data.dataDir}`;
  }
  if (!items.length) {
    backupRows.innerHTML =
      '<tr><td colspan="5" class="empty">暂无备份，建议更新前点击「立即备份」</td></tr>';
    return;
  }
  backupRows.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.created_at_text || "-")}</td>
        <td>${escapeHtml(item.file_count ?? 0)}</td>
        <td>${escapeHtml(item.operator || "-")}</td>
        <td class="actions">
          <button type="button" class="btn secondary small" data-action="download-backup" data-id="${escapeHtml(item.id)}">下载</button>
          <button type="button" class="btn danger small" data-action="restore-backup" data-id="${escapeHtml(item.id)}">恢复</button>
          <button type="button" class="btn danger small" data-action="delete-backup" data-id="${escapeHtml(item.id)}">删除</button>
        </td>
      </tr>`
    )
    .join("");
}

async function loadBackups() {
  const data = await bridge.apiGet("data/backups");
  renderBackups(data);
}

async function createBackup() {
  const data = await bridge.apiPost("data/backups/create", {});
  setStatus(`备份已创建：${data?.backup?.id || ""}`, "ok");
  await loadBackups();
}

async function restoreBackup(backupId) {
  const confirmed = await confirmAction(
    `确定从备份「${backupId}」恢复数据吗？\n当前运行时数据将被覆盖（赛事绑定、账号关联、报分开关等）。`
  );
  if (!confirmed) {
    return;
  }
  const data = await bridge.apiPost("data/backups/restore", { backup_id: backupId });
  const restored = (data?.restored || []).join(", ") || "无";
  setStatus(`已从备份 ${backupId} 恢复：${restored}`, "ok");
  await Promise.all([loadTournaments(), loadLinks(), loadBackups()]);
}

async function downloadBackup(backupId) {
  const { filename, saveLocation } = await downloadViaBridge(
    "data/backups/download",
    { backup_id: backupId },
    `startggbot-backup-${backupId}.zip`
  );
  setStatus(formatDownloadStatus(filename, saveLocation), "ok");
}

async function deleteBackup(backupId) {
  const confirmed = await confirmAction(
    `确定删除备份「${backupId}」吗？\n此操作不可撤销，备份文件将被永久删除。`
  );
  if (!confirmed) {
    return;
  }
  await bridge.apiPost("data/backups/delete", { backup_id: backupId });
  setStatus(`已删除备份 ${backupId}。`, "ok");
  await loadBackups();
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
  if (!code) {
    setStatus("请先选择要切换的赛事。", "err");
    return;
  }
  await bridge.apiPost("tournaments/active", { code });
  setStatus(`已切换当前赛事为 ${code}。`, "ok");
  await loadTournaments();
}

async function activateSelectedTournament() {
  const code = activeSelect.value.trim();
  await activateTournament(code);
}

async function unbindTournament(code) {
  const confirmed = await confirmAction(`确定解绑赛事「${code}」吗？`);
  if (!confirmed) return;
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

async function refreshLink(qq_id) {
  await bridge.apiPost("account-links/refresh", { qq_id });
  setStatus(`已更新 QQ ${qq_id} 的选手名与前缀。`, "ok");
  await loadLinks();
}

async function deleteLink(qq_id) {
  const confirmed = await confirmAction(`确定删除 QQ ${qq_id} 的关联吗？`);
  if (!confirmed) return;
  await bridge.apiPost("account-links/remove", { qq_id });
  setStatus(`已删除 QQ ${qq_id} 的关联。`, "ok");
  await loadLinks();
}

async function exportLinks(format) {
  const { filename, saveLocation } = await downloadViaBridge(
    "account-links/export",
    { format },
    `startgg_account_links.${format}`
  );
  setStatus(formatDownloadStatus(filename, saveLocation), "ok");
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
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  try {
    if (btn.dataset.action === "refresh-link") {
      await refreshLink(btn.dataset.qq);
    } else if (btn.dataset.action === "delete-link") {
      await deleteLink(btn.dataset.qq);
    }
  } catch (error) {
    setStatus(error.message || "操作失败。", "err");
  }
});

backupRows.addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  const backupId = btn.dataset.id;
  try {
    if (btn.dataset.action === "download-backup") {
      await downloadBackup(backupId);
    } else if (btn.dataset.action === "restore-backup") {
      await restoreBackup(backupId);
    } else if (btn.dataset.action === "delete-backup") {
      await deleteBackup(backupId);
    }
  } catch (error) {
    setStatus(error.message || "操作失败。", "err");
  }
});

document.getElementById("btn-set-active-tournament").addEventListener("click", () =>
  activateSelectedTournament().catch((e) => setStatus(e.message, "err"))
);
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
document.getElementById("btn-create-backup").addEventListener("click", () =>
  createBackup().catch((e) => setStatus(e.message, "err"))
);

await bridge.ready();
document.title = bridge.t("pages.manage.title", "赛事与账号管理");
document.getElementById("page-title").textContent = document.title;

try {
  await Promise.all([loadTournaments(), loadLinks(), loadBackups()]);
  setStatus("就绪。");
} catch (error) {
  setStatus(error.message || "加载失败。", "err");
}
