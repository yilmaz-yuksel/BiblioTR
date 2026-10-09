"""Eşanlamlılar sözlüğü: yazar, kurum ve anahtar kelime birleştirme.

Aynı birim farklı yazımlarla ayrı sayılmasın diye onaylanmış eşleşmeler saklanır ve
analizlerden, dışa aktarımlardan önce yayınlara uygulanır. Ham veri değişmez; sözlük
ayrı tutulur, dosyaya yazılıp başka projelerde kullanılabilir.

- `suggest()`: birleştirme önerileri üretir (hiçbiri kendiliğinden uygulanmaz).
- `apply()`: sözlüğü uygulanmış yayın kopyaları döndürür.
- `vosviewer_thesaurus()` / `bibliometrix_synonyms()`: araçların sözlük dosyaları.

Anahtar kelime ve kurum girdileri yazımın `fold()` biçimiyle eşleşir (büyük/küçük harf ve
Türkçe karakter farkı önemsizdir). Yazar girdileri yazar anahtarıyla (`id:`, `orcid:`,
`name:`) eşleşir, çünkü aynı adı taşıyan farklı kişiler ada göre birleştirilmemelidir.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from itertools import combinations
from typing import Iterable, Sequence

import pandas as pd

from .analysis import _author_labels, _split_institutions, author_key
from .normalize import Author, Publication, fold

FIELDS = ("keyword", "institution", "author")
FIELD_LABELS = {"keyword": "Anahtar kelime", "institution": "Kurum", "author": "Yazar"}

# Varsayılan benzerlik eşikleri (difflib oranı, 0-1). Kurumlarda daha yüksek: "Gazi" ile
# "Gaziosmanpaşa" gibi gerçekten farklı kurumlar birbirine benzeyebilir.
DEFAULT_THRESHOLD = {"keyword": 0.88, "institution": 0.93, "author": 1.0}


# ---- sözlük -------------------------------------------------------------------

@dataclass
class Thesaurus:
    # alan -> {fold(varyant): (varyant yazımı, hedef)}; yazarda anahtar -> (ad, hedef anahtar)
    entries: dict[str, dict[str, tuple[str, str]]] = field(default_factory=lambda: {f: {} for f in FIELDS})
    # yazar hedef anahtarı -> görünen ad
    author_names: dict[str, str] = field(default_factory=dict)
    # alan -> {frozenset({a, b})}: "ayrı tut" denmiş çiftler (bir daha önerilmez)
    separate: dict[str, set[frozenset[str]]] = field(default_factory=lambda: {f: set() for f in FIELDS})

    def __len__(self) -> int:
        return sum(len(v) for v in self.entries.values())

    @staticmethod
    def key(field_: str, value: str) -> str:
        return value if field_ == "author" else fold(value)

    def add(self, field_: str, variant: str, target: str, name: str | None = None) -> None:
        """`variant` artık `target` olarak sayılır. Yazarda ikisi de yazar anahtarıdır."""
        k, tk = self.key(field_, variant), self.key(field_, target)
        if k == tk:
            if field_ == "author" and name:
                self.author_names[tk] = name
            return
        # Hedefin kendisi başka bir hedefe bağlıysa zincir oluşmasın, doğrudan son hedefe bağla.
        target = self.resolve(field_, target)
        tk = self.key(field_, target)
        if k == tk:
            return
        self.entries[field_][k] = (variant, target)
        # Bu varyanta bağlı eski girdileri yeni hedefe taşı.
        for vk, (v, t) in list(self.entries[field_].items()):
            if self.key(field_, t) == k:
                self.entries[field_][vk] = (v, target)
        if field_ == "author" and name:
            self.author_names[tk] = name

    def remove(self, field_: str, variant: str) -> bool:
        return self.entries[field_].pop(self.key(field_, variant), None) is not None

    def keep_separate(self, field_: str, a: str, b: str) -> None:
        self.separate[field_].add(frozenset((self.key(field_, a), self.key(field_, b))))

    def is_separate(self, field_: str, a: str, b: str) -> bool:
        return frozenset((self.key(field_, a), self.key(field_, b))) in self.separate[field_]

    def resolve(self, field_: str, value: str) -> str:
        """Değerin sözlükteki hedefi (yoksa kendisi)."""
        seen = set()
        m = self.entries[field_]
        while True:
            k = self.key(field_, value)
            if k in seen or k not in m:
                return value
            seen.add(k)
            value = m[k][1]

    def mapping(self, field_: str) -> dict[str, list[str]]:
        """Hedef -> varyant yazımları."""
        out: dict[str, list[str]] = defaultdict(list)
        for variant, target in self.entries[field_].values():
            out[target].append(variant)
        return dict(out)

    def frame(self) -> pd.DataFrame:
        rows = []
        for f in FIELDS:
            for variant, target in self.entries[f].values():
                if f == "author":
                    variant_shown = f"{self.author_names.get(variant, variant)} ({variant})"
                    shown = f"{self.author_names.get(target, target)} ({target})"
                else:
                    variant_shown, shown = variant, target
                rows.append({"Alan": FIELD_LABELS[f], "Varyant": variant_shown, "Hedef": shown, "field": f,
                             "key": variant})
        return pd.DataFrame(rows, columns=["Alan", "Varyant", "Hedef", "field", "key"])

    # ---- dosya ----

    def to_dict(self) -> dict:
        return {
            "format": "trdizin-thesaurus",
            "version": 1,
            "entries": {f: [{"variant": v, "target": t} for v, t in self.entries[f].values()] for f in FIELDS},
            "author_names": dict(self.author_names),
            "keep_separate": {f: [sorted(p) for p in self.separate[f]] for f in FIELDS},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Thesaurus":
        t = cls()
        t.merge(data)
        return t

    @classmethod
    def from_json(cls, text: str) -> "Thesaurus":
        return cls.from_dict(json.loads(text))

    def merge(self, data: "dict | Thesaurus") -> int:
        """Başka bir sözlüğün girdilerini ekler; eklenen girdi sayısını döndürür."""
        if isinstance(data, Thesaurus):
            data = data.to_dict()
        before = len(self)
        self.author_names.update(data.get("author_names", {}))
        for f in FIELDS:
            for e in data.get("entries", {}).get(f, []):
                self.add(f, e["variant"], e["target"])
            for pair in data.get("keep_separate", {}).get(f, []):
                if len(pair) == 2:
                    self.keep_separate(f, *pair)
        return len(self) - before


# ---- uygulama -------------------------------------------------------------------

def _dedupe(values: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for v in values:
        seen.setdefault(fold(v), v)
    return list(seen.values())


def _split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(";")]


def _apply_institutions(a: Author, thes: Thesaurus) -> Author:
    insts = _split_institutions(a)
    if not insts or not thes.entries["institution"]:
        return a
    mapped = [thes.resolve("institution", i) for i in insts]
    if mapped == insts:
        return a
    raw = _split(a.institution)
    cities, countries = _split(a.city), _split(a.country)
    # Şehir ve ülke kurum listesiyle paralel ise, birleşen kurumun şehrini de düşür.
    parallel = len(raw) == len(insts) and len(cities) in (len(insts), 1) and len(countries) in (len(insts), 1)
    keep, seen = [], set()
    for i, m in enumerate(mapped):
        if fold(m) not in seen:
            seen.add(fold(m))
            keep.append(i)
    new_inst = "; ".join(mapped[i] for i in keep)
    if not parallel:
        return replace(a, institution=new_inst)

    def pick(vals: list[str], attr: str | None) -> str | None:
        if len(vals) == 1:
            return attr
        return "; ".join(vals[i] for i in keep)

    return replace(a, institution=new_inst, city=pick(cities, a.city), country=pick(countries, a.country))


def _set_author_key(a: Author, target: str, name: str | None) -> Author:
    kind, _, value = target.partition(":")
    if kind == "id":
        a = replace(a, author_id=value)
    elif kind == "orcid":
        a = replace(a, author_id=None, orcid=value)
    else:
        a = replace(a, author_id=None, orcid=None)
    return replace(a, name=name) if name else a


def apply(pubs: Sequence[Publication], thes: Thesaurus | None) -> list[Publication]:
    """Sözlüğü uygulanmış yayın kopyaları (girdiler değişmez)."""
    if thes is None or (not len(thes) and not thes.author_names):
        return list(pubs)
    kw = bool(thes.entries["keyword"])
    au = thes.entries["author"]
    out = []
    for p in pubs:
        changes: dict = {}
        if kw:
            changes["keywords"] = _dedupe(thes.resolve("keyword", k) for k in p.keywords)
            changes["keywords_by_language"] = {
                lang: _dedupe(thes.resolve("keyword", k) for k in ks) for lang, ks in p.keywords_by_language.items()
            }
        authors = []
        for a in p.authors:
            a = _apply_institutions(a, thes)
            k = author_key(a)
            target = thes.resolve("author", k)
            if target != k:
                a = _set_author_key(a, target, thes.author_names.get(target))
            elif k in thes.author_names:
                a = replace(a, name=thes.author_names[k])
            authors.append(a)
        changes["authors"] = authors
        out.append(replace(p, **changes))
    return out


# ---- öneriler -------------------------------------------------------------------

@dataclass
class Suggestion:
    field: str
    target: str                      # önerilen hedef (yazarda anahtar)
    target_label: str                # görünen ad
    members: list[tuple[str, str, int]]  # (anahtar/yazım, görünen ad, yayın sayısı); hedef hariç
    target_count: int
    reason: str
    score: float

    @property
    def total(self) -> int:
        return self.target_count + sum(m[2] for m in self.members)


_INST_ABBR = [
    (r"\bt\s*c\b", ""),
    (r"\b(univ|uni|universites[i]?|universite|university|universitat|universidad|universita)\b", "universitesi"),
    (r"\b(fak|fakultesi|faculty)\b", "fakultesi"),
    (r"\b(hast|hastanesi|hospital)\b", "hastanesi"),
    (r"\b(egt|egitim)\b", "egitim"),
    (r"\b(arst|arastirma)\b", "arastirma"),
    (r"\b(bak|bakanligi|ministry)\b", "bakanligi"),
    (r"\b(enst|enstitusu|institute)\b", "enstitusu"),
    (r"\b(md|mudurlugu)\b", "mudurlugu"),
    (r"\b(vakif|vakfi)\b", "vakif"),
]


def _stem(token: str) -> str:
    """Çok kaba çoğul eki atma (yalnız öneri gruplaması için)."""
    if len(token) > 5 and token.endswith(("ler", "lar")):
        return token[:-3]
    if len(token) > 5 and token.endswith(("yses", "eses")):  # analyses -> analysis
        return token[:-2] + "is"
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is", "ys")):
        return token[:-1]
    return token


def normal_key(field_: str, value: str) -> str:
    """Öneri gruplaması için sıkı normal biçim: noktalama, kısaltma ve çoğul farkları silinir."""
    t = fold(value)
    t = re.sub(r"[\"'`’().,;:/\\&\-–_]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if field_ == "institution":
        for pat, rep in _INST_ABBR:
            t = re.sub(pat, rep, t)
        t = re.sub(r"\b(ve|and|of|the)\b", " ", t)
    else:
        t = " ".join(_stem(w) for w in t.split())
    return re.sub(r"\s+", " ", t).strip()


def _values(pubs: Sequence[Publication], field_: str) -> tuple[Counter, dict[str, Counter]]:
    """fold(yazım) -> yayın sayısı ve her fold anahtarı için yazım sıklıkları."""
    counts: Counter = Counter()
    spellings: dict[str, Counter] = defaultdict(Counter)
    for p in pubs:
        if field_ == "keyword":
            vals = p.keywords
        else:
            vals = [i for a in p.authors for i in _split_institutions(a)]
        seen = set()
        for v in vals:
            k = fold(v)
            spellings[k][v] += 1
            if k not in seen:
                seen.add(k)
                counts[k] += 1
    return counts, spellings


def _label(spellings: Counter) -> str:
    # En sık yazım; eşitlikte tamamı büyük harf olmayanı tercih et.
    return max(spellings, key=lambda s: (spellings[s], not s.isupper(), -len(s)))


def _digits(s: str) -> set[str]:
    return set(re.findall(r"\d+", s))


def _similar_pairs(keys: list[str], norm: dict[str, str], threshold: float) -> list[tuple[str, str, float]]:
    """Normal biçimleri birbirine yakın anahtar çiftleri (ilk 3 harfe göre bloklanır)."""
    blocks: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        n = norm[k].replace(" ", "")
        if len(n) >= 4:
            blocks[n[:3]].append(k)
    out = []
    for block in blocks.values():
        block.sort(key=lambda k: len(norm[k]))
        for i, a in enumerate(block):
            na = norm[a]
            for b in block[i + 1:]:
                nb = norm[b]
                if len(nb) - len(na) > max(3, 0.25 * len(na)):
                    break
                if _digits(na) != _digits(nb):
                    continue
                sm = SequenceMatcher(None, na, nb)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
                if r >= threshold:
                    out.append((a, b, r))
    return out


def _language_pairs(pubs: Sequence[Publication], min_pairs: int) -> list[tuple[str, str, int]]:
    """Aynı yayınlarda birlikte verilen Türkçe ve İngilizce anahtar kelime çiftleri.

    İki kanıt birleştirilir: (1) iki listede aynı sırada duran terimler; (2) sıradan bağımsız birliktelik.
    Dergiler İngilizce listeyi çoğu zaman farklı sırayla verdiğinden (ör. MeSH sırası) yalnız sıraya
    bakmak "Obezite" / "Obesity" gibi açık karşılıkları kaçırır. Birliktelikte bir çift, iki terim de
    birbirinin en sık eşi olduğunda (eşitlik yoksa) ve iki terimin iki dilli yayınlarının en az %60'ında
    birlikte geçtiğinde önerilir."""
    pos: Counter = Counter()
    pos_term: Counter = Counter()
    co: Counter = Counter()
    tr_n: Counter = Counter()
    en_n: Counter = Counter()
    for p in pubs:
        tr = list(dict.fromkeys(fold(k) for k in p.keywords_by_language.get("TUR") or []))
        en = list(dict.fromkeys(fold(k) for k in p.keywords_by_language.get("ENG") or []))
        if not tr or not en:
            continue
        raw_tr = p.keywords_by_language.get("TUR") or []
        raw_en = p.keywords_by_language.get("ENG") or []
        if len(raw_tr) == len(raw_en):
            for a, b in zip(raw_tr, raw_en):
                fa, fb = fold(a), fold(b)
                if fa != fb:
                    pos[(fa, fb)] += 1
                    pos_term[fa] += 1
                    pos_term[fb] += 1
        tr_n.update(tr)
        en_n.update(en)
        for a in tr:
            for b in en:
                if a != b:
                    co[(a, b)] += 1
    best_tr: dict[str, list[tuple[int, str]]] = defaultdict(list)
    best_en: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (a, b), n in co.items():
        if n >= min_pairs:
            best_tr[a].append((n, b))
            best_en[b].append((n, a))

    def top(cands: list[tuple[int, str]]) -> str | None:
        cands = sorted(cands, reverse=True)
        return cands[0][1] if len(cands) == 1 or cands[0][0] > cands[1][0] else None

    found: dict[tuple[str, str], int] = {}
    tr_of: dict[str, str] = {}
    en_of: dict[str, str] = {}
    for a, cands in best_tr.items():
        b = top(cands)
        if b is None or top(best_en[b]) != a:
            continue
        tr_of[b], en_of[a] = a, b
        n = co[(a, b)]
        if n / tr_n[a] >= 0.6 and n / en_n[b] >= 0.6:
            found[(a, b)] = n
    for (a, b), n in pos.items():
        # Terimin sıra eşleşmelerinin çoğu bu çiftse güvenilir say; birliktelik başka bir eşi gösteriyorsa sayma
        # (listeler farklı sıradaysa sıra eşleşmesi yanlış çift üretir).
        if en_of.get(a, b) != b or tr_of.get(b, a) != a:
            continue
        if n >= min_pairs and n / pos_term[a] >= 0.6 and n / pos_term[b] >= 0.6:
            found[(a, b)] = max(found.get((a, b), 0), n)
    return sorted((a, b, n) for (a, b), n in found.items())


def _value_suggestions(pubs: Sequence[Publication], field_: str, thes: Thesaurus, threshold: float,
                       min_language_pairs: int) -> list[Suggestion]:
    counts, spellings = _values(pubs, field_)
    labels = {k: _label(spellings[k]) for k in counts}
    norm = {k: normal_key(field_, labels[k]) for k in counts}
    groups: dict[str, set[str]] = {}  # temsilci -> üyeler
    reason: dict[str, str] = {}
    score: dict[str, float] = {}

    # 1) Aynı normal biçim: yazım, noktalama, kısaltma veya çoğul farkı.
    by_norm: dict[str, list[str]] = defaultdict(list)
    for k, n in norm.items():
        by_norm[n].append(k)
    member_of: dict[str, str] = {}
    for ks in by_norm.values():
        r = max(ks, key=lambda k: (counts[k], -len(k)))
        for k in ks:
            member_of[k] = r
        if len(ks) > 1:
            groups[r] = set(ks)
            reason[r] = "Yazım farkı (noktalama, kısaltma veya çoğul eki)"
            score[r] = 1.0

    # 2) Benzer yazılış: her temsilci kendisinden sık geçen en benzer temsilciye bağlanır;
    # bağlantılar en sık geçen köke kadar izlenir.
    reps = sorted(set(member_of.values()))
    parent: dict[str, tuple[float, str]] = {}
    for a, b, r in _similar_pairs(reps, norm, threshold):
        if thes.is_separate(field_, labels[a], labels[b]):
            continue
        small, big = sorted((a, b), key=lambda k: (counts[k], -len(k), k))
        if r > parent.get(small, (0.0, ""))[0]:
            parent[small] = (r, big)
    for small in list(parent):
        root, seen, r_min = small, {small}, 1.0
        while root in parent and parent[root][1] not in seen:
            r_min = min(r_min, parent[root][0])
            root = parent[root][1]
            seen.add(root)
        if root == small:
            continue
        members = groups.pop(small, {small})
        groups.setdefault(root, {root}).update(members)
        for k in members:
            member_of[k] = root
        reason[root] = ("Benzer yazılış" if reason.get(root, "Benzer yazılış") == "Benzer yazılış"
                        else reason[root].split(" + ")[0] + " + benzer yazılış")
        score[root] = min(score.get(root, 1.0), r_min)

    out = []
    for r, members in groups.items():
        mem = [k for k in members if k != r and not thes.is_separate(field_, labels[k], labels[r])]
        if mem:
            out.append(Suggestion(field_, labels[r], labels[r], [(labels[k], labels[k], counts[k]) for k in mem],
                                  counts[r], reason[r], round(score[r], 3)))

    # 3) Türkçe-İngilizce karşılıklar (yalnız anahtar kelime).
    if field_ == "keyword" and min_language_pairs:
        for a, b, n in _language_pairs(pubs, min_language_pairs):
            ra, rb = member_of.get(a, a), member_of.get(b, b)
            if ra == rb or thes.is_separate(field_, labels[a], labels[b]):
                continue
            # Hedef: daha sık geçen (eşitlikte İngilizce).
            tgt, var = (a, b) if counts[a] > counts[b] else (b, a)
            out.append(Suggestion(field_, labels[tgt], labels[tgt], [(labels[var], labels[var], counts[var])],
                                  counts[tgt], f"Türkçe-İngilizce karşılık ({n} yayında birlikte)", round(n / 100, 3)))
    out.sort(key=lambda s: (-s.score, -s.total))
    return out


def _author_suggestions(pubs: Sequence[Publication], thes: Thesaurus) -> list[Suggestion]:
    labels = _author_labels(pubs)
    counts: Counter = Counter()
    orcids: dict[str, set[str]] = defaultdict(set)
    insts: dict[str, set[str]] = defaultdict(set)
    for p in pubs:
        for k in dict.fromkeys(author_key(a) for a in p.authors):
            counts[k] += 1
        for a in p.authors:
            k = author_key(a)
            if a.orcid:
                orcids[k].add(a.orcid.strip().upper())
            insts[k].update(fold(i) for i in _split_institutions(a))

    pairs: dict[frozenset, str] = {}
    by_orcid: dict[str, list[str]] = defaultdict(list)
    for k, os_ in orcids.items():
        for o in os_:
            by_orcid[o].append(k)
    for o, ks in by_orcid.items():
        for a, b in combinations(sorted(set(ks)), 2):
            pairs[frozenset((a, b))] = "Aynı ORCID"
    by_name: dict[str, list[str]] = defaultdict(list)
    for k in counts:
        by_name[fold(labels[k])].append(k)
    for ks in by_name.values():
        for a, b in combinations(sorted(ks), 2):
            pair = frozenset((a, b))
            if pair in pairs:
                continue
            if a.startswith("name:") or b.startswith("name:"):
                pairs[pair] = "Kimliksiz kayıt, aynı ad"
            elif insts[a] & insts[b] and not (orcids[a] and orcids[b] and orcids[a] != orcids[b]):
                pairs[pair] = "Aynı ad ve ortak kurum (olası çift profil)"

    out = []
    for pair, why in pairs.items():
        a, b = sorted(pair, key=lambda k: (-counts[k], k.startswith("name:"), k))
        if thes.is_separate("author", a, b):
            continue
        out.append(Suggestion("author", a, labels[a], [(b, labels[b], counts[b])], counts[a], why,
                              1.0 if why == "Aynı ORCID" else 0.9))
    out.sort(key=lambda s: (-s.score, -s.total))
    return out


def suggest(pubs: Sequence[Publication], field_: str, thes: Thesaurus | None = None,
            threshold: float | None = None, min_language_pairs: int = 2) -> list[Suggestion]:
    """Birleştirme önerileri. Sözlükteki girdiler önce uygulanır, "ayrı tut" çiftleri atlanır."""
    thes = Thesaurus() if thes is None else thes
    pubs = apply(pubs, thes)
    if field_ == "author":
        return _author_suggestions(pubs, thes)
    if field_ not in ("keyword", "institution"):
        raise ValueError(f"Bilinmeyen alan: {field_}")
    return _value_suggestions(pubs, field_, thes, threshold or DEFAULT_THRESHOLD[field_], min_language_pairs)


def suggestions_frame(sugs: Sequence[Suggestion]) -> pd.DataFrame:
    rows = []
    for i, s in enumerate(sugs):
        rows.append({
            "no": i,
            "Uygula": False,
            "Hedef": s.target_label,
            "Birleşecekler": " | ".join(f"{m[1]} ({m[2]})" for m in s.members),
            "Hedef yayın": s.target_count,
            "Toplam yayın": s.total,
            "Neden": s.reason,
            "Benzerlik": s.score,
        })
    return pd.DataFrame(rows, columns=["no", "Uygula", "Hedef", "Birleşecekler", "Hedef yayın", "Toplam yayın",
                                       "Neden", "Benzerlik"])


def accept(thes: Thesaurus, s: Suggestion, target_label: str | None = None) -> None:
    """Öneriyi sözlüğe ekler. `target_label` verilirse hedef yazım olarak o kullanılır."""
    if s.field == "author":
        name = target_label or s.target_label
        for key, label, _ in s.members:
            thes.author_names.setdefault(key, label)  # varyantın adı (VOSviewer sözlüğü için)
            thes.add("author", key, s.target, name=name)
        thes.author_names[s.target] = name
        return
    target = (target_label or s.target_label).strip()
    for variant, _, _ in s.members:
        thes.add(s.field, variant, target)
    if fold(target) != fold(s.target_label):
        thes.add(s.field, s.target_label, target)


def reject(thes: Thesaurus, s: Suggestion) -> None:
    for key, _, _ in s.members:
        thes.keep_separate(s.field, key, s.target)


# ---- kurala dayalı yazar birleştirme ----------------------------------------------

AUTHOR_RULES = ("none", "orcid", "orcid+institution")


def _author_evidence(pubs: Sequence[Publication]) -> tuple[Counter, dict, dict, dict]:
    """Yazar anahtarı başına yayın sayısı, ORCID'ler, kurumlar ve katlanmış ad yazımları."""
    counts: Counter = Counter()
    orcids: dict[str, set[str]] = defaultdict(set)
    insts: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    for p in pubs:
        for k in dict.fromkeys(author_key(a) for a in p.authors):
            counts[k] += 1
        for a in p.authors:
            k = author_key(a)
            if a.orcid:
                orcids[k].add(a.orcid.strip().upper())
            insts[k].update(fold(i) for i in _split_institutions(a))
            names[k].add(fold(a.name))
    return counts, orcids, insts, names


