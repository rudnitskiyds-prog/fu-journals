(function () {
  "use strict";

  const DATA = window.FU_DATA || { meta: {}, journals: [] };
  const CHANGES = window.FU_CHANGES || [];
  const J = DATA.journals;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lower = (s) => String(s || "").toLowerCase().replace(/ё/g, "е");
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "—";

  // ---------- шапка и счётчики
  const m = DATA.meta;
  $("#meta").textContent = `${m.list_edition || ""} · ${m.total || J.length} журналов · обновлено ${fmtDate(m.updated)}`;

  const count = (fn) => J.filter(fn).length;
  const STATS = [
    ["ЕГПНИ, уровень 1–2", (j) => [1, 2].includes(j.egpni?.level), { "f-egpni": "12" }],
    ["В перечне ВАК", (j) => j.vak?.in, { "f-vak": "in" }],
    ["Scopus", (j) => j.scopus?.in, { "f-scopus": "in" }],
    ["WoS CC", (j) => j.wos?.in, { "f-wos": "in" }],
    ["Ядро РИНЦ", (j) => j.rsci, { "f-rsci": "1" }],
    ["Публикация бесплатна", isFree, { "f-price": "free" }],
  ];
  $("#stats").innerHTML = STATS.map(([label, fn], i) =>
    `<button type="button" class="stat" data-i="${i}"><b>${count(fn)}</b><span>${label}</span></button>`).join("");
  $("#stats").addEventListener("click", (e) => {
    const b = e.target.closest(".stat"); if (!b) return;
    resetFilters(false);
    for (const [id, v] of Object.entries(STATS[b.dataset.i][2])) $("#" + id).value = v;
    render();
  });

  function isFree(j) {
    return /бесплатн|не взима/i.test(j.price?.text || "");
  }

  // ---------- фильтры
  const FIELDS = ["q", "f-egpni", "f-vak", "f-scopus", "f-wos", "f-rsci", "f-price", "f-spec", "sort"];
  function state() {
    const s = {}; FIELDS.forEach((id) => (s[id] = $("#" + id).value.trim())); return s;
  }
  function matches(j, s) {
    if (s.q) {
      const hay = lower([j.name, j.egpni_title, j.publisher, ...(j.issns || [])].join(" "));
      if (!lower(s.q).split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    const lv = j.egpni?.level;
    if (s["f-egpni"] === "none" ? lv : s["f-egpni"] === "12" ? ![1, 2].includes(lv) : s["f-egpni"] && lv !== +s["f-egpni"]) return false;
    const v = s["f-vak"];
    if (v === "in" && !j.vak?.in) return false;
    if (v === "out" && j.vak?.in) return false;
    if (/^К/.test(v) && j.vak?.category !== v) return false;
    const sc = s["f-scopus"];
    if (sc === "in" && !j.scopus?.in) return false;
    if (/^Q/.test(sc) && j.scopus?.quartile !== sc) return false;
    if (s["f-wos"] && !j.wos?.in) return false;
    if (s["f-rsci"] && !j.rsci) return false;
    if (s["f-price"] === "free" && !isFree(j)) return false;
    if (s["f-price"] === "known" && !j.price && !(j.price_mentions || []).length) return false;
    if (s["f-spec"] && !(j.vak?.specialties || []).some((x) => lower(x).includes(lower(s["f-spec"])))) return false;
    return true;
  }
  const rankQ = (q) => (q ? +q.slice(1) : 9);
  const SORTS = {
    name: (a, b) => a.name.localeCompare(b.name, "ru"),
    egpni: (a, b) => (a.egpni?.level || 9) - (b.egpni?.level || 9) || SORTS.name(a, b),
    scopus: (a, b) => rankQ(a.scopus?.quartile) - rankQ(b.scopus?.quartile) || (b.scopus?.in - a.scopus?.in) || SORTS.name(a, b),
    vak: (a, b) => (a.vak?.category || (a.vak?.in ? "К8" : "К9")).localeCompare(b.vak?.category || (b.vak?.in ? "К8" : "К9")) || SORTS.name(a, b),
  };

  // ---------- ячейки
  const dash = '<span class="dash">—</span>';
  const lvl = (j) => j.egpni?.level ? `<span class="lvl lvl-${j.egpni.level}">Ур. ${j.egpni.level}</span>` : dash;
  const vak = (j) => j.vak?.category ? `<span class="tag">${esc(j.vak.category)}</span>` : j.vak?.in ? '<span class="yes">да</span>' : dash;
  const scopus = (j) => !j.scopus?.in ? dash : j.scopus.quartile
    ? `<span class="q q-${j.scopus.quartile}" title="${esc(j.scopus.quartile_src)} ${j.scopus.year}">${j.scopus.quartile}</span>`
    : '<span class="yes">да</span>';
  const wos = (j) => !j.wos?.in ? dash : j.wos.quartile ? `<span class="q q-${esc(j.wos.quartile)}">${esc(j.wos.quartile)}</span>` : '<span class="yes" title="Web of Science Core Collection, квартиль не указан">да</span>';
  const yes = (b) => (b ? '<span class="yes">да</span>' : dash);
  const price = (j) => j.price ? (isFree(j) ? '<span class="yes">бесплатно</span>' : esc(shortPrice(j.price.text)))
    : (j.price_mentions || []).length ? '<span class="tag src-auto">см. карточку</span>' : dash;
  const shortPrice = (t) => t.replace(/\s*\(.*\)\s*$/, "");

  let shown = [];
  function render() {
    const s = state();
    shown = J.filter((j) => matches(j, s)).sort(SORTS[s.sort] || SORTS.name);
    $("#count").textContent = `Показано ${shown.length} из ${J.length}`;
    $("#empty").hidden = shown.length > 0;
    $("#grid tbody").innerHTML = shown.map((j) => `
      <tr data-id="${j.id}" tabindex="0">
        <td><div class="jname">${esc(j.name)}</div><div class="jsub">${esc((j.issns || []).join(", "))}${j.publisher ? " · " + esc(j.publisher) : ""}</div></td>
        <td data-l="ЕГПНИ">${lvl(j)}</td><td data-l="ВАК">${vak(j)}</td><td data-l="Scopus">${scopus(j)}</td>
        <td data-l="WoS">${wos(j)}</td><td data-l="Ядро РИНЦ">${yes(j.rsci)}</td><td data-l="РИНЦ">${yes(j.rinc)}</td>
        <td data-l="Стоимость">${price(j)}</td>
      </tr>`).join("");
    try { localStorage.setItem("fu-filters", JSON.stringify(s)); } catch (e) { /* без хранилища — не страшно */ }
  }
  function resetFilters(rerender = true) {
    FIELDS.forEach((id) => ($("#" + id).value = id === "sort" ? "name" : ""));
    if (rerender) render();
  }

  // ---------- карточка
  const srcTag = (src) => src === "manual" ? '<span class="tag src-manual">проверено</span>' : src === "doaj" ? '<span class="tag src-doaj">DOAJ</span>' : src === "site" ? '<span class="tag src-auto">с сайта</span>' : "";
  const link = (u, t) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(t || u)}</a>`;
  const mentions = (list) => (list || []).map((x) =>
    `<div class="mention">«${esc(x.text)}» <span class="tag src-auto">с сайта</span> ${link(x.url, "источник")}</div>`).join("");

  function openCard(id) {
    const j = J.find((x) => x.id === id); if (!j) return;
    const e = j.egpni;
    const areas = (j.scopus?.areas || []).map((a) => `${esc(a.area)}: ${a.q}`).join(" · ");
    const howto = Array.isArray(j.how_to_submit) ? j.how_to_submit : [j.how_to_submit];
    $("#card article").innerHTML = `
      <div class="card-head"><h2>${esc(j.name)}</h2><button class="close" aria-label="Закрыть">×</button></div>
      <div class="jsub">${esc((j.issns || []).join(", ") || "ISSN не найден")}${j.publisher ? " · " + esc(j.publisher) : ""}</div>
      <div class="kv">
        <div><small>ЕГПНИ / Белый список</small>${e ? (e.level ? `${lvl(j)} <span class="muted">(${e.level_year})</span>` : "входит, уровень не присвоен") : "не найден"}
          ${e?.discontinued ? `<div class="jsub">исключён ${esc(e.discontinued)}</div>` : ""}</div>
        <div><small>ВАК</small>${j.vak?.in ? (j.vak.category ? `категория ${esc(j.vak.category)}` : "в перечне, категория не загружена") : "нет сведений"}</div>
        <div><small>Scopus</small>${scopus(j)} ${j.scopus?.year ? `<span class="muted">${esc(j.scopus.quartile_src)} ${j.scopus.year}</span>` : ""}</div>
        <div><small>Web of Science</small>${wos(j)}</div>
        <div><small>Ядро РИНЦ (RSCI)</small>${j.rsci ? "да" : "нет"}</div>
        <div><small>РИНЦ</small>${j.rinc ? "да" : "нет"}</div>
      </div>
      ${areas ? `<div class="areas">Квартили Scopus по областям: ${areas}</div>` : ""}
      ${(j.vak?.specialties || []).length ? `<h3>Специальности ВАК</h3><div>${j.vak.specialties.map((s) => `<span class="tag">${esc(s)}</span>`).join(" ")}</div>` : ""}

      <h3>Стоимость публикации</h3>
      ${j.price ? `<div>${esc(j.price.text)} ${srcTag(j.price.src)} ${j.price.url ? link(j.price.url, "подробнее") : ""}</div>` : (j.price_mentions || []).length ? "" : '<div class="muted">Сведений нет — уточните в редакции.</div>'}
      ${mentions(j.price_mentions)}

      <h3>Срок публикации</h3>
      ${j.term ? `<div>${esc(j.term.text)} ${srcTag(j.term.src)}</div>` : (j.term_mentions || []).length ? "" : '<div class="muted">Сведений нет.</div>'}
      ${mentions(j.term_mentions)}

      <h3>Контакты редакции</h3>
      <div class="contacts">
        ${j.site ? `<div>Сайт: ${link(j.site)}</div>` : '<div class="muted">Сайт не найден</div>'}
        ${j.author_page ? `<div>Для авторов: ${link(j.author_page)}</div>` : ""}
        ${(j.emails || []).length ? `<div>E-mail: ${j.emails.map((x) => `<a href="mailto:${esc(x)}">${esc(x)}</a>`).join(", ")}</div>` : ""}
        ${(j.phones || []).length ? `<div>Телефон: ${j.phones.map(esc).join(", ")}</div>` : ""}
      </div>

      <h3>Как подать статью</h3>
      <ol>${howto.map((s) => `<li>${esc(s).replace(/(https?:\/\/\S+)/g, (u) => link(u.replace(/&amp;/g, "&")))}</li>`).join("")}</ol>
      ${j.review ? `<div class="jsub">Рецензирование: ${esc(j.review)}</div>` : ""}
      ${j.notes ? `<h3>Примечание</h3><div>${esc(j.notes)}</div>` : ""}
      <p class="jsub" style="margin-top:16px">${j.egpni_url ? link(j.egpni_url, "Карточка в ЕГПНИ") + " · " : ""}Данные собраны автоматически ${fmtDate(m.updated)}; перед подачей сверяйтесь с сайтом журнала.</p>`;
    $("#card").showModal();
  }
  $("#card").addEventListener("click", (e) => {
    if (e.target.id === "card" || e.target.closest(".close")) $("#card").close();
  });
  $("#grid tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) openCard(tr.dataset.id); });
  $("#grid tbody").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr"); if (tr) openCard(tr.dataset.id); } });

  // ---------- CSV
  $("#csv").addEventListener("click", () => {
    const head = ["Журнал", "ISSN", "Издатель", "ЕГПНИ уровень", "ВАК", "Категория ВАК", "Специальности ВАК", "Scopus", "Квартиль Scopus", "WoS CC", "Квартиль WoS", "Ядро РИНЦ", "РИНЦ", "Сайт", "E-mail", "Телефон", "Стоимость", "Срок"];
    const yn = (b) => (b ? "да" : "нет");
    const rows = shown.map((j) => [j.name, (j.issns || []).join(", "), j.publisher, j.egpni?.level, yn(j.vak?.in), j.vak?.category, (j.vak?.specialties || []).join(", "),
      yn(j.scopus?.in), j.scopus?.quartile, yn(j.wos?.in), j.wos?.quartile, yn(j.rsci), yn(j.rinc), j.site, (j.emails || []).join(", "), (j.phones || []).join(", "), j.price?.text, j.term?.text]);
    const csv = [head, ...rows].map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(";")).join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = `журналы_ФУ_${m.updated || ""}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // ---------- вкладки и изменения
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    ["list", "changes", "about"].forEach((v) => ($("#view-" + v).hidden = v !== t.dataset.view));
  }));
  const val = (x) => (x === true ? "да" : x === false || x == null ? "—" : x);
  $("#view-changes").innerHTML = CHANGES.length ? CHANGES.map((c) => `
    <div class="change-block"><h3>${fmtDate(c.date)}</h3><ul>${c.changes.map((x) =>
      `<li><a href="#" data-id="${x.id}">${esc(x.name)}</a>: ${esc(x.field)} ${esc(val(x.from))} → <b>${esc(val(x.to))}</b></li>`).join("")}</ul></div>`).join("")
    : '<div class="change-block">Изменений пока не зафиксировано: история начнёт копиться со следующего ежемесячного обновления.</div>';
  $("#view-changes").addEventListener("click", (e) => { const a = e.target.closest("a[data-id]"); if (a) { e.preventDefault(); openCard(a.dataset.id); } });

  // ---------- запуск
  try {
    const saved = JSON.parse(localStorage.getItem("fu-filters") || "null");
    if (saved) FIELDS.forEach((id) => saved[id] != null && ($("#" + id).value = saved[id]));
  } catch (e) { /* нет доступа к хранилищу */ }
  $("#filters").addEventListener("input", render);
  $("#sort").addEventListener("change", render);
  $("#reset").addEventListener("click", () => resetFilters());
  render();
})();
