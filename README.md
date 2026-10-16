# Расписание УУНиТ в Telegram

Mini App с расписанием УУНиТ: те же данные, что в приложении «Расписание УУНиТ» и на schedule.uust.ru, но удобнее. Работает на телефоне в России без VPN.

**Приложение:** https://ethancole304-wq.github.io/uust-schedule/
**Бот:** [@RadmirAkhtyamov_bot](https://t.me/RadmirAkhtyamov_bot), кнопка «Расписание»

## Что умеет

- **День:** текущая пара с прогрессом, «через сколько» следующая, перерывы и окна, цвета по типу занятия.
- **Неделя:** вся неделя одним списком.
- **Сессия:** экзамены, зачёты и консультации с отсчётом дней.
- **Карточка пары:** подробности и заметка или домашка. Заметки синхронизируются через Telegram CloudStorage.
- **Поиск групп и преподавателей.** По тапу на фамилию открывается расписание преподавателя.
- **Без связи:** открывается мгновенно из сохранённой копии и работает офлайн (кэш + service worker).
- **Полноэкранный режим в Telegram** и ярлык на главный экран.
- **Бот:** напоминание за 10 минут до пары и сообщение, если расписание на ближайшие 2 недели изменилось.
- **Умный поиск:** понимает английскую раскладку (`ghj403` → ПРО-403), транслит (`uzbekova`), опечатки и «фамилия + инициалы».
- **Погода** у каждой пары и «☔ возьми зонт» (Open-Meteo, без ключа).
- **Праздники и переносы** из производственного календаря (isdayoff.ru + Nager.Date), а не из зашитого списка.
- **В календарь телефона:** подписка `.ics` для iPhone и Google Календаря, обновляется сама (страница `cal.html`).
- **Навык для OpenClaw:** `openclaw/uust-schedule` — спроси своего ИИ-ассистента «какие пары завтра».

## Как устроено

```
schedule.uust.ru API (dev.uust-time.ru)
        │
        ├── GitHub Actions (scripts/update.py)
        │     ├─ каждые 3 часа: группы из tracked.json + их преподаватели
        │     └─ раз в сутки (02:40 Уфа): все группы и преподаватели + список групп
        │     → коммитит data/<тип>/<id>.json, data/idx.json,
        │       data/calendar.json (isdayoff + Nager), data/ics/<тип>/<id>.ics
        │
        ├── GitHub Pages: index.html + data/  ← телефон открывает отсюда (не Cloudflare)
        │
        └── Cloudflare Worker (worker/worker.js) — бот
              ├─ webhook Telegram: кнопка «Расписание», настройки уведомлений
              ├─ крон */5:      напоминания за 10 минут
              ├─ крон 7 */3:    проверка изменений на 14 дней вперёд
              └─ /api/schedule, /api/search, /api/idx — запасной источник (с VPN)
```

Почему так: в России Cloudflare замедляют, поэтому страницы с `*.workers.dev` на телефоне без VPN не открываются. GitHub Pages открывается нормально. Сам бот общается только с серверами Telegram, поэтому на Cloudflare ему ничего не мешает.

## Файлы

| Путь | Что это |
|---|---|
| `index.html` | Mini App (один файл, без сборки) |
| `sw.js` | service worker для офлайна |
| `data/` | данные, их пишет только GitHub Actions |
| `tracked.json` | какие группы обновлять каждые 3 часа (`groups` — id, `prefixes` — начало названия) |
| `scripts/update.py` | скачивание расписаний (`tracked` / `full`) |
| `.github/workflows/update.yml` | расписание запусков |
| `cal.html` | подписка на расписание в календаре телефона |
| `worker/worker.js` | бот для Cloudflare Workers |
| `openclaw/uust-schedule/` | навык для OpenClaw (`SKILL.md` + `uust.py`, только stdlib) |

## Обслуживание

- **Запустить обновление вручную:** Actions → update-schedule → Run workflow (`full` — все группы).
- **Смена семестра:** номер семестра и дату начала года скрипт сам берёт с schedule.uust.ru. Если в логе Actions появилось предупреждение «беру значения по умолчанию», поправь `DEF` в `scripts/update.py` и `DEFAULTS` в `worker/worker.js`.
- **Обновить бота:** Cloudflare → Workers & Pages → `uust-schedule-bot` → Edit code → вставить `worker/worker.js` → Deploy. Нужны KV-привязка `KV` и кроны `*/5 * * * *` и `7 */3 * * *`.
- **Адрес приложения для кнопки** хранится в KV-ключе `app_url`.

## OpenClaw

```
cp -r openclaw/uust-schedule ~/.openclaw/workspace/skills/
```

Дальше можно задать `UUST_GROUP=ПРО-403Б` и спрашивать ассистента в любом мессенджере: «какие пары завтра», «когда экзамены».

## Откуда идеи

- [public-apis](https://github.com/public-apis/public-apis): isdayoff.ru и Nager.Date (календарь), Open-Meteo (погода) — всё бесплатно и без ключей.
- [build-your-own-x](https://github.com/codecrafters-io/build-your-own-x): поиск с исправлением раскладки и опечаток (Дамерау–Левенштейн), свой генератор iCalendar (RFC 5545) без библиотек.
- [openclaw](https://github.com/openclaw/openclaw): формат навыков `SKILL.md`.