def author_rule_pairs(pubs: Sequence[Publication], rule: str) -> list[tuple[str, str, str]]:
    """Kurala göre birleşecek yazar anahtarı çiftleri: (a, b, dayanak).

    - "orcid": aynı ORCID'i taşıyan anahtarlar.
    - "orcid+institution": ayrıca aynı adı (herhangi bir yazımı) taşıyan ve en az bir kurumu
      ortak olan anahtarlar; ikisinin de ORCID'i varsa ve hiç ortak değilse birleştirilmez.
    """
    if rule not in AUTHOR_RULES:
        raise ValueError(f"Bilinmeyen kural: {rule} (seçenekler: {', '.join(AUTHOR_RULES)})")
    if rule == "none":
        return []
    counts, orcids, insts, names = _author_evidence(pubs)
    pairs: dict[frozenset, str] = {}
    by_orcid: dict[str, set[str]] = defaultdict(set)
    for k, os_ in orcids.items():
        for o in os_:
            by_orcid[o].add(k)
    for ks in by_orcid.values():
        for a, b in combinations(sorted(ks), 2):
            pairs[frozenset((a, b))] = "orcid"
    if rule == "orcid+institution":
        by_name: dict[str, set[str]] = defaultdict(set)
        for k, ns in names.items():
            for n in ns:
                by_name[n].add(k)
        for ks in by_name.values():
            for a, b in combinations(sorted(ks), 2):
                pair = frozenset((a, b))
                if pair in pairs or not (insts[a] & insts[b]):
                    continue
                if orcids[a] and orcids[b] and orcids[a].isdisjoint(orcids[b]):
                    continue
                pairs[pair] = "institution"
    return [(*sorted(pair), why) for pair, why in sorted(pairs.items(), key=lambda kv: sorted(kv[0]))]


