"use strict";

// Страница сервиса аттестации: выбор кафедры и групп, запуск задачи, опрос её состояния, показ отчёта
// (сам показ — в report.js, его же использует страница по ссылке), ссылки на результаты.
const BASE = "/attestation";
const LEVEL_TITLES = { "б": "Бакалавриат", "м": "Магистратура" };
const LAST_DEPARTMENT = "attestation.department";
let csrf = "";
let faculties = [];
let currentJob = null;  // готовая задача, которой можно поделиться ссылкой

function fillYears(current) {
  const select = document.getElementById("year");
  for (let year = current; year >= current - 4; year--) select.add(new Option(`${year}-${year + 1}`, year));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(BASE + path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) {
    window.location.href = BASE + "?error=expired";
    throw new Error("Сеанс завершён.");
  }
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Сервер не смог выполнить запрос.");
  return data;
}

function remembered(key) {
  try { return window.localStorage.getItem(key) || ""; } catch (exc) { return ""; }
}

function remember(key, value) {
  try { window.localStorage.setItem(key, value); } catch (exc) { /* only a convenience */ }
}

const $ = id => document.getElementById(id);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function periodQuery() {
  return new URLSearchParams({
    year: $("year").value,
    odd_semester: $("semester").value === "odd",
    attestation: $("att").value,
  });
}

function selectedText(id) {
  const select = $(id);
  return select.selectedIndex >= 0 && select.value ? select.options[select.selectedIndex].text : "—";
}

function updateStamp() {
  // основная надпись внизу листа: что за документ, чей и за какой период
  $("stamp-department").textContent = selectedText("department");
  $("stamp-period").textContent = `№${NBSP}${$("att").value}, ${selectedText("semester")}, ${selectedText("year")}`;
}

function showError(exc) {
  $("form-error").textContent = exc.message;
}

function fillSelect(select, items, placeholder, selected) {
  select.innerHTML = "";
  select.add(new Option(placeholder, ""));
  items.forEach(item => select.add(new Option(item.label, item.value)));
  select.value = items.some(item => item.value === selected) ? selected : "";
}

async function loadPeriodLists() {
  const query = periodQuery();
  const department = $("department").value || remembered(LAST_DEPARTMENT);
  const faculty = $("faculty").value;
  $("department").innerHTML = '<option value="">загрузка списка кафедр…</option>';
  const [facultyList, departments] = await Promise.all([
    api(`/api/faculties?${query}`),
    api(`/api/departments?${query}`),
  ]);
  faculties = facultyList;
  fillSelect($("faculty"), faculties.map(item => ({ value: item.id, label: item.label })), "выберите факультет", faculty);
  fillSelect($("department"), departments.map(name => ({ value: name, label: name })), "выберите кафедру", department);
  if ($("department").value && !faculty) await applyPreset();
  else await loadDirections(checkedCodes());
}

function selectedDirections() {
  return Array.from(document.querySelectorAll("#directions .direction")).filter(row => row.querySelector(".group-box:checked"));
}

function checkedCodes() {
  return selectedDirections().map(row => row.querySelector(".direction-box").value);
}

function byGroups() {
  return document.querySelector('input[name="scope"]:checked').value === "groups";
}

// Пусто в отчёте по направлениям: тогда в него попадают все группы направлений, и новые тоже
function checkedGroups() {
  if (!byGroups()) return [];
  return Array.from(document.querySelectorAll("#directions .group-box:checked"), box => box.value);
}

function showScope() {
  const groups = byGroups();
  $("directions-fieldset").classList.toggle("by-groups", groups);
  if (groups) return;
  // по направлениям: направление, в котором отмечена хоть одна группа, берётся целиком
  document.querySelectorAll("#directions .direction").forEach(row => {
    const any = Boolean(row.querySelector(".group-box:checked"));
    row.querySelectorAll(".group-box").forEach(box => { box.checked = any; });
    const box = row.querySelector(".direction-box");
    box.checked = any;
    box.indeterminate = false;
  });
}

