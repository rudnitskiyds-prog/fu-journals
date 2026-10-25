"""Сопоставление названий из списка ФУ с записями ЕГПНИ («Белого списка»)."""
from __future__ import annotations
import difflib
import re

_LOOKALIKE = dict(zip("ACEHKMOPTX", "АСЕНКМОРТХ"))


def norm(title: str) -> str:
    t = title.upper().replace("Ё", "Е").replace("&AMP;", "&")
    # латинские двойники в русских словах («Cерия» с латинской C)
    t = re.sub(r"[ACEHKMOPTX](?=[А-Я])", lambda m: _LOOKALIKE[m.group()], t)
    t = re.sub(r"[«»\"'“”„]", "", t)
    t = re.sub(r"[^\w]+", " ", t)
    t = re.sub(r"\bИМ\b", "ИМЕНИ", t)
    return re.sub(r"\s+", " ", t).strip()


def _base(n: str) -> str:
    """Название без «имени ...» и прочих хвостов — для мягкого сравнения."""
    return re.sub(r"\bИМЕНИ( [А-ЯA-Z]){1,3} [А-ЯA-Z]+\b", "", n).replace("  ", " ").strip()


def _series_no(n: str):
    m = re.search(r"СЕРИЯ (\d+)", n)
    return m.group(1) if m else None


class Matcher:
    def __init__(self, records):
        # records: список записей ЕГПНИ; действующие версии важнее исключённых
        self.records = records
        self.exact = {}
        self.base = {}
        for idx, r in enumerate(records):
            for t in r.get("title", []):
                n = norm(t)
                for table, key in ((self.exact, n), (self.base, _base(n))):
                    prev = table.get(key)
                    if prev is None or (_discontinued(records[prev]) and not _discontinued(r)):
                        table[key] = idx
        self.keys = list(self.base)

    def find(self, name: str, issn=None):
        """Возвращает (индекс записи, способ сопоставления) или (None, None)."""
        if issn:
            for idx, r in enumerate(self.records):
                if issn in r.get("issns", []) and not _discontinued(r):
                    return idx, "issn"
        n = norm(name)
        if n in self.exact:
            return self.exact[n], "exact"
        b = _base(n)
        if b in self.base:
            return self.base[b], "exact"
        cands = difflib.get_close_matches(b, self.keys, n=5, cutoff=0.85)
        for c in cands:
            # «Серия 18» не должна совпасть с «Серия 19», «Политология» — с «Биологией»
            if _series_no(c) == _series_no(b) and _same_words(b, c):
                return self.base[c], "fuzzy"
        return None, None


_STOP = {"СЕРИЯ", "И", "AND", "РАН", "ИМЕНИ", "AMP", "НОВАЯ"}


def _same_words(a: str, b: str) -> bool:
    """Каждое значимое слово одного названия есть (почти дословно) в другом."""
    wa = [w for w in a.split() if w not in _STOP]
    wb = [w for w in b.split() if w not in _STOP]
    for x, y in ((wa, wb), (wb, wa)):
        for w in x:
            if w not in y and not difflib.get_close_matches(w, y, n=1, cutoff=0.8):
                return False
    return True


def _discontinued(r) -> bool:
    return bool(r.get("date_discontinued"))