def author_clusters(pubs: Sequence[Publication], rule: str, thes: Thesaurus | None = None) -> dict[str, str]:
    """Kurala göre yazar anahtarı -> küme temsilcisi (en çok yayını olan anahtar).

    "Ayrı tut" denmiş çiftler doğrudan birleştirilmez. Yalnızca birleşen anahtarlar döner.
    """
    counts = _author_evidence(pubs)[0]
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in author_rule_pairs(pubs, rule):
        if thes is not None and thes.is_separate("author", a, b):
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list[str]] = defaultdict(list)
    for k in list(parent):
        groups[find(k)].append(k)
    out: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        head = min(members, key=lambda k: (-counts[k], k.startswith("name:"), k))
        for k in members:
            out[k] = head
    return out


def apply_author_rule(pubs: Sequence[Publication], rule: str, thes: Thesaurus | None = None
                      ) -> tuple[Thesaurus, int]:
    """Kuralın birleştirmelerini eklenmiş bir sözlük kopyası ve birleşen anahtar sayısı.

    Var olan sözlük önce uygulanır; kural yalnızca kalan anahtarlara bakar. Girdi sözlüğü değişmez.
    """
    new = Thesaurus.from_dict(thes.to_dict()) if thes is not None else Thesaurus()
    if rule == "none":
        return new, 0
    current = apply(pubs, new)
    labels = _author_labels(current)
    clusters = author_clusters(current, rule, new)
    merged = 0
    for k, head in clusters.items():
        if k == head:
            continue
        new.author_names.setdefault(k, labels.get(k, k))
        new.add("author", k, head, name=new.author_names.get(head) or labels.get(head))
        merged += 1
    return new, merged


