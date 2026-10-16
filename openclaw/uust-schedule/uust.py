#!/usr/bin/env python3
"""CLI расписания УУНиТ для OpenClaw. Только stdlib."""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("UUST_BASE", "https://ethancole304-wq.github.io/uust-schedule/data/")
UFA = timezone(timedelta(hours=5))
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
EXAM = ("экзамен", "зачет", "зачёт", "консультац")


def get(path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "uust-skill"}), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(s):
    return "".join(c for c in str(s).lower().replace("ё", "е") if c.isalnum())


def find(q):
    idx = get("idx.json")
    if q[:2] in ("g:", "t:") and q[2:].isdigit():
        return (0 if q[0] == "g" else 1), int(q[2:]), q
    n, res = norm(q), []
    for k, lst in ((0, idx["g"]), (1, idx["t"])):
        for i, t in lst:
            key = norm(t)
            if key.startswith(n):
                res.append((0 if key == n else 1, k, i, t))
            elif n in key:
                res.append((2, k, i, t))
    res.sort(key=lambda r: (r[0], len(r[3])))
    return res


def load(q):
    r = find(q)
    if isinstance(r, tuple):
        k, i, t = r
    elif not r:
        sys.exit("Не нашёл «%s»" % q)
    else:
        _, k, i, t = r[0]
    d = get("%d/%d.json" % (k, i))
    return d, d.get("title") or t


def calendar():
    try:
        return set(get("calendar.json").get("holidays", []))
    except Exception:
        return set()


def week_of(d, cfg):
    y = datetime.strptime(cfg["yearStart"], "%Y-%m-%d").date()
    y -= timedelta(days=y.weekday())
    return (d - y).days // 7 + 1


def lessons(d, data, hol):
    if d.isoformat() in hol:
        return []
    w = week_of(d, data["cfg"])
    return sorted((x for x in data["items"] if x.get("d") == d.isoweekday() and w in (x.get("w") or [])), key=lambda x: x.get("n") or 0)


def line(x):
    room = ", ".join(v for v in (x.get("r"), x.get("bs")) if v)
    who = (x.get("p") or "").strip(" .") and x["p"] or x.get("g") or ""
    return ("  %s  %s (%s)%s%s" % (x.get("t", "—"), x.get("s", "—"), x.get("y") or "—", " · " + room if room else "", " · " + who if who else "")).rstrip()


def day(d, data, hol, title=True):
    ls = lessons(d, data, hol)
    head = "%s, %s" % (WD[d.weekday()], d.strftime("%d.%m"))
    if d.isoformat() in hol:
        return head + ": праздник, пар нет"
    return head + (":\n" + "\n".join(map(line, ls)) if ls else ": пар нет")


def main(a):
    if len(a) < 1:
        sys.exit(__doc__ + "\nкоманды: search|today|tomorrow|week|next|exams|date")
    cmd, rest = a[0], a[1:]
    now = datetime.now(UFA)
    if cmd == "search":
        r = find(" ".join(rest))
        for _, k, i, t in (r if isinstance(r, list) else [])[:10]:
            print("%s  %s:%d" % (t, "g" if k == 0 else "t", i))
        return
    when = None
    if cmd == "date":
        when, rest = datetime.strptime(rest[0], "%Y-%m-%d").date(), rest[1:]
    q = " ".join(rest) or os.environ.get("UUST_GROUP", "")
    if not q:
        sys.exit("Укажи группу или задай UUST_GROUP")
    data, title = load(q)
    hol, today = calendar(), now.date()
    print("📅 " + title)
    if cmd in ("today", "tomorrow", "date"):
        print(day(when or today + timedelta(days=cmd == "tomorrow"), data, hol))
    elif cmd == "week":
        mon = today - timedelta(days=today.weekday())
        print("%d неделя" % week_of(today, data["cfg"]))
        print("\n".join(day(mon + timedelta(i), data, hol) for i in range(6)))
    elif cmd == "next":
        cur = now.hour * 60 + now.minute
        for i in range(14):
            d = today + timedelta(i)
            for x in lessons(d, data, hol):
                h, m = map(int, x["t"].split("-")[0].split(":"))
                if i or h * 60 + m > cur:
                    print("%s, %s:\n%s" % (WD[d.weekday()], d.strftime("%d.%m"), line(x)))
                    return
        print("В ближайшие 2 недели пар нет")
    elif cmd == "exams":
        y = datetime.strptime(data["cfg"]["yearStart"], "%Y-%m-%d").date()
        y -= timedelta(days=y.weekday())
        ex = []
        for x in data["items"]:
            if any(k in (x.get("y") or "").lower() for k in EXAM):
                for w in x.get("w") or []:
                    d = y + timedelta(weeks=w - 1, days=x["d"] - 1)
                    if d >= today:
                        ex.append((d, x))
        ex.sort(key=lambda e: (e[0], e[1].get("n") or 0))
        print("\n".join("%s %s (через %d дн.)\n%s" % (WD[d.weekday()], d.strftime("%d.%m"), (d - today).days, line(x)) for d, x in ex) or "Экзаменов в расписании пока нет")
    else:
        sys.exit("неизвестная команда " + cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
