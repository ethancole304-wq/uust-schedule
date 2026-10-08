"""Обновляет офлайн-копию расписания УУНиТ для GitHub Pages.

  python scripts/update.py tracked   # группы из tracked.json и их преподаватели (каждые 3 часа)
  python scripts/update.py full      # все группы и преподаватели + список групп (раз в сутки)

Источник — тот же API, что у schedule.uust.ru (dev.uust-time.ru). Если он не ответил,
запрос идёт через воркер бота на Cloudflare. Результат: data/<type>/<id>.json, data/idx.json.
"""
import json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

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

    if mode == "full":
        groups = set(gname)
    else:
        tracked = json.load(open(os.path.join(ROOT, "tracked.json"), encoding="utf-8"))
        groups = set(tracked.get("groups", []))
        for pre in tracked.get("prefixes", []):
            groups |= {gid for gid, t in gname.items() if t.startswith(pre)}
    print(f"групп: {len(groups)}")
    done_g, ch_g, f_g = run_batch(cfg, 0, groups, gname, meta)

    if mode == "full":
        teachers = set(tname)
    else:
        teachers = {x["pid"] for _, items in done_g for x in items if x.get("pid")}
        teachers |= set(json.load(open(os.path.join(ROOT, "tracked.json"), encoding="utf-8")).get("teachers", []))
    print(f"преподавателей: {len(teachers)}")
    done_t, ch_t, f_t = run_batch(cfg, 1, teachers, tname, meta)

    avail = set()
    for k in ("0", "1"):
        d = os.path.join(DATA, k)
        if os.path.isdir(d):
            avail |= {f"{k}:{f[:-5]}" for f in os.listdir(d) if f.endswith(".json")}
    idx["a"] = sorted(avail)
    write(idx_path, idx)
    write(os.path.join(DATA, "meta.json"), {"date": date.today().isoformat(), "semester": cfg["semester"], "yearStart": cfg["yearStart"]})
    print(f"готово за {time.time() - t0:.0f} c: изменено {ch_g + ch_t}, ошибок {f_g + f_t}, офлайн доступно {len(avail)}")
    if not done_g and not done_t:
        sys.exit(1)


if __name__ == "__main__":
    main()