# ---- aynı adlı farklı yazarlar ---------------------------------------------------

def homonyms(pubs: Sequence[Publication]) -> pd.DataFrame:
    """Kısa adı (Soyad, baş harfler) aynı olan farklı yazar kimlikleri.

    Bibliometrix ve VOSviewer yazarları yalnızca adla tanıdığı için bu kişiler orada tek kişi
    sayılır. WoS dışa aktarımı bunları ayırt edilebilir adlarla yazar (`wos.author_map`).
    """
    from .wos import author_short, ascii_fold

    labels = _author_labels(pubs)
    counts: Counter = Counter()
    insts: dict[str, Counter] = defaultdict(Counter)
    for p in pubs:
        for a in p.authors:
            k = author_key(a)
            counts[k] += 1
            insts[k].update(_split_institutions(a))
    by_short: dict[str, list[str]] = defaultdict(list)
    for k in counts:
        by_short[(ascii_fold(author_short(labels[k])) or "").upper()].append(k)
    rows = []
    for short, ks in sorted(by_short.items()):
        if len(ks) < 2:
            continue
        for k in sorted(ks, key=lambda k: -counts[k]):
            rows.append({"Kısa ad": short, "Yazar": labels[k], "Kimlik": k, "Yayın": counts[k],
                         "Kurum": "; ".join(i for i, _ in insts[k].most_common(2))})
    return pd.DataFrame(rows, columns=["Kısa ad", "Yazar", "Kimlik", "Yayın", "Kurum"])


