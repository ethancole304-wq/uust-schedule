"""Обновляет офлайн-копию расписания для GitHub Pages.

Берёт расписание из того же API, что schedule.uust.ru (dev.uust-time.ru); если не вышло —
через воркер бота на Cloudflare. Пишет data/<type>/<id>.json и data/idx.json.
"""
import json, os, re, sys, time, urllib.request
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
        print("cfg fallback:", e)
    return cfg


def compact(x):
    return {
        "w": [int(w) for w in x.get("schedule_weeks") or [] if str(w).strip().isdigit()], "d": x.get("schedule_weekday_id"),
        "n": x.get("schedule_time_num"), "t": x.get("schedule_time_title"), "s": x.get("schedule_subject_title"),
        "y": x.get("type"), "r": x.get("room_title_short") or x.get("room_title"), "bs": x.get("building_short_title"),
        "c": x.get("comment"), "p": x.get("teacher"), "pf": x.get("teacher_fullname"), "pid": x.get("teacher_id"),
        "g": x.get("student_group_number_title"),
    }


DIRECT_FAILS = 0


def schedule(cfg, kind, eid):
    global DIRECT_FAILS
    if DIRECT_FAILS < 3:  # после трёх неудач подряд ходим только через воркер
        try:
            raw = json.loads(get(f"{API}/{cfg['ver']}/schedule/{kind}/{eid}/semester/{cfg['semester']}?site=schedule", timeout=15))
            items = [compact(x) for x in (raw or [])]
            DIRECT_FAILS = 0
            return items
        except Exception as e:
            DIRECT_FAILS += 1
            print(f"  direct {kind}/{eid} failed ({e}), via worker")
    return json.loads(get(f"{LIVE}/api/schedule?type={kind}&id={eid}", timeout=40))["items"]


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != txt:
        open(path, "w", encoding="utf-8").write(txt)


def main():
    cfg = site_cfg()
    print("cfg:", cfg)
    idx_path = os.path.join(DATA, "idx.json")
    idx = None
    try:
        idx = json.loads(get(LIVE + "/api/idx", timeout=40))
        idx = {"g": idx["g"], "t": idx["t"]}
    except Exception as e:
        print("idx from worker failed:", e)
        if os.path.exists(idx_path):
            idx = json.load(open(idx_path, encoding="utf-8"))
    if not idx or not idx.get("g"):
        sys.exit("нет индекса групп")
    gname = {g[0]: g[1] for g in idx["g"]}
    tname = {t[0]: t[1] for t in idx["t"]}

    tracked = json.load(open(os.path.join(ROOT, "tracked.json"), encoding="utf-8"))
    groups = set(tracked.get("groups", []))
    for pre in tracked.get("prefixes", []):
        groups |= {gid for gid, t in gname.items() if t.startswith(pre)}
    print(f"групп: {len(groups)}")

    meta = {"yearStart": cfg["yearStart"], "semester": cfg["semester"]}
    avail, teachers, fails = [], set(), 0
    for gid in sorted(groups):
        try:
            items = schedule(cfg, 0, gid)
        except Exception as e:
            print("  fail group", gid, e); fails += 1; continue
        teachers |= {x["pid"] for x in items if x.get("pid")}
        write(os.path.join(DATA, "0", f"{gid}.json"), {"title": gname.get(gid), "cfg": meta, "items": items})
        avail.append(f"0:{gid}")
        time.sleep(0.3)
    teachers |= set(tracked.get("teachers", []))
    print(f"преподавателей: {len(teachers)}")
    for tid in sorted(teachers):
        try:
            items = schedule(cfg, 1, tid)
        except Exception as e:
            print("  fail teacher", tid, e); fails += 1; continue
        write(os.path.join(DATA, "1", f"{tid}.json"), {"title": tname.get(tid), "cfg": meta, "items": items})
        avail.append(f"1:{tid}")
        time.sleep(0.3)
    # файлы, которые уже есть с прошлых запусков, тоже доступны офлайн
    for k in ("0", "1"):
        d = os.path.join(DATA, k)
        if os.path.isdir(d):
            avail += [f"{k}:{f[:-5]}" for f in os.listdir(d) if f.endswith(".json")]
    idx["a"] = sorted(set(avail))
    write(idx_path, idx)
    write(os.path.join(DATA, "meta.json"), {"date": date.today().isoformat()})
    print(f"готово, ошибок: {fails}")
    if fails and not avail:
        sys.exit(1)


if __name__ == "__main__":
    main()
