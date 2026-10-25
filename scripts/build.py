#!/usr/bin/env python3
"""Ежемесячная сборка данных о журналах из списка Финансового университета.

Источники (все открытые):
  * ЕГПНИ / «Белый список» — journalrank.rcsi.science: уровень, признаки WoS CC,
    Scopus, RSCI (ядро РИНЦ), перечня ВАК, квартили SJR/CiteScore, издатель;
  * DOAJ — сайт, страница для авторов, APC (стоимость), срок публикации;
  * Crossref, OpenAlex — издатель и сайт, если DOAJ не знает журнал;
  * сайты журналов — e-mail, телефон, упоминания стоимости и сроков;
  * data/vak.csv — категории ВАК К1–К3 и специальности (файл ВАК, см. README);
  * data/manual.csv — ручные правки, перекрывают всё остальное.

Запуск:  python3 scripts/build.py            (кэш живёт 25 дней)
         python3 scripts/build.py --fresh    (игнорировать кэш)
         python3 scripts/build.py --no-crawl (не обходить сайты журналов)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from match import Matcher  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = ROOT / "site" / "data"
EGPNI = "https://journalrank.rcsi.science"
UA = "Mozilla/5.0 (fu-journals monthly updater; +https://github.com/)"
CACHE_DAYS = 25

ARGS = None


# ---------------------------------------------------------------- загрузка

def fetch(url: str, *, binary=False, timeout=40, retries=2):
    """GET с файловым кэшем. Возвращает текст (или байты) либо None."""
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / "http" / key[:2] / key
    if path.exists() and not ARGS.fresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age < CACHE_DAYS:
            raw = path.read_bytes()
            if raw == b"\0ERR":
                return None
            return raw if binary else raw.decode("utf-8", "replace")
    raw = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
            if not binary:
                m = re.search(r"charset=([\w-]+)", ctype)
                enc = m.group(1) if m else _sniff_charset(raw)
                raw = raw.decode(enc, "replace").encode("utf-8")
            break
        except Exception:
            raw = None
            time.sleep(1.5 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if raw is not None else b"\0ERR")
    if raw is None:
        return None
    return raw if binary else raw.decode("utf-8")


def _sniff_charset(raw: bytes) -> str:
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
    return m.group(1).decode() if m else "utf-8"


def fetch_json(url: str):
    t = fetch(url)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


def text_of(fragment: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\xa0]+", " ", t)


# ---------------------------------------------------------------- входные файлы

def read_fu_list():
    lines = (DATA / "fu_list.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def read_csv(name: str, key: str):
    path = DATA / name
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    return {r[key].strip(): {k: (v or "").strip() for k, v in r.items()} for r in rows if r.get(key)}


# ---------------------------------------------------------------- ЕГПНИ

def load_egpni():
    print("ЕГПНИ: выгрузка JSON…", flush=True)
    t = fetch(f"{EGPNI}/ru/record-sources/download/?dataType=Json", timeout=600)
    if not t:
        sys.exit("Не удалось скачать выгрузку ЕГПНИ")
    return json.loads(t)


def current_level(rec):
    # «Для журнала действует последний из утверждённых уровней»
    for year in (2026, 2025, 2023):
        if rec.get(f"level_{year}"):
            return rec[f"level_{year}"], year
    return None, None


def egpni_card(issn: str):
    """id карточки, издатель, отметка ВАК — со страниц поиска и карточки."""
    page = fetch(f"{EGPNI}/ru/record-sources/?s={urllib.parse.quote(issn)}")
    if not page:
        return {}
    m = re.search(r'href="?/ru/record-sources/details/(\d+)/', page)
    if not m:
        return {}
    rid = m.group(1)
    out = {"egpni_id": rid}
    chunk = page[m.end(): m.end() + 3000]
    pm = re.search(r"<small class=\"col-12 tx-gray-500[^\"]*\">(.*?)</small>", chunk, re.S)
    if pm:
        out["publisher"] = text_of(pm.group(1)).strip().rstrip(".").strip()
    card = fetch(f"{EGPNI}/ru/record-sources/details/{rid}/") or ""
    vm = re.search(r'title="Последнее обновление: ([\d.]+)"[^>]*>Перечень ВАК<', card)
    out["vak_flag"] = bool(vm) or ">Перечень ВАК<" in card
    if vm:
        out["vak_flag_date"] = vm.group(1)
    return out


def egpni_quartiles(rid: str):
    page = fetch(f"{EGPNI}/ru/record-sources/quartiles/{rid}/?ps=100") or ""
    rows = re.findall(r"<tr data-id=(\w+) data-value=(Q\d)><td>[^<]*</td><td>([^<]*)</td><td>(\d{4})</td>", page)
    res = {}
    for kind, q, area, year in rows:
        res.setdefault(kind.lower(), []).append({"q": q, "area": html.unescape(area), "year": int(year)})
    best = {}
    for kind, items in res.items():
        last = max(i["year"] for i in items)
        cur = sorted((i for i in items if i["year"] == last), key=lambda i: i["q"])
        best[kind] = {"best": cur[0]["q"], "year": last, "areas": [{"area": i["area"], "q": i["q"]} for i in cur]}
    return best


# ---------------------------------------------------------------- DOAJ / Crossref / OpenAlex

def doaj(issns):
    for issn in issns:
        d = fetch_json(f"https://doaj.org/api/search/journals/issn:{issn}")
        if d and d.get("results"):
            b = d["results"][0]["bibjson"]
            apc = b.get("apc") or {}
            return {
                "site": (b.get("ref") or {}).get("journal"),
                "author_page": (b.get("ref") or {}).get("author_instructions"),
                "has_apc": apc.get("has_apc"),
                "apc_max": apc.get("max"),
                "apc_url": apc.get("url"),
                "weeks": b.get("publication_time_weeks"),
                "publisher": (b.get("publisher") or {}).get("name"),
                "review": ", ".join((b.get("editorial") or {}).get("review_process") or []),
            }
    return None


def crossref_publisher(issns):
    for issn in issns:
        d = fetch_json(f"https://api.crossref.org/journals/{issn}")
        if d and d.get("message"):
            return d["message"].get("publisher")
    return None


AGGREGATORS = re.compile(r"elibrary|cyberleninka|doi\.org|crossref|springer|sciencedirect|tandfonline|"
                         r"wiley|sagepub|mdpi|pleiades|journals\.rcsi|scilit", re.I)


def crossref_site(issns):
    """Сайт журнала по адресу его последней статьи в Crossref."""
    for issn in issns:
        d = fetch_json(f"https://api.crossref.org/journals/{issn}/works?rows=3&sort=published&order=desc")
        for w in ((d or {}).get("message") or {}).get("items", []):
            url = ((w.get("resource") or {}).get("primary") or {}).get("URL") or ""
            p = urllib.parse.urlparse(url)
            if not p.netloc or AGGREGATORS.search(p.netloc):
                continue
            m = re.match(r"(/(?:index\.php/)?[^/]+/)(?:article|issue|about)/", p.path) or \
                re.match(r"(/jour/)", p.path)
            return f"{p.scheme}://{p.netloc}{m.group(1) if m else '/'}"
    return None


def openalex(ids):
    """Пакетный запрос: {openalex_id: {...}}."""
    res = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 50):
        part = "|".join(ids[i:i + 50])
        d = fetch_json(
            "https://api.openalex.org/sources?per-page=50&select=id,homepage_url,host_organization_name,apc_usd"
            f"&filter=ids.openalex:{part}")
        for r in (d or {}).get("results", []):
            res[r["id"].rsplit("/", 1)[-1]] = r
    return res


# ---------------------------------------------------------------- сайт журнала

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zа-я]{2,}", re.I)
PHONE = re.compile(r"(?:\+7|8)[\s(-]*\d{3,5}[\s)-]*\d{1,3}[\s-]?\d{2}[\s-]?\d{2}")
LINK_HINT = re.compile(r"контакт|contact|автор|author|правила|требован|guideline|submission|подач|"
                       r"оплат|стоимост|публикац|редакц", re.I)
# Предложение считается упоминанием цены, только если речь о публикации статьи,
# а не о подписке, авторских экземплярах или содержании самих статей.
FEE = re.compile(r"(?:плат[аыуе]|оплат|стоимост|бесплатн|руб\.|рубл|\bAPC\b|processing charge|publication fee)"
                 r".{0,80}(?:публикац|стать|рукопис|опубликова|размещени|рецензирован|authors?)"
                 r"|(?:публикац|опубликова|стать|рукопис).{0,80}"
                 r"(?:бесплатн|платн|не взима|руб\.|рубл|оплат)", re.I)
FEE_SKIP = re.compile(r"подписк|экземпляр|гонорар|вознагражден", re.I)
TERM = re.compile(r"(?:срок\w*|период\w*|длительност\w*|в течение|занимает|составляет)"
                  r".{0,80}(?:рецензирован|публикац|рассмотрен|выход|опубликова)"
                  r"|(?:рецензирован|публикац|рассмотрен).{0,60}"
                  r"(?:\d+\s*[–—-]?\s*\d*\s*(?:месяц|мес\.|недел|дн|рабоч))", re.I)
# в упоминании срока должна быть сама длительность
DURATION = re.compile(r"\d+\s*(?:[–—-]\s*\d+\s*)?(?:календарн\w+ |рабоч\w+ )?(?:месяц|мес\.|недел|дн|day|week|month)"
                      r"|(?:одн\w+|дв\w+|тр\w+|четыр\w+|шест\w+|полугод)\s*(?:[–—-]\s*\w+\s*)?(?:месяц|недел)", re.I)
TERM_SKIP = re.compile(r"лицензи|претензи|не отвечают|снимается|возврат", re.I)
FREE = re.compile(r"не взима|бесплатн|без оплаты|без взимания", re.I)
BAD_MAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|example|sentry|wixpress|@2x", re.I)


def crawl_site(site: str, extra=()):
    home = fetch(site, timeout=25)
    if not home:
        return {"site_ok": False}
    pages = [(site, home)]
    links = []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', home, re.S | re.I):
        label = text_of(label).strip()
        if LINK_HINT.search(label) or LINK_HINT.search(href):
            url = urllib.parse.urljoin(site, href)
            if urllib.parse.urlparse(url).netloc == urllib.parse.urlparse(site).netloc and url not in links:
                links.append(url)
    # типовые адреса OJS: /about/submissions, /about/contact
    if re.search(r"/jour/|/index\.php/", site) or "ojs" in home.lower():
        base = site.rstrip("/")
        links = [base + "/about/submissions", base + "/about/contact"] + links
    for url in list(extra) + links[:6]:
        if url and url != site:
            t = fetch(url, timeout=25)
            if t:
                pages.append((url, t))

    emails, phones, fee, term = [], [], [], []
    for url, page in pages:
        mailtos = re.findall(r"mailto:([^\"'?>\s]+)", page)
        txt = text_of(page)
        for e in mailtos + EMAIL.findall(txt):
            e = urllib.parse.unquote(e).strip(".").lower()
            if not BAD_MAIL.search(e) and e not in emails:
                emails.append(e)
        for p in PHONE.findall(txt):
            p = re.sub(r"\s+", " ", p).strip()
            if p not in phones and len(re.sub(r"\D", "", p)) == 11:
                phones.append(p)
        for s in re.split(r"(?<=[.!?])\s+|\n+", txt):
            s = re.sub(r"\s+", " ", s).strip(" ,;:-•")
            if not 20 < len(s) < 340:
                continue
            for rx, bucket in ((FEE, fee), (TERM, term)):
                if rx is FEE and FEE_SKIP.search(s):
                    continue
                if rx is TERM and (not DURATION.search(s) or TERM_SKIP.search(s)):
                    continue
                if rx.search(s) and all(s != b["text"] for b in bucket):
                    bucket.append({"text": s, "url": url})
    return {"site_ok": True, "emails": emails[:4], "phones": phones[:3], "fee_mentions": fee[:3],
            "term_mentions": term[:3], "pages": [u for u, _ in pages]}


# ---------------------------------------------------------------- сборка

def slug(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()[:10]


def split_list(s: str):
    return [x.strip() for x in re.split(r"[,;|]", s or "") if x.strip()]


def build():
    names = read_fu_list()
    manual = read_csv("manual.csv", "name")
    vak = read_csv("vak.csv", "issn")
    records = load_egpni()
    matcher = Matcher(records)

    journals = []
    for name in names:
        man = manual.get(name, {})
        idx, how = matcher.find(name, man.get("issn") or None)
        rec = records[idx] if idx is not None else {}
        level, level_year = current_level(rec)
        issns = rec.get("issns") or split_list(man.get("issn", ""))
        journals.append({
            "id": slug(name),
            "name": name,
            "issns": issns,
            "match": how,
            "egpni_title": (rec.get("title") or [None])[0],
            "egpni": {
                "level": level,
                "level_year": level_year,
                "levels": {y: rec.get(f"level_{y}") for y in (2023, 2025, 2026) if rec.get(f"level_{y}")},
                "accepted": rec.get("date_accepted"),
                "discontinued": rec.get("date_discontinued"),
                "notice": rec.get("notice"),
            } if rec else None,
            "scopus": {"in": bool((rec.get("scopus") or {}).get("value"))},
            "wos": {"in": bool((rec.get("wos_cc") or {}).get("value"))},
            "rsci": bool((rec.get("rsci") or {}).get("value")),
            "doaj_flag": bool((rec.get("doaj") or {}).get("value")),
            "_oax": ((rec.get("oax") or {}).get("id") or [None])[0],
        })

    # карточки ЕГПНИ — не больше 4 запросов параллельно, чтобы не нагружать портал
    print(f"ЕГПНИ: карточки {len(journals)} журналов…", flush=True)
    with ThreadPoolExecutor(4) as ex:
        cards = list(ex.map(lambda j: egpni_card(j["issns"][0]) if j["issns"] and j["egpni"] else {}, journals))
    for j, c in zip(journals, cards):
        j["egpni_id"] = c.get("egpni_id")
        j["publisher"] = c.get("publisher")
        j["vak"] = {"in": c.get("vak_flag"), "flag_date": c.get("vak_flag_date")}
    print("ЕГПНИ: квартили Scopus…", flush=True)
    with ThreadPoolExecutor(4) as ex:
        quarts = list(ex.map(lambda j: egpni_quartiles(j["egpni_id"]) if j["egpni_id"] and j["scopus"]["in"] else {},
                             journals))
    for j, q in zip(journals, quarts):
        sjr, cs = q.get("sjr"), q.get("citescore")
        j["scopus"].update({
            "quartile": (sjr or cs or {}).get("best"),
            "quartile_src": "SJR" if sjr else ("CiteScore" if cs else None),
            "year": (sjr or cs or {}).get("year"),
            "areas": (sjr or cs or {}).get("areas", []),
            "citescore_q": (cs or {}).get("best"),
        })

    print("DOAJ / Crossref / OpenAlex…", flush=True)
    with ThreadPoolExecutor(8) as ex:
        doajs = list(ex.map(lambda j: doaj(j["issns"]) if j["issns"] else None, journals))
    oax = openalex([j["_oax"] for j in journals if j["_oax"]])
    for j, d in zip(journals, doajs):
        o = oax.get(j.pop("_oax") or "", {})
        j["doaj"] = d
        j["site"] = (d or {}).get("site") or o.get("homepage_url")
        j["author_page"] = (d or {}).get("author_page")
        if not j["publisher"]:
            j["publisher"] = (d or {}).get("publisher") or o.get("host_organization_name")
    with ThreadPoolExecutor(8) as ex:
        pubs = list(ex.map(lambda j: None if j["publisher"] or not j["issns"] else crossref_publisher(j["issns"]),
                           journals))
    for j, p in zip(journals, pubs):
        j["publisher"] = j["publisher"] or p
    with ThreadPoolExecutor(8) as ex:
        sites = list(ex.map(lambda j: None if j["site"] or not j["issns"] else crossref_site(j["issns"]), journals))
    for j, s in zip(journals, sites):
        j["site"] = j["site"] or s

    # ручные правки сайта нужны до обхода
    for j in journals:
        man = manual.get(j["name"], {})
        if man.get("site"):
            j["site"] = man["site"]

    if not ARGS.no_crawl:
        todo = [j for j in journals if j["site"]]
        print(f"Сайты журналов: {len(todo)}…", flush=True)
        with ThreadPoolExecutor(12) as ex:
            crawled = list(ex.map(lambda j: crawl_site(j["site"], [j["author_page"]]), todo))
        for j, c in zip(todo, crawled):
            j["crawl"] = c

    for j in journals:
        finalize(j, manual.get(j["name"], {}), vak)
    return journals


def finalize(j, man, vak):
    c = j.pop("crawl", None) or {}
    d = j.get("doaj") or {}

    # ВАК: категория и специальности — из файла ВАК, затем ручные правки
    v = next((vak[i] for i in j["issns"] if i in vak), {})
    cat = man.get("vak_category") or v.get("category") or ""
    specs = split_list(man.get("vak_specialties") or v.get("specialties") or "")
    j["vak"].update({"category": cat.upper().replace("K", "К") or None, "specialties": specs})
    if cat or specs:
        j["vak"]["in"] = True

    if man.get("wos_quartile"):
        j["wos"]["quartile"] = man["wos_quartile"].upper()
    # Журналы перечня ВАК и ЕГПНИ из российского списка индексируются в РИНЦ;
    # явное «нет» в manual.csv это перекрывает
    j["rinc"] = (man.get("rinc", "").lower() not in ("нет", "0", "false")) if man.get("rinc") else \
        bool(j["vak"]["in"] or j["egpni"] or j["rsci"])

    j["emails"] = split_list(man.get("email")) or c.get("emails", [])
    j["phones"] = split_list(man.get("phone")) or c.get("phones", [])
    j["site_ok"] = c.get("site_ok")

    price = None
    if man.get("price"):
        price = {"text": man["price"], "src": "manual"}
    elif d.get("has_apc") is False:
        price = {"text": "Бесплатно (плата за публикацию не взимается, по данным DOAJ)", "src": "doaj",
                 "url": d.get("apc_url")}
    elif d.get("has_apc") and d.get("apc_max"):
        m = d["apc_max"][0]
        price = {"text": f"{m.get('price')} {m.get('currency')} (APC, по данным DOAJ)", "src": "doaj",
                 "url": d.get("apc_url")}
    mentions = c.get("fee_mentions", [])
    if not price:
        free = next((m for m in mentions if FREE.search(m["text"])), None)
        if free:
            price = {"text": "Бесплатно (по данным сайта журнала)", "src": "site", "url": free["url"]}
    j["price"] = price
    j["price_mentions"] = [] if price and price["src"] == "manual" else mentions

    term = None
    if man.get("term"):
        term = {"text": man["term"], "src": "manual"}
    elif d.get("weeks"):
        term = {"text": f"≈ {d['weeks']} нед. от подачи до публикации (по данным DOAJ)", "src": "doaj"}
    j["term"] = term
    j["term_mentions"] = [] if term and term["src"] == "manual" else c.get("term_mentions", [])

    j["how_to_submit"] = man.get("how_to_submit") or default_howto(j)
    j["notes"] = man.get("notes") or None
    j["egpni_url"] = f"{EGPNI}/ru/record-sources/details/{j['egpni_id']}/" if j.get("egpni_id") else None
    j["review"] = d.get("review") or None
    j.pop("doaj", None)


def default_howto(j):
    steps = []
    page = j.get("author_page") or j.get("site")
    if page:
        steps.append(f"Изучите требования к оформлению и публикационную этику на сайте журнала: {page}")
    if j.get("site") and re.search(r"/jour/|/index\.php/", j["site"] + (j.get("author_page") or "")):
        steps.append("Зарегистрируйтесь в системе подачи (OJS) на сайте журнала и загрузите рукопись "
                     "с метаданными (аннотация, ключевые слова, сведения об авторах)")
    elif j.get("emails"):
        steps.append(f"Отправьте рукопись и сопроводительные документы на e-mail редакции: {j['emails'][0]}")
    else:
        steps.append("Подайте рукопись в редакцию способом, указанным на сайте журнала")
    steps.append("Проверьте текст на оригинальность до подачи; дождитесь результатов рецензирования "
                 "и ответьте на замечания рецензентов")
    return steps


CARRY = ("emails", "phones", "price_mentions", "term_mentions", "site")


def carry_over(old, new):
    """Если сайт журнала в этот раз не ответил (часть российских сайтов закрыта
    для зарубежных серверов), сохраняем найденное в прошлых выпусках."""
    prev = {j["id"]: j for j in old}
    for j in new:
        o = prev.get(j["id"])
        if not o or j.get("site_ok"):
            continue
        for k in CARRY:
            if not j.get(k) and o.get(k):
                j[k] = o[k]
        if not j.get("price") and (o.get("price") or {}).get("src") == "site":
            j["price"] = o["price"]
        if any(j.get(k) for k in CARRY[:4]):
            j["carried_from"] = o.get("carried_from") or o.get("updated_on")


def diff(old, new):
    """Изменения между выпусками: уровень ЕГПНИ, квартиль Scopus, ВАК, индексация."""
    prev = {j["id"]: j for j in old}
    changes = []
    for j in new:
        o = prev.get(j["id"])
        if not o:
            continue
        def g(x, path):
            for p in path.split("."):
                x = (x or {}).get(p)
            return x
        for path, label in (("egpni.level", "Уровень ЕГПНИ"), ("scopus.quartile", "Квартиль Scopus"),
                            ("scopus.in", "Scopus"), ("wos.in", "WoS CC"), ("rsci", "Ядро РИНЦ (RSCI)"),
                            ("vak.in", "Перечень ВАК"), ("vak.category", "Категория ВАК"),
                            ("wos.quartile", "Квартиль WoS")):
            a, b = g(o, path), g(j, path)
            if a != b and not (a in (None, False) and b in (None, False)):
                changes.append({"id": j["id"], "name": j["name"], "field": label, "from": a, "to": b})
    return changes


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--no-crawl", action="store_true")
    ARGS = ap.parse_args()

    journals = build()
    OUT.mkdir(parents=True, exist_ok=True)
    out_file = OUT / "journals.json"
    old = json.loads(out_file.read_text(encoding="utf-8"))["journals"] if out_file.exists() else []
    today = dt.date.today().isoformat()
    for j in journals:
        if j.get("site_ok"):
            j["updated_on"] = today
    carry_over(old, journals)

    hist_file = OUT / "changes.json"
    history = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else []
    ch = diff(old, journals)
    if ch:
        history = [{"date": today, "changes": ch}] + [h for h in history if h["date"] != today]
    hist_file.write_text(json.dumps(history[:24], ensure_ascii=False, indent=1), encoding="utf-8")

    meta = {
        "updated": today,
        "list_edition": "Редакция списка ФУ, вводится в действие с 01.04.2026",
        "total": len(journals),
        "matched_egpni": sum(1 for j in journals if j["egpni"]),
        "vak_file": (DATA / "vak.csv").exists(),
    }
    payload = json.dumps({"meta": meta, "journals": journals}, ensure_ascii=False, indent=1)
    out_file.write_text(payload, encoding="utf-8")
    # тот же набор данных в виде скрипта — чтобы index.html открывался и без веб-сервера
    (OUT / "journals.js").write_text(
        f"window.FU_DATA = {payload};\nwindow.FU_CHANGES = {json.dumps(history[:24], ensure_ascii=False)};\n",
        encoding="utf-8")
    unmatched = [j["name"] for j in journals if not j["egpni"]]
    print(f"Готово: {len(journals)} журналов, в ЕГПНИ найдено {meta['matched_egpni']}, изменений {len(ch)}")
    if unmatched:
        print("Не найдены в ЕГПНИ (укажите ISSN в data/manual.csv):\n  " + "\n  ".join(unmatched))


if __name__ == "__main__":
    main()
