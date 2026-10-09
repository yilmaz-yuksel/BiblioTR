"""TR Dizin gelişmiş arama sorguları: oluşturma ve denetleme.

TR Dizin'in gelişmiş arama formu dört kutuyu tek bir sorgu dizgisine çevirir. Sınamalarla
(2026-10-02, `api/defaultSearch/publication`) doğrulanan davranış:

- Alan sorgusu `(title : ( "beden" AND "eğitimi" ))` biçimindedir ve **en dıştaki parantez
  zorunludur**; parantezsiz gönderilen alan sorgusu ilgisiz kayıtlar döndürür.
- Alan içinde `AND`, `OR`, tırnaklı ifade ve `-( ... )` doğru çalışır (sayılar kümelerle tutarlı).
- Eşleşme tam sözcükledir: "eğitimi" araması "eğitiminde" geçen kayıtları getirmez.
- Alanlar arasındaki `|` bağlacı VEYA değil **VE** gibi çalışır (iki alanın kesişimi). Alanlar
  arası birleşim için her alan ayrı sorgulanıp sonuçlar kimliğe göre birleştirilmelidir
  (`field_queries(..., join="or")` her alan için ayrı sorgu döndürür).
- Ana (alansız) aramada çift tırnak desteklenmez: tırnaklı her sorgu 0 sonuç döndürür.
  Tırnaksız ana arama ise bulanık eşleştirir ve sonuçların çoğu ilgisiz olabilir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Sınanmış arama alanları (API adı -> görünen ad). Bilinmeyen alan adı HTTP 500 döndürür.
SEARCH_FIELDS: dict[str, str] = {"title": "Başlık", "abstract": "Özet", "keyword": "Anahtar kelime"}

_FIELD_QUERY_RE = re.compile(r"^\(*\s*[A-Za-z_.]+\s*:\s*\(")
_QUOTES = '"“”„'


def _words(text: str | None) -> list[str]:
    """Kutudaki metni sözcüklere ayırır; tırnak ve parantezler sorgu sözdizimini bozmasın diye atılır."""
    return [w for w in re.sub(rf"[{_QUOTES}()]", " ", text or "").split() if w]


def _phrase(text: str | None) -> str:
    return " ".join(_words(text))


def _groups(text: str | None) -> list[str]:
    """`;` ile ayrılmış sözcük grupları (her biri bir ifade)."""
    return [g for g in (_phrase(part) for part in (text or "").split(";")) if g]


@dataclass
class FieldTerms:
    """Gelişmiş arama formunun dört kutusu."""

    all_words: str = ""   # Kelimelerin tümünü içeren
    any_words: str = ""   # Kelimelerin herhangi birini içeren
    phrase: str = ""      # Kelime veya kelime grubunu aynen içeren
    exclude: str = ""     # Kelime veya kelime gruplarını içermeyen (`;` ile birden çok grup)

    def clauses(self) -> list[str]:
        out = [f'"{w}"' for w in _words(self.all_words)]
        any_ = _words(self.any_words)
        if any_:
            out.append("(" + " OR ".join(f'"{w}"' for w in any_) + ")")
        if _phrase(self.phrase):
            out.append(f'"{_phrase(self.phrase)}"')
        return out

    def expression(self) -> str:
        """Alan parantezinin içine yazılacak ifade; olumlu koşul yoksa boş dizgi."""
        positive = self.clauses()
        if not positive:
            return ""  # yalnızca dışlama, tüm dizini döndürür; sorgu sayılmaz
        return " AND ".join(positive + [f'-("{g}")' for g in _groups(self.exclude)])

    def label(self) -> str:
        """Veri seti adı önerisi için kısa metin."""
        return _phrase(self.phrase) or " ".join(_words(self.all_words)) or " ".join(_words(self.any_words))


def field_query(field: str, terms: FieldTerms) -> str:
    """Tek alan için sorgu: `(title : ( "beden" AND "eğitimi" ))`. Olumlu koşul yoksa boş dizgi."""
    expr = terms.expression()
    return f"({field} : ( {expr} ))" if expr else ""


def field_queries(fields: list[str], terms: FieldTerms, join: str = "or") -> list[tuple[str, str]]:
    """Seçilen alanlar için gönderilecek sorgular: [(etiket, sorgu), ...].

    - `join="or"`: her alan için ayrı sorgu (sonuçlar kimliğe göre birleştirilmelidir; TR Dizin
      alanlar arası VEYA'yı desteklemez).
    - `join="and"`: tek sorgu, alanlar `|` ile bağlanır (TR Dizin'de kesişim verir).
    """
    expr = terms.expression()
    if not expr or not fields:
        return []
    if len(fields) == 1 or join == "or":
        return [(SEARCH_FIELDS.get(f, f), f"({f} : ( {expr} ))") for f in fields]
    label = " ve ".join(SEARCH_FIELDS.get(f, f) for f in fields)
    return [(label, "(" + " | ".join(f"{f} : ( {expr} )" for f in fields) + ")")]


def is_field_query(q: str) -> bool:
    """Sorgu `alan : ( ... )` biçiminde mi?"""
    return bool(_FIELD_QUERY_RE.match(q.strip()))


def _balanced_outer(q: str) -> bool:
    """Dizgi, tamamını saran tek bir parantez çiftiyle mi başlayıp bitiyor?"""
    if not (q.startswith("(") and q.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(q):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(q) - 1:
            return False
    return depth == 0


def normalize_query(q: str) -> str:
    """Alan sorgusunda eksik dış parantezi ekler; diğer sorguları olduğu gibi bırakır."""
    q = (q or "").strip()
    if q and is_field_query(q) and not _balanced_outer(q):
        return f"({q})"
    return q


def query_notes(q: str) -> list[tuple[str, str]]:
    """Elle yazılmış sorgu için uyarılar: [("error" | "warning" | "info", ileti), ...]."""
    q = (q or "").strip()
    notes: list[tuple[str, str]] = []
    if not q:
        return notes
    if is_field_query(q):
        if not _balanced_outer(q):
            notes.append(("info", "Alan sorgusuna dış parantez eklendi; TR Dizin parantezsiz alan sorgusunu "
                                  f"doğru çözmüyor. Gönderilen sorgu: `{normalize_query(q)}`"))
        if re.search(r"\)\s*\|\s*[A-Za-z_.]+\s*:", q):
            notes.append(("warning", "Alanlar arasındaki `|` TR Dizin'de VEYA değil VE gibi çalışır (iki alanın "
                                     "kesişimi döner). Birleşim için *Gelişmiş* arama türünde alanları "
                                     "\"herhangi birinde\" seçeneğiyle arayın."))
    elif any(c in q for c in _QUOTES):
        notes.append(("error", "TR Dizin'in ana araması çift tırnağı desteklemez; tırnaklı sorgu 0 sonuç döndürür. "
                               "Tam ifade aramak için *Gelişmiş* arama türünü kullanın."))
    else:
        notes.append(("info", "Düz arama gevşek eşleştirir (benzer yazılışlı sözcükler de gelir); sonuçların "
                              "çoğu ilgisiz olabilir. Kesin eşleşme için *Gelişmiş* arama türünü kullanın."))
    return notes


# ---- ifade listesiyle alan sorgusu ve yinelemeli varyant keşfi ---------------------------------
#
# Gelişmiş arama tam sözcükle eşleştiği için Türkçe ekli biçimler ("fiziksel aktivitenin",
# "fiziksel aktiviteye") kök ifadeyle bulunmaz. Çözüm: bulunan kayıtların başlık, anahtar kelime ve
# özetlerinde geçen yeni biçimleri toplayıp aynı alan sorgusuna VEYA ile eklemek; yeni biçim
# çıkmayana (doygunluk) kadar tekrarlamak.

_TOKEN_RE = re.compile(r"\w+")


def tr_lower(text: str) -> str:
    """Türkçe kurallarıyla küçük harf ("İ" -> "i", "I" -> "ı")."""
    return text.replace("I", "ı").replace("İ", "i").lower()


def _fold_token(token: str) -> str:
    from .normalize import fold

    return fold(token)


def phrase_query(field: str, phrases: list[str], exclude: list[str] | None = None) -> str:
    """Bir alanda ifadelerden herhangi biri: `(title : ( "a b" OR "a c" ))`."""
    ph = [p for p in dict.fromkeys(_phrase(x) for x in phrases) if p]
    if not ph:
        return ""
    expr = " OR ".join(f'"{p}"' for p in ph)
    if len(ph) > 1:
        expr = f"({expr})"
    ex = [g for g in (_phrase(x) for x in (exclude or [])) if g]
    if ex:
        expr = " AND ".join([expr] + [f'-("{g}")' for g in ex])
    return f"({field} : ( {expr} ))"


def phrase_queries(field: str, phrases: list[str], exclude: list[str] | None = None,
                   max_terms: int = 20) -> list[str]:
    """`phrase_query` ama uzun listeler parçalara bölünür (adres uzunluğu ve sunucu sınırları için)."""
    ph = [p for p in dict.fromkeys(_phrase(x) for x in phrases) if p]
    return [q for q in (phrase_query(field, ph[i:i + max_terms], exclude) for i in range(0, len(ph), max_terms)) if q]


def publication_field_texts(pub, fields: tuple[str, ...] = ("title", "keyword", "abstract")) -> dict[str, list[str]]:
    """Yayının alan metinleri: başlık (tüm dillerde), anahtar kelimeler, özet."""
    out: dict[str, list[str]] = {}
    if "title" in fields:
        out["title"] = [t for t in dict.fromkeys((pub.title, pub.title_tr, pub.title_en)) if t]
    if "keyword" in fields:
        out["keyword"] = list(pub.keywords)
    if "abstract" in fields:
        out["abstract"] = [pub.abstract] if pub.abstract else []
    return out


def _seed_tokens(seed: str) -> list[str]:
    return [_fold_token(t) for t in _TOKEN_RE.findall(seed)]


def _matches(texts: list[str], seeds: list[list[str]], min_last: int = 3):
    """Metinlerde tohum ifadelerin ekli biçimlerini bulur: [(katlanmış biçim, görünen biçim), ...].

    Son sözcük dışındakiler birebir (katlanmış), son sözcük tohumun son sözcüğüyle başlamalıdır.
    """
    found = []
    for text in texts:
        toks = _TOKEN_RE.findall(text)
        folded = [_fold_token(t) for t in toks]
        for seed in seeds:
            n = len(seed)
            if not n or len(seed[-1]) < min_last:
                continue
            for i in range(len(toks) - n + 1):
                if folded[i:i + n - 1] == seed[:-1] and folded[i + n - 1].startswith(seed[-1]):
                    found.append((" ".join(folded[i:i + n]), tr_lower(" ".join(toks[i:i + n]))))
    return found


def discover_phrase_variants(pubs, seeds: list[str], fields: tuple[str, ...] = ("title", "keyword", "abstract")
                             ) -> dict[str, dict]:
    """Tohum ifadelerin kayıtlarda geçen tüm biçimleri.

    Dönüş: {katlanmış biçim: {"form": en sık görünen yazım, "records": kayıt sayısı,
    "fields": {alan: kayıt sayısı}}}. Katlanmış biçim, aynı sözcüğün Türkçe karakterli ve
    karaktersiz yazımlarını tek biçimde toplar; sorguda en sık görünen yazım kullanılır.
    """
    seed_toks = [t for t in (_seed_tokens(s) for s in seeds) if t]
    spellings: dict[str, dict[str, int]] = {}
    records: dict[str, int] = {}
    by_field: dict[str, dict[str, int]] = {}
    for p in pubs:
        seen: set[str] = set()
        seen_field: dict[str, set[str]] = {}
        for field, texts in publication_field_texts(p, fields).items():
            for key, surface in _matches(texts, seed_toks):
                spellings.setdefault(key, {})
                spellings[key][surface] = spellings[key].get(surface, 0) + 1
                seen.add(key)
                seen_field.setdefault(field, set()).add(key)
        for key in seen:
            records[key] = records.get(key, 0) + 1
        for field, keys in seen_field.items():
            for key in keys:
                by_field.setdefault(key, {})
                by_field[key][field] = by_field[key].get(field, 0) + 1
    return {k: {"form": max(spellings[k], key=lambda s: (spellings[k][s], s)), "records": records[k],
                "fields": by_field.get(k, {})}
            for k in sorted(records, key=lambda k: (-records[k], k))}


def contains_phrase(pub, phrases: list[str], field: str) -> bool:
    """Yayının ilgili alanında ifadelerden biri tam sözcüklerle geçiyor mu (sözcüksel kesinlik denetimi)."""
    targets = [t for t in (_seed_tokens(p) for p in phrases) if t]
    for text in publication_field_texts(pub, (field,)).get(field, []):
        folded = [_fold_token(t) for t in _TOKEN_RE.findall(text)]
        for t in targets:
            n = len(t)
            if any(folded[i:i + n] == t for i in range(len(folded) - n + 1)):
                return True
    return False