function directionRow(item, checked) {
  const row = el("div", "direction");
  const head = el("label", "direction-head");
  const box = el("input", "direction-box");
  box.type = "checkbox";
  box.value = item.code;
  head.append(box, el("b", "", item.code));
  const list = el("div", "group-list");
  const groupBoxes = item.groups.map(name => {
    const chip = el("label", "group-chip");
    const groupBox = el("input", "group-box");
    groupBox.type = "checkbox";
    groupBox.value = name;
    groupBox.checked = checked;
    chip.append(groupBox, el("span", "", name));
    list.appendChild(chip);
    return groupBox;
  });
  // отметка направления — все его группы; снятые отметки с групп — отчёт только по оставшимся
  const sync = () => {
    const count = groupBoxes.filter(groupBox => groupBox.checked).length;
    box.checked = count === groupBoxes.length;
    box.indeterminate = count > 0 && count < groupBoxes.length;
  };
  box.addEventListener("change", () => {
    groupBoxes.forEach(groupBox => { groupBox.checked = box.checked; });
    sync();
  });
  groupBoxes.forEach(groupBox => groupBox.addEventListener("change", sync));
  sync();
  row.append(head, list);
  return row;
}

async function applyPreset() {
  const department = $("department").value;
  if (!department) return;
  remember(LAST_DEPARTMENT, department);
  const preset = await api(`/api/preset?${new URLSearchParams({ department })}`);
  if (preset) {
    if (faculties.some(item => item.id === preset.faculty)) $("faculty").value = preset.faculty;
    $("faculty-title").value = preset.faculty_title || "";
    $("head-name").value = preset.head_name || "";
  } else {
    $("head-name").value = "";
  }
  await loadDirections(preset ? preset.codes : []);
}

async function loadDirections(codes) {
  const target = $("directions");
  const faculty = $("faculty").value;
  if (!faculty) {
    target.innerHTML = '<p class="hint">Сначала выберите факультет.</p>';
    return;
  }
  if (!$("faculty-title").value) {
    const item = faculties.find(entry => entry.id === faculty);
    $("faculty-title").value = item ? item.label : "";
  }
  target.innerHTML = '<p class="hint">Загружаю группы факультета…</p>';
  const query = periodQuery();
  query.set("faculty", faculty);
  const directions = await api(`/api/groups?${query}`);
  if (!directions.length) {
    target.innerHTML = '<p class="hint">У факультета нет групп с аттестацией за этот период.</p>';
    return;
  }
  const wanted = new Set(codes);
  target.innerHTML = "";
  ["б", "м"].forEach(level => {
    const items = directions.filter(item => item.level === level);
    if (!items.length) return;
    const title = document.createElement("h4");
    title.textContent = LEVEL_TITLES[level];
    const list = el("div", "direction-list");
    items.forEach(item => list.appendChild(directionRow(item, wanted.has(item.code))));
    target.append(title, list);
  });
}

async function waitForJob(job) {
  const progress = $("progress");
  progress.hidden = false;
  while (job.status === "running") {
    progress.textContent = job.progress.length ? job.progress.join("\n") : "Начинаю выгрузку…";
    await sleep(2000);
    job = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
  }
  progress.textContent = job.progress.join("\n");
  if (job.status === "failed") throw new Error(job.error || "Не удалось сформировать отчёт.");
  return job;
}

async function requestReport(event) {
  event.preventDefault();
  $("form-error").textContent = "";
  const codes = checkedCodes();
  if (!$("department").value || !$("faculty").value) {
    $("form-error").textContent = "Выберите кафедру и факультет.";
    return;
  }
  if (!codes.length) {
    $("form-error").textContent = byGroups() ? "Отметьте хотя бы одну группу." : "Отметьте хотя бы одно направление групп.";
    return;
  }
  const body = {
    year: Number($("year").value),
    odd_semester: $("semester").value === "odd",
    attestation: Number($("att").value),
    faculty: $("faculty").value,
    department: $("department").value,
    codes,
    groups: checkedGroups(),
    faculty_title: $("faculty-title").value.trim(),
    head_name: $("head-name").value.trim(),
  };
  const button = $("submit");
  button.disabled = true;
  button.textContent = "Формирую отчёт, это займёт несколько минут…";
  $("result").hidden = true;
  currentJob = null;
  $("share-made").hidden = true;
  $("share-error").textContent = "";
  try {
    currentJob = await waitForJob(await api("/api/jobs", { method: "POST", body: JSON.stringify(body) }));
    showReport(currentJob.markdown, currentJob.downloads, currentJob.data);
  } catch (exc) {
    showError(exc);
  } finally {
    button.disabled = false;
    button.textContent = "Сформировать отчёт";
  }
}

