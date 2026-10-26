"use strict";

// Страница по ссылке: результаты отчёта без входа. Ключ доступа — в адресе после «#»: браузер не
// отправляет эту часть адреса на сервер, поэтому ключ не попадает в журналы, а страница передаёт его
// заголовком X-Share-Token. Файлы по той же причине скачиваются запросом с заголовком, а не ссылкой.
const BASE = "/attestation";
const token = decodeURIComponent(window.location.hash.slice(1));
const $ = id => document.getElementById(id);

async function shared(path) {
  const response = await fetch(path, { headers: { "X-Share-Token": token }, credentials: "omit", cache: "no-store" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Не удалось открыть результаты.");
  }
  return response;
}

async function download(file) {
  $("share-error").textContent = "";
  try {
    const url = URL.createObjectURL(await (await shared(file.href)).blob());
    const link = el("a");
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (exc) {
    $("share-error").textContent = exc.message;
  }
}

async function start() {
  if (!token) throw new Error("В ссылке нет ключа доступа. Скопируйте ссылку целиком, вместе с частью после «#».");
  const report = await (await shared(`${BASE}/api/shared`)).json();
  showReport(report.markdown, report.downloads, report.data, download);
  document.title = `${report.title} — результаты аттестации`;
  $("share-until").textContent = `Ссылка открывается до ${formatDate(report.expires)}.`;
  $("stamp-department").textContent = report.data ? report.data.department : "—";
  $("stamp-period").textContent = report.data ? report.data.title : "—";
  $("stamp-date").textContent = new Date(report.created * 1000).toLocaleDateString("ru-RU");
}

// другая ссылка в той же вкладке меняет только часть после «#», и браузер страницу не перезагружает
window.addEventListener("hashchange", () => window.location.reload());

document.addEventListener("DOMContentLoaded", () => {
  start()
    .catch(exc => { $("share-error").textContent = exc.message; })
    .finally(() => { $("share-loading").hidden = true; });
});
