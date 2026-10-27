"use strict";

// Показ готового отчёта: разделы сводки Markdown вкладками, плитки, графики и тепловые карты (charts.js),
// файлы для скачивания. Общий для страницы сервиса (app.js) и страницы по ссылке (share.js).
const STATUS_CLASSES = { "●": "s-ok", "▲": "s-warn", "✕": "s-crit", "○": "s-none" };
let reportData = null;  // данные графиков из attestation_report.json; без них — только таблицы

function formatDate(seconds) {
  return new Date(seconds * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

// срок ссылки на результаты: null — постоянная, до отзыва
function lifetimeText(expires) {
  return expires === null ? "постоянная, открывается до отзыва" : `открывается до ${formatDate(expires)}`;
}

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function inline(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}

function renderTable(lines) {
  const rows = lines.filter((line, index) => index !== 1).map(line => line.replace(/^\||\|$/g, "").split("|").map(cell => cell.trim()));
  const [head, ...body] = rows;
  const th = head.map(cell => `<th>${inline(cell)}</th>`).join("");
  const tr = body.map(cells => "<tr>" + cells.map(cell => {
    const status = STATUS_CLASSES[cell.charAt(0)];
    const numeric = /^[−+\-–]?[\d,]+( %| из \d+)?$|^\d+ из \d+$/.test(cell);
    const kind = status || (numeric ? "num" : cell.length > 30 ? "wide" : "");
    return `<td class="${kind}">${inline(cell)}</td>`;
  }).join("") + "</tr>").join("");
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
}

// Разбор того подмножества Markdown, которое пишет build_markdown_report.py
function renderMarkdown(markdown) {
  const html = [];
  const lines = markdown.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("|")) {
      const block = [];
      while (i < lines.length && lines[i].startsWith("|")) block.push(lines[i++]);
      i--;
      html.push(renderTable(block));
    } else if (line.startsWith("- ")) {
      const items = [];
      while (i < lines.length && lines[i].startsWith("- ")) items.push(`<li>${inline(lines[i++].slice(2))}</li>`);
      i--;
      html.push(`<ul>${items.join("")}</ul>`);
    } else if (line.startsWith("### ")) {
      html.push(`<h3>${inline(line.slice(4))}</h3>`);
    } else if (line.trim()) {
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  return html.join("");
}

function splitSections(markdown) {
  const [header, ...parts] = markdown.split(/^## /m);
  const headerLines = header.trim().split("\n");
  return {
    title: headerLines[0].replace(/^# /, ""),
    intro: headerLines.slice(1).join(" ").trim(),
    sections: parts.map(part => {
      const [name, ...rest] = part.split("\n");
      const body = rest.join("\n");
      const cards = body.split(/^(?=### )/m).filter(chunk => chunk.trim());
      return { name: name.trim(), body, cards };
    }),
  };
}

function showSection(section) {
  const toolbar = document.getElementById("toolbar");
  const target = document.getElementById("report-body");
  toolbar.innerHTML = "";
  const isCards = section.name === "Группы" || section.name === "Преподаватели";
  if (!isCards) {
    // плитки заменяют строку «Студентов: … · групп: …», дальше — выводы и таблицы как есть
    const body = reportData && section.name === "Сводка"
      ? section.body.split("\n").filter(line => !line.startsWith("Студентов: ")).join("\n")
      : section.body;
    target.innerHTML = renderMarkdown(body);
    if (reportData && section.name === "Сводка") {
      target.prepend(renderSummaryTiles(reportData), renderGroupChart(reportData));
    }
    if (section.name === "Дисциплины") addStatusFilter(toolbar, target);
    return;
  }
  const cards = section.cards.map(chunk => `<div class="card">${renderMarkdown(chunk)}</div>`);
  target.innerHTML = cards.join("");
  if (reportData && section.name === "Группы") addHeatmaps(target);
  const control = section.name === "Группы" ? document.createElement("select") : document.createElement("input");
  if (section.name === "Группы") {
    control.innerHTML = `<option value="">Все группы</option>` +
      section.cards.map((chunk, index) => `<option value="${index}">${escapeHtml(chunk.split("\n")[0].replace("### ", ""))}</option>`).join("");
    control.onchange = () => target.querySelectorAll(".card").forEach((card, index) => {
      card.hidden = control.value !== "" && Number(control.value) !== index;
    });
  } else {
    control.type = "search";
    control.placeholder = "Фамилия или кафедра";
    control.oninput = () => target.querySelectorAll(".card").forEach(card => {
      card.hidden = !card.querySelector("h3").textContent.toLowerCase().includes(control.value.toLowerCase());
    });
  }
  toolbar.appendChild(control);
}

function addStatusFilter(toolbar, target) {
  const active = new Set(["s-ok", "s-warn", "s-crit", "s-none"]);
  const chips = [["s-crit", "✕ критично"], ["s-warn", "▲ внимание"], ["s-ok", "● норма"]];
  if (target.querySelector("td.s-none")) chips.push(["s-none", "○ не выставлено"]);
  chips.forEach(([cls, label]) => {
    const chip = document.createElement("button");
    chip.className = "chip on";
    chip.textContent = label;
    chip.onclick = () => {
      active.has(cls) ? active.delete(cls) : active.add(cls);
      chip.classList.toggle("on");
      target.querySelectorAll("tbody tr").forEach(row => {
        const statusCell = row.querySelector("td.s-ok, td.s-warn, td.s-crit, td.s-none");
        row.hidden = statusCell && !active.has(statusCell.className);
      });
    };
    toolbar.appendChild(chip);
  });
}

function addHeatmaps(target) {
  const byName = new Map(reportData.groups.map(group => [group.name, group]));
  target.querySelectorAll(".card").forEach(card => {
    const title = card.querySelector("h3");
    const group = title && byName.get(title.textContent.split(" · ")[0].trim());
    if (!group || !group.students_scores.length) return;
    const stats = title.nextElementSibling;
    (stats || title).after(renderHeatmap(group, reportData));
  });
}

function renderDownloads(target, downloads, fetchFile) {
  // один документ — одна строка, форматы рядом: «Краткий отчёт: бакалавриат  PDF · 180 КБ  DOCX · 71 КБ»;
  // fetchFile — для файлов, которые отдаются только с заголовком (страница по ссылке)
  target.replaceChildren(...downloads.map(item => {
    const row = el("div", "download");
    row.appendChild(el("span", "download-name", item.label));
    item.files.forEach(file => {
      const link = el("a", file.format === "PDF" ? "download-file main" : "download-file");
      link.href = file.href;
      link.setAttribute("download", file.name || "");
      if (fetchFile) {
        link.addEventListener("click", event => {
          event.preventDefault();
          fetchFile(file);
        });
      }
      link.append(el("b", "", file.format), el("span", "", file.size));
      row.appendChild(link);
    });
    return row;
  }));
}

function showReport(markdown, downloads, data, fetchFile) {
  reportData = data || null;
  const report = splitSections(markdown);
  document.getElementById("report-title").textContent = report.title;
  document.getElementById("report-intro").textContent = report.intro;
  renderDownloads(document.getElementById("downloads"), downloads, fetchFile);
  const tabs = document.getElementById("report-tabs");
  tabs.innerHTML = "";
  report.sections.forEach((section, index) => {
    const button = document.createElement("button");
    button.textContent = section.name;
    button.onclick = () => {
      tabs.querySelectorAll("button").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      showSection(section);
    };
    tabs.appendChild(button);
    if (index === 0) button.click();
  });
  document.getElementById("result").hidden = false;
  document.getElementById("result").scrollIntoView({ behavior: "smooth" });
}
