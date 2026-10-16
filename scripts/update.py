"""Обновляет офлайн-копию расписания УУНиТ для GitHub Pages.

  python scripts/update.py tracked   # группы из tracked.json и их преподаватели (каждые 3 часа)
  python scripts/update.py full      # все группы и преподаватели + список групп (раз в сутки)

Источник — тот же API, что у schedule.uust.ru (dev.uust-time.ru). Если он не ответил,
запрос идёт через воркер бота на Cloudflare. Результат: data/<type>/<id>.json, data/idx.json,
data/calendar.json (праздники: isdayoff.ru + Nager.Date из public-apis) и data/ics/<type>/<id>.ics
(подписка на календарь телефона для групп из tracked.json и их преподавателей).
"""
import hashlib, json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

SITE = "https://schedule.uust.ru"
API = "https://dev.uust-time.ru/api/v"
LIVE = "https://uust-schedule-bot.ethancole304.workers.dev"
DEF = {"ver": "852972", "semester": 241, "yearStart": "2026-09-01"}
H = {
    "Origin": SITE, "Referer": SITE + "/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9",
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WORKERS = 4  # параллельных запросов — не нагружаем чужой сервер


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def site_cfg():
    cfg = dict(DEF)
    try:
        html = get(SITE + "/")
        m = re.search(r"/assets/(index-[\w-]+\.js)", html)
        js = get(f"{SITE}/assets/{m.group(1)}")
        if v := re.search(r"uust-time\.ru/api/v/(\d+)/", js): cfg["ver"] = v.group(1)
        if v := re.search(r'"selectedSemester",(\d+)', js): cfg["semester"] = int(v.group(1))
        if v := re.search(r'=\w+\(\w+\("(\d{4}-\d{2}-\d{2})"\)\)', js): cfg["yearStart"] = v.group(1)
    except Exception as e:
        print("::warning::не удалось прочитать параметры с schedule.uust.ru, беру значения по умолчанию:", e)
    return cfg


def fio(t):
    return " ".join(x for x in (t.get("surname"), t.get("name"), t.get("patronymic")) if x)


def fetch_index(cfg):
    """Список групп и преподавателей: напрямую из API, иначе через воркер, иначе старый файл."""
    try:
        g = json.loads(get(f"{API}/{cfg['ver']}/groups?site=schedule", timeout=90))
        t = json.loads(get(f"{API}/{cfg['ver']}/teachers?site=schedule", timeout=90))
        g = g.values() if isinstance(g, dict) else g
        t = t.values() if isinstance(t, dict) else t
        idx = {
            "g": [[x["group_id"], x["group_title"]] for x in g if x.get("group_title") and x["group_title"] != "0"],
            "t": [[x["isu_person_id"], fio(x)] for x in t if x.get("isu_person_id")],
        }
        if len(idx["g"]) > 100:
            print(f"индекс из API: {len(idx['g'])} групп, {len(idx['t'])} преподавателей")
            return idx
    except Exception as e:
        print("индекс из API не получен:", e)
    try:
        idx = json.loads(get(LIVE + "/api/idx", timeout=40))
        print("индекс через воркер")
        return {"g": idx["g"], "t": idx["t"]}
    except Exception as e:
        print("индекс через воркер не получен:", e)
    return None


def compact(x):
    return {
        "w": [int(w) for w in x.get("schedule_weeks") or [] if str(w).strip().isdigit()], "d": x.get("schedule_weekday_id"),
        "n": x.get("schedule_time_num"), "t": x.get("schedule_time_title"), "s": x.get("schedule_subject_title"),
        "y": x.get("type"), "r": x.get("room_title_short") or x.get("room_title"), "bs": x.get("building_short_title"),
        "c": x.get("comment"), "p": x.get("teacher"), "pf": x.get("teacher_fullname"), "pid": x.get("teacher_id"),
        "g": x.get("student_group_number_title"),
    }


lock = threading.Lock()
direct_fails = 0


def schedule(cfg, kind, eid):
    global direct_fails
    if direct_fails < 10:  # если API подряд не отвечает — дальше только через воркер
        try:
            raw = json.loads(get(f"{API}/{cfg['ver']}/schedule/{kind}/{eid}/semester/{cfg['semester']}?site=schedule", timeout=20))
            items = [compact(x) for x in (raw or [])]
            with lock:
                direct_fails = 0
            return items
        except Exception as e:
            with lock:
                direct_fails += 1
            print(f"  {kind}/{eid}: API не ответил ({e}), пробую воркер")
    return json.loads(get(f"{LIVE}/api/schedule?type={kind}&id={eid}", timeout=40))["items"]


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != txt:
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        return True
    return False


def run_batch(cfg, kind, ids, names, meta):
    ok, changed, fails = [], 0, 0

    def one(eid):
        try:
            items = schedule(cfg, kind, eid)
        except Exception as e:
            print(f"  ошибка {kind}/{eid}: {e}")
            return eid, None
        time.sleep(0.2)
        return eid, items

    with ThreadPoolExecutor(WORKERS) as pool:
        for eid, items in pool.map(one, sorted(ids)):
            if items is None:
                fails += 1
                continue
            if write(os.path.join(DATA, str(kind), f"{eid}.json"), {"title": names.get(eid), "cfg": meta, "items": items}):
                changed += 1
            ok.append((eid, items))
    return ok, changed, fails

# ---------------- производственный календарь ----------------
FIXED = ["01-01", "01-02", "01-03", "01-04", "01-05", "01-06", "01-07", "01-08", "02-23", "03-08", "05-01", "05-09", "06-12", "11-04"]


def fetch_calendar(cfg):
    """Праздники и выходные дни РФ: isdayoff.ru (переносы) + Nager.Date (сами праздники, в т.ч. в субботу)."""
    y0 = int(cfg["yearStart"][:4])
    hol, src = set(), []
    for y in (y0, y0 + 1):
        try:
            s = get(f"https://isdayoff.ru/api/getdata?year={y}&cc=ru", timeout=20).strip()
            if len(s) in (365, 366) and set(s) <= set("01248"):
                d0 = date(y, 1, 1)
                for i, ch in enumerate(s):
                    d = d0 + timedelta(days=i)
                    if ch == "1" and d.weekday() < 5:  # будний день объявлен выходным
                        hol.add(d.isoformat())
                src.append(f"isdayoff:{y}")
        except Exception as e:
            print("isdayoff.ru:", e)
        try:
            for h in json.loads(get(f"https://date.nager.at/api/v3/PublicHolidays/{y}/RU", timeout=20)):
                if h.get("global", True):
                    hol.add(h["date"])
            src.append(f"nager:{y}")
        except Exception as e:
            print("Nager.Date:", e)
    if not src:
        return None
    for y in (y0, y0 + 1):  # государственные праздники — всегда
        hol |= {f"{y}-{md}" for md in FIXED}
    return {"holidays": sorted(hol), "source": src}


def load_holidays():
    p = os.path.join(DATA, "calendar.json")
    if os.path.exists(p):
        return set(json.load(open(p, encoding="utf-8")).get("holidays", []))
    return set()


# ---------------- iCalendar (подписка на календарь телефона) ----------------
SHORT = {"Лекция": "лекция", "Практика (семинар)": "практика", "Лабораторная работа": "лаб."}


def ics_escape(s):
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line):
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def make_ics(kind, eid, title, items, cfg, hol):
    ys = date.fromisoformat(cfg["yearStart"])
    w1 = ys - timedelta(days=ys.weekday())
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//uust-schedule//RU", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(title)} — УУНиТ", "X-WR-TIMEZONE:Asia/Yekaterinburg",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
        "BEGIN:VTIMEZONE", "TZID:Asia/Yekaterinburg", "BEGIN:STANDARD", "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0500", "TZOFFSETTO:+0500", "TZNAME:+05", "END:STANDARD", "END:VTIMEZONE",
    ]
    seen = set()
    for x in sorted(items, key=lambda x: (x.get("d") or 0, x.get("n") or 0, x.get("s") or "")):
        m = re.match(r"(\d\d):(\d\d)-(\d\d):(\d\d)", x.get("t") or "")
        if not m or not x.get("d"):
            continue
        for w in x.get("w") or []:
            d = w1 + timedelta(days=(w - 1) * 7 + x["d"] - 1)
            if d.isoformat() in hol:
                continue
            key = (d, x.get("n"), x.get("s"), x.get("y"), x.get("r"))
            if key in seen:
                continue
            seen.add(key)
            uid = hashlib.sha1(f"{kind}|{eid}|{d}|{x.get('n')}|{x.get('s')}|{x.get('y')}".encode()).hexdigest()[:16]
            room = x.get("c") if x.get("r") == "Уточняется" and x.get("c") else x.get("r")
            loc = ", ".join(v for v in dict.fromkeys([room, x.get("bs")]) if v)
            desc = []
            if kind == 0 and (x.get("pf") or x.get("p") or "").replace(".", "").strip():
                desc.append(x.get("pf") or x.get("p"))
            if kind == 1 and x.get("g"):
                desc.append(x["g"])
            if x.get("n"):
                desc.append(f"{x['n']} пара")
            if x.get("c") and x.get("c") != room:
                desc.append(x["c"])
            typ = SHORT.get(x.get("y"), (x.get("y") or "").lower())
            ds = d.strftime("%Y%m%d")
            lines += [
                "BEGIN:VEVENT", f"UID:{uid}@uust-schedule", f"DTSTAMP:{w1.strftime('%Y%m%d')}T000000Z",
                f"DTSTART;TZID=Asia/Yekaterinburg:{ds}T{m[1]}{m[2]}00",
                f"DTEND;TZID=Asia/Yekaterinburg:{ds}T{m[3]}{m[4]}00",
                ics_fold("SUMMARY:" + ics_escape(f"{x.get('s') or 'Занятие'}" + (f" ({typ})" if typ else ""))),
            ]
            if loc:
                lines.append(ics_fold("LOCATION:" + ics_escape(loc)))
            if desc:
                lines.append(ics_fold("DESCRIPTION:" + ics_escape("\n".join(desc))))
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_text(path, txt):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else None
    if old != txt:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(txt)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tracked"
    t0 = time.time()
    cfg = site_cfg()
    print("режим:", mode, "| параметры:", cfg)
    idx_path = os.path.join(DATA, "idx.json")
    old_idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else None

    idx = fetch_index(cfg) if (mode == "full" or not old_idx) else None
    if not idx:
        idx = {"g": old_idx["g"], "t": old_idx["t"]} if old_idx else None
    if not idx or not idx.get("g"):
        sys.exit("нет списка групп")
    gname = {g[0]: g[1] for g in idx["g"]}
    tname = {t[0]: t[1] for t in idx["t"]}
    meta = {"yearStart": cfg["yearStart"], "semester": cfg["semester"]}

    tracked = json.load(open(os.path.join(ROOT, "tracked.json"), encoding="utf-8"))
    tracked_groups = set(tracked.get("groups", []))
    for pre in tracked.get("prefixes", []):
        tracked_groups |= {gid for gid, t in gname.items() if t.startswith(pre)}

    if mode == "full" or not os.path.exists(os.path.join(DATA, "calendar.json")):
        cal = fetch_calendar(cfg)
        if cal:
            write(os.path.join(DATA, "calendar.json"), cal)
            print(f"календарь: {len(cal['holidays'])} праздничных дней ({', '.join(cal['source'])})")
    hol = load_holidays()

    groups = set(gname) if mode == "full" else tracked_groups
    print(f"групп: {len(groups)}")
    done_g, ch_g, f_g = run_batch(cfg, 0, groups, gname, meta)

    tracked_teachers = {x["pid"] for gid, items in done_g if gid in tracked_groups for x in items if x.get("pid")}
    tracked_teachers |= set(tracked.get("teachers", []))
    teachers = set(tname) if mode == "full" else tracked_teachers
    print(f"преподавателей: {len(teachers)}")
    done_t, ch_t, f_t = run_batch(cfg, 1, teachers, tname, meta)

    # подписка на календарь — только для «своих» групп и их преподавателей
    ics = []
    for kind, done, keep, names in ((0, done_g, tracked_groups, gname), (1, done_t, tracked_teachers, tname)):
        for eid, items in done:
            if eid in keep:
                write_text(os.path.join(DATA, "ics", str(kind), f"{eid}.ics"), make_ics(kind, eid, names.get(eid) or str(eid), items, cfg, hol))
                ics.append(f"{kind}:{eid}")
    print(f"ics-календарей: {len(ics)}")

    avail = set()
    for k in ("0", "1"):
        d = os.path.join(DATA, k)
        if os.path.isdir(d):
            avail |= {f"{k}:{f[:-5]}" for f in os.listdir(d) if f.endswith(".json")}
    ics_all = set()
    for k in ("0", "1"):
        d = os.path.join(DATA, "ics", k)
        if os.path.isdir(d):
            ics_all |= {f"{k}:{f[:-4]}" for f in os.listdir(d) if f.endswith(".ics")}
    every = {f"0:{g[0]}" for g in idx["g"]} | {f"1:{t[0]}" for t in idx["t"]}
    # компактнее, чем перечислять всё доступное: только то, чего НЕТ в офлайн-копии
    idx = {"g": idx["g"], "t": idx["t"], "na": sorted(every - avail), "ics": sorted(ics_all)}
    write(idx_path, idx)
    write(os.path.join(DATA, "meta.json"), {"date": date.today().isoformat(), "semester": cfg["semester"], "yearStart": cfg["yearStart"]})
    print(f"готово за {time.time() - t0:.0f} c: изменено {ch_g + ch_t}, ошибок {f_g + f_t}, офлайн доступно {len(avail)}, нет в копии {len(idx['na'])}")
    if not done_g and not done_t:
        sys.exit(1)

if __name__ == "__main__":
    main()
