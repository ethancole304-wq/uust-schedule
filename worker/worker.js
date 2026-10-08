// Telegram-бот «Расписание УУНиТ» на Cloudflare Workers.
//
// Что делает:
//   • ставит кнопку «Расписание» → Mini App на GitHub Pages (ethancole304-wq.github.io/uust-schedule);
//   • напоминает за 10 минут до пары (крон */5 * * * *);
//   • сообщает об изменениях в расписании на 2 недели вперёд (крон 7 */3 * * *);
//   • отдаёт /api/schedule, /api/search, /api/idx — запасной источник данных для приложения.
//
// Привязки: KV namespace → переменная KV. Токен — секрет BOT_TOKEN или ключ token в KV (ставится на главной странице).

export default (() => {
const SITE = "https://schedule.uust.ru";
const API_HOST = "https://dev.uust-time.ru/api/v";
const PAGES = "https://ethancole304-wq.github.io/uust-schedule/";
const DEFAULTS = { ver: "852972", semester: 241, yearStart: "2026-09-01" };
const TZ_H = 5; // Уфа, UTC+5
const GROUP = 0, TEACHER = 1;
const CRON_REMIND = "*/5 * * * *";
const HDR = {
  Origin: SITE, Referer: SITE + "/",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
  Accept: "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9",
};
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const HOLIDAYS = new Set(["1-1", "1-7", "2-23", "3-8", "5-1", "5-9", "6-12", "11-4"]);
const TYPE_ICON = {
  "Лекция": "📘", "Практика (семинар)": "📗", "Лабораторная работа": "🧪", "Военная подготовка": "🎖",
  "Экзамен": "📝", "Зачет": "✅", "Зачёт": "✅", "Консультация": "💬",
};
const SHORT_TYPE = { "Лекция": "лекция", "Практика (семинар)": "практика", "Лабораторная работа": "лаб." };
// начала пар (минуты от полуночи) — чтобы крон напоминаний не дёргал API зря
const STARTS = [480, 575, 695, 790, 910, 1005, 1100, 1195];

// ---------------- даты ----------------
const DAY = 86400000;
const ufaNow = () => Date.now() + TZ_H * 3600000;
const todayNum = () => Math.floor(ufaNow() / DAY);
const minsNow = () => Math.floor((ufaNow() % DAY) / 60000);
const isoToNum = (s) => Math.floor(Date.parse(s + "T00:00:00Z") / DAY);
const numToDate = (n) => new Date(n * DAY);
const numToIso = (n) => numToDate(n).toISOString().slice(0, 10);
const isoWeekday = (n) => ((numToDate(n).getUTCDay() + 6) % 7) + 1;
const isHoliday = (n) => { const d = numToDate(n); return HOLIDAYS.has(`${d.getUTCMonth() + 1}-${d.getUTCDate()}`); };
const dayLabel = (n) => { const d = numToDate(n); return `${WEEKDAYS[isoWeekday(n) - 1]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
const week1Monday = (cfg) => { const s = isoToNum(cfg.yearStart); return s - (isoWeekday(s) - 1); };
const weekOf = (cfg, n) => Math.max(1, Math.floor((n - week1Monday(cfg)) / 7) + 1);
const toMin = (t) => { const m = /(\d\d):(\d\d)/.exec(t || ""); return m ? +m[1] * 60 + +m[2] : null; };
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ---------------- параметры сайта ----------------
let cfgMem = null;
async function getCfg(env) {
  if (cfgMem && Date.now() - cfgMem.at < 6 * 3600000) return cfgMem.cfg;
  let cfg = null;
  try {
    const saved = await env.KV.get("cfg", "json");
    if (saved && Date.now() - saved.at < 6 * 3600000) cfg = saved.cfg;
  } catch {}
  if (!cfg) {
    cfg = { ...DEFAULTS };
    try {
      const html = await (await fetch(SITE + "/", { signal: AbortSignal.timeout(10000) })).text();
      const f = html.match(/\/assets\/(index-[\w-]+\.js)/);
      if (f) {
        const js = await (await fetch(`${SITE}/assets/${f[1]}`, { signal: AbortSignal.timeout(15000) })).text();
        let m;
        if ((m = js.match(/uust-time\.ru\/api\/v\/(\d+)\//))) cfg.ver = m[1];
        if ((m = js.match(/"selectedSemester",(\d+)/))) cfg.semester = +m[1];
        if ((m = js.match(/=\w+\(\w+\("(\d{4}-\d{2}-\d{2})"\)\)/))) cfg.yearStart = m[1];
      }
      await env.KV.put("cfg", JSON.stringify({ at: Date.now(), cfg }));
    } catch (e) { console.log("cfg fallback", String(e)); }
  }
  cfgMem = { at: Date.now(), cfg };
  return cfg;
}

// ---------------- расписание ----------------
const mem = new Map();
const compact = (x) => ({
  w: (x.schedule_weeks || []).filter((w) => /^\d+$/.test(String(w).trim())).map(Number), d: x.schedule_weekday_id,
  n: x.schedule_time_num, t: x.schedule_time_title, s: x.schedule_subject_title, y: x.type,
  r: x.room_title_short || x.room_title, bs: x.building_short_title, c: x.comment,
  p: x.teacher, pf: x.teacher_fullname, pid: x.teacher_id, g: x.student_group_number_title,
});

async function schedule(env, kind, id, ttlSec = 1800, fallback = true) {
  const key = `${kind}/${id}`;
  const hit = mem.get(key);
  if (hit && Date.now() - hit.at < ttlSec * 1000) return hit.items;
  const cfg = await getCfg(env);
  let items = null;
  try {
    const r = await fetch(`${API_HOST}/${cfg.ver}/schedule/${kind}/${id}/semester/${cfg.semester}?site=schedule`, {
      headers: HDR, signal: AbortSignal.timeout(20000), cf: { cacheTtl: 300, cacheEverything: true },
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    items = ((await r.json()) || []).map(compact);
  } catch (e) {
    console.log("api fail", key, String(e));
    if (fallback) try {
      const r = await fetch(`${PAGES}data/${kind}/${id}.json`, { signal: AbortSignal.timeout(15000) });
      if (r.ok) items = (await r.json()).items;
    } catch {}
  }
  if (!items) { if (hit) return hit.items; throw new Error("schedule unavailable " + key); }
  mem.set(key, { at: Date.now(), items });
  return items;
}

function lessonsOn(items, cfg, n) {
  const w = weekOf(cfg, n), wd = isoWeekday(n);
  const list = items.filter((x) => x.d === wd && (x.w || []).includes(w)).sort((a, b) => (a.n ?? 99) - (b.n ?? 99));
  const out = new Map();
  for (const x of list) {
    const k = [x.n, x.s, x.y, x.r].join("|");
    if (out.has(k) && x.g) out.get(k).g += ", " + x.g;
    else if (!out.has(k)) out.set(k, { ...x });
  }
  return [...out.values()];
}
const roomOf = (x) => (x.r === "Уточняется" && (x.c || "").trim() ? x.c.trim() : x.r || "");

// ---------------- индекс групп и преподавателей (из GitHub Pages) ----------------
let idxMem = null;
async function getIndex(env) {
  if (idxMem && Date.now() - idxMem.at < 6 * 3600000) return idxMem.idx;
  let saved = await env.KV.get("idx", "json");
  if (!saved || Date.now() - (saved.at || 0) > DAY) {
    try {
      const r = await fetch(PAGES + "data/idx.json", { signal: AbortSignal.timeout(15000) });
      const j = r.ok ? await r.json() : null;
      if (j && j.g && j.g.length > 100) {
        saved = { g: j.g, t: j.t, at: Date.now() };
        await env.KV.put("idx", JSON.stringify(saved));
      }
    } catch (e) { console.log("idx refresh failed", String(e)); }
  }
  idxMem = { at: Date.now(), idx: saved };
  return saved;
}
const norm = (s) => (s || "").toLowerCase().replace(/ё/g, "е").replace(/[\s\-_.]/g, "");
async function search(env, query) {
  const q = norm(query), idx = await getIndex(env);
  if (!q || !idx) return [];
  const res = [];
  for (const [id, title] of idx.g) { const t = norm(title); if (t.includes(q)) res.push([GROUP, id, title, t.startsWith(q) ? 0 : 1]); }
  if (/[а-яa-z]{3,}/i.test(q) && !/\d/.test(q)) for (const [id, name] of idx.t) if (norm(name).includes(q)) res.push([TEACHER, id, name, 2]);
  res.sort((a, b) => a[3] - b[3] || a[2].length - b[2].length || a[2].localeCompare(b[2]));
  return res.slice(0, 20);
}
async function titleOf(env, kind, id) {
  const idx = await getIndex(env);
  const list = idx ? (kind === GROUP ? idx.g : idx.t) : [];
  return list.find((x) => x[0] === id)?.[1] || String(id);
}

// ---------------- Telegram ----------------
const getToken = async (env) => env.BOT_TOKEN || (await env.KV.get("token"));
async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${await getToken(env)}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!j.ok && !/not modified/.test(j.description || "")) console.log(method, j.description);
  return j;
}
const send = (env, chat_id, text, reply_markup) =>
  tg(env, "sendMessage", { chat_id, text, parse_mode: "HTML", reply_markup, link_preview_options: { is_disabled: true } });

const appBase = async (env) => (await env.KV.get("app_url")) || PAGES;
async function appLink(env, u) {
  const base = await appBase(env);
  if (!u || u.id == null) return base;
  return `${base}${base.includes("?") ? "&" : "?"}type=${u.kind}&id=${u.id}&title=${encodeURIComponent(u.title || "")}`;
}

// ---------------- пользователи и подписки ----------------
const getUser = async (env, uid) => (await env.KV.get("u:" + uid, "json")) || {};
async function saveUser(env, uid, patch) {
  const u = { ...(await getUser(env, uid)), ...patch };
  await env.KV.put("u:" + uid, JSON.stringify(u));
  const subs = (await env.KV.get("subs", "json")) || {};
  const on = !!((u.remind || u.changes) && u.id != null && u.chat_id);
  if (on !== !!subs[uid]) {
    if (on) subs[uid] = 1; else delete subs[uid];
    await env.KV.put("subs", JSON.stringify(subs));
  }
  return u;
}

function settingsText(u) {
  let t = "📅 <b>Расписание УУНиТ</b>\n\nОткрывай кнопкой «Расписание» слева от поля ввода или кнопкой ниже.";
  if (u.id != null) t += `\n\nУведомления для: <b>${esc(u.title)}</b>\nСменить группу — в приложении внизу «Напоминания и изменения».`;
  else t += "\n\nЧтобы включить напоминания о парах, открой приложение и внизу нажми «Напоминания и изменения».";
  return t;
}
function settingsKb(u, url) {
  const rows = [[{ text: "📅 Открыть расписание", web_app: { url } }]];
  if (u.id != null) {
    rows.push([{ text: `${u.remind ? "✅" : "⬜️"} Напоминать за 10 минут до пары`, callback_data: "t:remind" }]);
    rows.push([{ text: `${u.changes ? "✅" : "⬜️"} Сообщать об изменениях`, callback_data: "t:changes" }]);
  }
  return { inline_keyboard: rows };
}

async function onMessage(env, msg) {
  const chat = msg.chat.id, uid = msg.from.id, text = (msg.text || "").trim();
  let u = await getUser(env, uid);
  const m = /^\/start\s+n_(\d)_(\d+)/.exec(text);
  if (m) {
    const kind = +m[1], id = +m[2];
    const patch = { kind, id, title: await titleOf(env, kind, id), chat_id: chat };
    if (u.remind === undefined && u.changes === undefined) { patch.remind = true; patch.changes = true; }
    u = await saveUser(env, uid, patch);
  }
  const url = await appLink(env, u);
  await tg(env, "setChatMenuButton", { chat_id: chat, menu_button: { type: "web_app", text: "Расписание", web_app: { url } } });
  if (!u.v2) {
    await send(env, chat, "Обновление: теперь всё расписание — в приложении.", { remove_keyboard: true });
    u = await saveUser(env, uid, { v2: true, chat_id: chat });
  }
  return send(env, chat, (m ? "✅ Готово!\n\n" : "") + settingsText(u), settingsKb(u, url));
}

async function onCallback(env, cb) {
  const uid = cb.from.id, chat = cb.message?.chat.id, mid = cb.message?.message_id, data = cb.data || "";
  const t = /^t:(remind|changes)$/.exec(data);
  if (!t) return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Расписание открывается кнопкой «Расписание»", show_alert: true });
  let u = await getUser(env, uid);
  if (u.id == null) return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Сначала выбери группу в приложении", show_alert: true });
  u = await saveUser(env, uid, { [t[1]]: !u[t[1]], chat_id: chat });
  await tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: u[t[1]] ? "Включено" : "Выключено" });
  return tg(env, "editMessageText", { chat_id: chat, message_id: mid, text: settingsText(u), parse_mode: "HTML", reply_markup: settingsKb(u, await appLink(env, u)) });
}

// ---------------- напоминания ----------------
function lessonBlock(x, kind) {
  const lines = [`${TYPE_ICON[x.y] || "▫️"} ${esc(x.s || "—")}${x.y ? ` <i>(${esc(SHORT_TYPE[x.y] || x.y)})</i>` : ""}`];
  const room = roomOf(x);
  if (room) lines.push(`📍 ${esc(room)}${x.bs && x.bs !== room ? ` · ${esc(x.bs)}` : ""}`);
  if (kind === GROUP && (x.p || "").replace(/[.\s]/g, "")) lines.push(`👤 ${esc(x.p.trim())}`);
  if (kind === TEACHER && x.g) lines.push(`👥 ${esc(x.g)}`);
  return lines.join("\n");
}

async function remindTick(env) {
  const n = todayNum(), now = minsNow();
  if (isoWeekday(n) === 7 || isHoliday(n)) return;
  if (!STARTS.some((s) => s - now >= 8 && s - now <= 12)) return;
  const subs = Object.keys((await env.KV.get("subs", "json")) || {});
  if (!subs.length) return;
  const cfg = await getCfg(env);
  for (const uid of subs.slice(0, 15)) {
    const u = await getUser(env, uid);
    if (!u.remind || u.id == null || !u.chat_id) continue;
    try {
      const items = lessonsOn(await schedule(env, u.kind, u.id), cfg, n)
        .filter((x) => { const s = toMin((x.t || "").split("-")[0]); return s != null && s - now >= 8 && s - now <= 12; });
      if (!items.length) continue;
      const x0 = items[0];
      const head = `⏰ <b>Через ${toMin((x0.t || "").split("-")[0]) - now} минут${x0.n ? ` — ${x0.n} пара` : ""}</b> · ${esc((x0.t || "").replace("-", "–"))}`;
      await send(env, u.chat_id, head + "\n\n" + items.map((x) => lessonBlock(x, u.kind)).join("\n\n"));
    } catch (e) { console.log("remind", uid, String(e)); }
  }
}

// ---------------- изменения в расписании ----------------
const HORIZON = 14;
function fingerprint(items, cfg, from) {
  const lines = [];
  for (let n = from; n < from + HORIZON; n++) {
    if (isoWeekday(n) === 7) continue;
    for (const x of lessonsOn(items, cfg, n)) lines.push([numToIso(n), x.n ?? "", x.t || "", x.s || "", x.y || "", roomOf(x), (x.p || "").trim()].join("|"));
  }
  return lines.sort();
}
function describe(line, sign) {
  const [, num, t, s, y, room, p] = line.split("|");
  return `${sign} ${num ? num + " пара " : ""}${esc((t || "").split("-")[0])} ${esc(s)}${y ? ` (${esc(SHORT_TYPE[y] || y)})` : ""}${room ? ` · ${esc(room)}` : ""}${p && p.replace(/[.\s]/g, "") ? ` · ${esc(p)}` : ""}`;
}

async function changesTick(env) {
  const subs = Object.keys((await env.KV.get("subs", "json")) || {});
  if (!subs.length) return;
  const cfg = await getCfg(env), today = todayNum();
  const byEnt = new Map();
  for (const uid of subs) {
    const u = await getUser(env, uid);
    if (!u.changes || u.id == null || !u.chat_id) continue;
    const k = `${u.kind}:${u.id}`;
    if (!byEnt.has(k)) byEnt.set(k, { kind: u.kind, id: u.id, title: u.title, users: [] });
    byEnt.get(k).users.push(u);
  }
  for (const [k, e] of [...byEnt].slice(0, 10)) {
    try {
      const items = await schedule(env, e.kind, e.id, 0, false); // только живые данные, иначе ложные «изменения»
      const cur = fingerprint(items, cfg, today);
      const prev = await env.KV.get("snap:" + k, "json");
      if (prev && prev.lines.length && !cur.length) continue; // похоже на сбой API, не пугаем
      if (prev) {
        const lo = numToIso(Math.max(today, prev.from)), hi = numToIso(prev.from + HORIZON - 1);
        const inWin = (l) => { const d = l.slice(0, 10); return d >= lo && d <= hi; };
        const was = new Set(prev.lines.filter(inWin)), now = new Set(cur.filter(inWin));
        const added = [...now].filter((l) => !was.has(l)), removed = [...was].filter((l) => !now.has(l));
        if (added.length || removed.length) {
          const days = [...new Set([...added, ...removed].map((l) => l.slice(0, 10)))].sort();
          let lines = [];
          for (const d of days) {
            lines.push(`\n<b>${dayLabel(isoToNum(d))}</b>`);
            removed.filter((l) => l.startsWith(d)).forEach((l) => lines.push(describe(l, "➖")));
            added.filter((l) => l.startsWith(d)).forEach((l) => lines.push(describe(l, "➕")));
          }
          if (lines.length > 25) lines = lines.slice(0, 24).concat("…и ещё изменения — смотри в приложении");
          const text = `📣 <b>Изменения в расписании ${esc(e.title)}</b>\n${lines.join("\n")}`;
          for (const u of e.users) {
            await send(env, u.chat_id, text, { inline_keyboard: [[{ text: "📅 Открыть расписание", web_app: { url: await appLink(env, u) } }]] });
          }
        }
      }
      if (!prev || prev.from !== today || prev.lines.join("\n") !== cur.join("\n")) {
        await env.KV.put("snap:" + k, JSON.stringify({ from: today, lines: cur }));
      }
    } catch (err) { console.log("changes", k, String(err)); }
  }
}

// ---------------- первичная настройка ----------------
const json = (o, status = 200, maxAge = 0) => new Response(JSON.stringify(o), {
  status,
  headers: { "access-control-allow-origin": "*", "content-type": "application/json; charset=utf-8", "cache-control": maxAge ? `public, max-age=${maxAge}` : "no-store" },
});
const page = (body) => new Response(
  `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Расписание УУНиТ — бот</title><style>
body{font-family:system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 16px;line-height:1.5;color:#1d1d1f;background:#f6f7f9}
.card{background:#fff;border-radius:14px;padding:24px;box-shadow:0 1px 4px #0001}
input{width:100%;box-sizing:border-box;padding:12px;border:1px solid #ccd;border-radius:10px;font-size:15px;margin:12px 0}
button,a.btn{display:block;width:100%;box-sizing:border-box;text-align:center;padding:12px;border:0;border-radius:10px;background:#2481cc;color:#fff;font-size:16px;text-decoration:none;margin-top:12px}
.err{color:#c00}</style></head><body><div class="card">${body}</div></body></html>`,
  { headers: { "content-type": "text/html; charset=utf-8" } },
);

async function setup(env, origin, token) {
  const call = (m, b) => fetch(`https://api.telegram.org/bot${token}/${m}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) }).then((r) => r.json());
  const me = await call("getMe");
  if (!me.ok) return { error: "Telegram не принял токен: " + (me.description || "проверь, что скопирован целиком") };
  const secret = crypto.randomUUID().replace(/-/g, "");
  await env.KV.put("token", token);
  await env.KV.put("secret", secret);
  await env.KV.put("bot", me.result.username);
  const wh = await call("setWebhook", { url: origin + "/webhook", secret_token: secret, allowed_updates: ["message", "callback_query"], drop_pending_updates: true });
  if (!wh.ok) return { error: "setWebhook: " + wh.description };
  await env.KV.delete("menu_v");
  return { username: me.result.username };
}

async function ensureBotSettings(env) {
  if ((await env.KV.get("menu_v")) === "4") return;
  await tg(env, "setChatMenuButton", { menu_button: { type: "web_app", text: "Расписание", web_app: { url: await appBase(env) } } });
  await tg(env, "setMyCommands", { commands: [{ command: "start", description: "Открыть расписание и уведомления" }] });
  await tg(env, "setMyDescription", { description: "Расписание УУНиТ: красивое приложение с парами, неделей и сессией, напоминания за 10 минут и уведомления об изменениях." });
  await env.KV.put("menu_v", "4");
}

return {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (!env.KV) return page(`<h2>Почти готово</h2><p class="err">Не подключено KV-хранилище с именем <b>KV</b>.</p>`);

    if (url.pathname === "/webhook" && req.method === "POST") {
      const secret = await env.KV.get("secret");
      if (!secret || req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== secret) return new Response("forbidden", { status: 403 });
      const upd = await req.json();
      try {
        await ensureBotSettings(env);
        if (upd.message) await onMessage(env, upd.message);
        else if (upd.callback_query) await onCallback(env, upd.callback_query);
      } catch (e) { console.log("update error", e && e.stack || e); }
      return new Response("ok");
    }

    if (url.pathname === "/api/schedule") {
      const kind = +url.searchParams.get("type"), id = +url.searchParams.get("id");
      if (![0, 1, 2].includes(kind) || !(id > 0)) return json({ error: "bad params" }, 400);
      try {
        const [cfg, items] = await Promise.all([getCfg(env), schedule(env, kind, id)]);
        return json({ title: kind < 2 ? await titleOf(env, kind, id) : null, cfg: { yearStart: cfg.yearStart, semester: cfg.semester }, items }, 200, 300);
      } catch (e) { return json({ error: String(e) }, 502); }
    }
    if (url.pathname === "/api/search") {
      try { return json(await search(env, url.searchParams.get("q") || ""), 200, 3600); } catch (e) { return json({ error: String(e) }, 502); }
    }
    if (url.pathname === "/api/idx") {
      const idx = await getIndex(env);
      return idx ? json(idx, 200, 3600) : json({ error: "no idx" }, 404);
    }

    const configured = !!(await getToken(env)) && !!(await env.KV.get("secret"));
    if (url.pathname === "/setup" && req.method === "POST") {
      if (configured) return page(`<h2>Уже настроено</h2>`);
      const token = String((await req.formData()).get("token") || "").trim();
      if (!/^\d+:[\w-]{30,}$/.test(token)) return page(`<h2>Токен не похож на настоящий</h2><p class="err">Скопируй его из @BotFather целиком.</p><a class="btn" href="/">Назад</a>`);
      const r = await setup(env, url.origin, token);
      if (r.error) return page(`<h2>Ошибка</h2><p class="err">${esc(r.error)}</p><a class="btn" href="/">Назад</a>`);
      return page(`<h2>✅ Бот запущен</h2><p>@${esc(r.username)} работает 24/7.</p><a class="btn" href="https://t.me/${esc(r.username)}">Открыть бота</a>`);
    }
    if (configured) {
      const bot = await env.KV.get("bot");
      return page(`<h2>✅ Бот работает</h2><p>Расписание УУНиТ в Telegram.</p>${bot ? `<a class="btn" href="https://t.me/${esc(bot)}">Открыть @${esc(bot)}</a>` : ""}<a class="btn" href="${PAGES}">Открыть приложение</a>`);
    }
    return page(`<h2>Подключение бота</h2><p>Вставь токен из <b>@BotFather</b>.</p>
<form method="post" action="/setup"><input name="token" placeholder="Токен бота" autocomplete="off" required><button>Запустить бота</button></form>`);
  },

  async scheduled(event, env, ctx) {
    if (event.cron === CRON_REMIND) await remindTick(env);
    else await changesTick(env);
  },
};
})();