function daysLabel(days) {
  const last = days % 10, tens = days % 100;
  const word = last === 1 && tens !== 11 ? "день" : last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? "дня" : "дней";
  return `${days} ${word}`;
}

function renderShares(shares) {
  $("shares").hidden = !shares.length;
  $("share-list").replaceChildren(...shares.map(share => {
    const row = el("div", "share-item");
    const text = el("div", "share-text");
    text.append(el("span", "share-title", share.title), el("small", "", `открывается до ${formatDate(share.expires)}`));
    const revoke = el("button", "link-btn", "Отозвать");
    revoke.type = "button";
    revoke.addEventListener("click", guarded(async () => {
      revoke.disabled = true;
      await api(`/api/shares/${encodeURIComponent(share.id)}`, { method: "DELETE" });
      await loadShares();
    }));
    row.append(text, revoke);
    return row;
  }));
}

async function loadShares() {
  const { lifetimes, shares } = await api("/api/shares");
  const select = $("share-days");
  if (!select.options.length) lifetimes.forEach(days => select.add(new Option(daysLabel(days), days)));
  renderShares(shares);
}

async function createShare() {
  $("share-error").textContent = "";
  if (!currentJob) return;
  const button = $("share-create");
  button.disabled = true;
  try {
    const share = await api(`/api/jobs/${encodeURIComponent(currentJob.id)}/share`, {
      method: "POST",
      body: JSON.stringify({ days: Number($("share-days").value) }),
    });
    $("share-url").value = new URL(share.link, window.location.origin).href;
    $("share-until").textContent = `Открывается до ${formatDate(share.expires)}. Ссылка показывается только сейчас — сохраните её.`;
    $("share-copy").textContent = "Копировать";
    $("share-made").hidden = false;
    $("share-url").select();
    await loadShares();
  } catch (exc) {
    $("share-error").textContent = exc.message;
  } finally {
    button.disabled = false;
  }
}

async function copyShare() {
  $("share-url").select();
  try {
    await navigator.clipboard.writeText($("share-url").value);
    $("share-copy").textContent = "Скопировано";
  } catch (exc) {
    $("share-error").textContent = "Браузер не дал скопировать: ссылка выделена, скопируйте её сами (Ctrl+C).";
  }
}

async function logout() {
  try {
    await api("/logout", { method: "POST" });
  } finally {
    window.location.href = BASE;
  }
}

function guarded(action) {
  return () => {
    $("form-error").textContent = "";
    action().catch(showError);
  };
}

async function start() {
  // the login redirect leaves «?error=none» behind; it only matters when the cookie is lost
  if (window.location.search) window.history.replaceState(null, "", BASE);
  const me = await api("/api/me");
  csrf = me.csrf;
  $("who").textContent = me.login;
  $("stamp-user").textContent = me.login;
  $("stamp-date").textContent = new Date().toLocaleDateString("ru-RU");
  fillYears(me.year);
  $("semester").value = me.odd_semester ? "odd" : "even";
  ["year", "semester", "att"].forEach(id => $(id).addEventListener("change", guarded(loadPeriodLists)));
  $("department").addEventListener("change", guarded(applyPreset));
  $("faculty").addEventListener("change", () => {
    $("faculty-title").value = "";
    guarded(() => loadDirections(checkedCodes()))();
  });
  $("logout").addEventListener("click", logout);
  $("report-form").addEventListener("submit", requestReport);
  document.querySelectorAll('input[name="scope"]').forEach(radio => radio.addEventListener("change", showScope));
  $("share-create").addEventListener("click", createShare);
  $("share-copy").addEventListener("click", copyShare);
  ["year", "semester", "att", "department"].forEach(id => $(id).addEventListener("change", updateStamp));
  loadShares().catch(showError);
  await loadPeriodLists();
  updateStamp();
}

document.addEventListener("DOMContentLoaded", () => {
  start().catch(showError);
});