# ---- araç dosyaları ----------------------------------------------------------------

def _ascii(s: str) -> str:
    from .wos import ascii_fold

    return ascii_fold(s) or s


def vosviewer_thesaurus(thes: Thesaurus, field_: str) -> str:
    """VOSviewer thesaurus dosyası (sekmeyle ayrılmış `label` / `replace by`).

    VOSviewer terimleri küçük harfe çevirerek eşleştirir; Türkçe karakterli ve ASCII biçimleri
    birlikte yazılır ki dosya ASCII'ye çevrilmiş WoS dışa aktarımıyla da çalışsın.
    """
    lines = ["label\treplace by"]
    seen = set()
    if field_ == "author":
        from .wos import author_short

        for variant_key, target in thes.entries["author"].values():
            name = thes.author_names.get(target)
            if not name:
                continue
            new = _ascii(author_short(name)).lower()
            # Varyant yazımı bilinmiyorsa (yalnız kimlik) satır üretilemez.
            old = thes.author_names.get(variant_key)
            if old and _ascii(author_short(old)).lower() != new:
                lines.append(f"{_ascii(author_short(old)).lower()}\t{new}")
        return "\n".join(lines) + "\n"
    for variant, target in thes.entries[field_].values():
        tgt = target.lower()
        for v in dict.fromkeys((variant.lower(), _ascii(variant).lower())):
            t = tgt if v == variant.lower() else _ascii(target).lower()
            if v != t and v not in seen:
                seen.add(v)
                lines.append(f"{v}\t{t}")
    return "\n".join(lines) + "\n"


def bibliometrix_synonyms(thes: Thesaurus, field_: str) -> str:
    """Bibliometrix `synonyms` listesi: her satır "HEDEF;VARYANT1;VARYANT2" (ilk terim kalır).

    Biblioshiny'de ağ ve tematik analizlerin "synonyms" dosya kutusuna yüklenebilir. Bibliometrix
    anahtar kelimeleri büyük harfe çevirdiği için terimler büyük harfle ve ASCII olarak yazılır.
    """
    lines = []
    for target, variants in sorted(thes.mapping(field_).items()):
        if field_ == "author":
            continue
        terms = [_ascii(target).upper()] + [_ascii(v).upper() for v in variants]
        terms = list(dict.fromkeys(t.replace(";", " ").strip() for t in terms))
        if len(terms) > 1:
            lines.append(";".join(terms))
    return "\n".join(lines) + ("\n" if lines else "")
