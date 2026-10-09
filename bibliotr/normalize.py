"""Ham TR Dizin yayın kayıtlarını düz, analiz edilebilir bir yapıya çevirir.

TR Dizin yanıt şeması resmi olarak belgelenmemiştir. Bu yüzden her alan için
birden çok aday anahtar denenir. Gerçek yanıtta bir alan farklı adla geliyorsa
yalnızca aşağıdaki `*_KEYS` listelerini güncellemek yeterlidir.
Gerçek şemayı görmek için: `bibliotr inspect "sorgu"`.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .client import detail_url

# ---- alan adı adayları (sırayla denenir, noktalı yol desteklenir) -----------
# İlk aday, gerçek TR Dizin yanıtında (api/defaultSearch ve api/publicationById)
# görülen alandır; diğerleri yedektir.

ID_KEYS = ("id", "_id", "publicationId", "code")
# Asıl başlık `abstracts[].title` içindedir (dile göre). `orderTitle` sıralama içindir
# ve bazen boşlukları kaybolmuş olabilir, bu yüzden en son denenir.
TITLE_KEYS = ("title", "publicationTitle", "orderTitle")
YEAR_KEYS = ("publicationYear", "year", "publication_year", "issue.year")
DOI_KEYS = ("doi", "DOI", "doiNumber")
CITATION_KEYS = ("orderCitationCount", "citationCount", "citedByCount", "citation_count")
LANGUAGE_KEYS = ("language", "publicationLanguage", "languages.0")
DOC_TYPE_KEYS = ("docType", "documentType")
PUB_TYPE_KEYS = ("publicationType", "type")
ACCESS_KEYS = ("accessType", "access")
DATABASE_KEYS = ("databases", "database", "journalDatabase", "journal.database")
JOURNAL_NAME_KEYS = ("journal.name", "journal.title", "journalName", "journal_name")
JOURNAL_ID_KEYS = ("journal.id", "journalId")
ISSN_KEYS = ("journal.issn", "issn")
EISSN_KEYS = ("journal.eissn", "eissn", "journal.eIssn")
VOLUME_KEYS = ("issue.volume", "volume", "journal.volume")
ISSUE_KEYS = ("issue.number", "issueNumber", "number", "issue")
PUBLISH_DATE_KEYS = ("issue.publishDate", "publishDate")
START_PAGE_KEYS = ("startPage", "firstPage", "pageStart")
END_PAGE_KEYS = ("endPage", "lastPage", "pageEnd")
AUTHORS_KEYS = ("authors", "authorList", "authorNames")
ABSTRACTS_KEYS = ("abstracts", "abstract", "summaries")
KEYWORDS_KEYS = ("keywords", "keywordList", "keyword")
SUBJECTS_KEYS = ("subjects", "subject", "subjectNames")
REFERENCES_KEYS = ("references", "referenceList")
# Bu yayına atıf yapan TR Dizin yayınları (adı yanıltıcı olsa da gelen atıflardır).
CITING_KEYS = ("citedReferences", "citingPublications")
OTHER_CITATIONS_KEYS = ("otherCitations",)
VIEW_KEYS = ("viewCount",)
DOWNLOAD_KEYS = ("downloadCount",)

AUTHOR_NAME_KEYS = ("inPublicationName", "name", "fullName", "authorName", "nameSurname")
AUTHOR_ID_KEYS = ("authorId", "id", "code")
AUTHOR_ORDER_KEYS = ("order", "position", "sequence")
AUTHOR_ORCID_KEYS = ("orcid", "orcidId")
# Standart (TR Dizin'in eşleştirdiği) kurum. `rootTitle` üst kurumdur (ör. üniversite);
# `title` alt birim olabilir (ör. "ANATOMİ ANABİLİM DALI"). Alanlar liste olarak gelir
# (yazarın birden çok kurumu olabilir).
AUTHOR_INST_KEYS = ("institution.rootTitle", "institution.title", "institution.name", "institution")
AUTHOR_INST_CODE_KEYS = ("institution.rootCode", "institution.code")
AUTHOR_INST_TYPE_KEYS = ("institution.type",)
AUTHOR_INST_STATUS_KEYS = ("institution.status",)
# Yazarın makalede yazdığı ham adres
AUTHOR_AFFILIATION_KEYS = ("institutionName", "affiliation", "affiliations", "inPublicationInstitution")
AUTHOR_CITY_KEYS = ("institution.city", "city", "institutionCity")
AUTHOR_COUNTRY_KEYS = ("institution.country", "country", "institutionCountry")

# TR Dizin'in kurum eşleştiremediği durumlarda kullandığı yer tutucular
UNMATCHED_INSTITUTIONS = {"tanimlanmamis kurum", "ascd", "yabanci kurumlar"}
DEFAULT_COUNTRY = "Türkiye"  # yurt içi eşleşmiş kurumlarda ülke alanı boş gelir
UNMATCHED_COUNTRIES = {"diger"}

_MISSING = object()


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """`a.b.0.c` biçimindeki yolu izleyerek değeri döndürür."""
    node = obj
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return default
            node = node[part]
        elif isinstance(node, list) and part.isdigit():
            i = int(part)
            if i >= len(node):
                return default
            node = node[i]
        else:
            return default
    return node


def first(obj: Any, keys: Iterable[str], default: Any = None) -> Any:
    """Adaylar arasından boş olmayan ilk değeri döndürür."""
    for key in keys:
        v = get_path(obj, key, _MISSING)
        if v is _MISSING or v is None or v == "" or v == []:
            continue
        return v
    return default


def first_scalar(obj: Any, keys: Iterable[str], default: Any = None) -> Any:
    """`first` gibi, ancak dict/list değerleri atlar (ör. `issue` bir nesne olabilir)."""
    for key in keys:
        v = get_path(obj, key)
        if v is None or v == "" or isinstance(v, (dict, list)):
            continue
        return v
    return default


# Metinlerde zaman zaman görülen görünmez kontrol karakterleri (sekme/satır sonu hariç)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def as_text(value: Any) -> str | None:
    """Dizgi, sayı, dict (`name`/`title` alanlı) veya listeyi okunabilir metne çevirir."""
    if value is None:
        return None
    if isinstance(value, str):
        # NFC: TR Dizin bazı alanlarda ayrışık Unicode verir ("u" + birleşik iki nokta); birleşik
        # biçime çevrilmezse aynı ad iki farklı dizgi sayılır ("Türkiye" ve "Tu\u0308rkiye").
        s = unicodedata.normalize("NFC", _CONTROL_CHARS.sub("", value)).strip()
        return s or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return as_text(first(value, ("name", "title", "fullName", "value", "label", "keyword")))
    if isinstance(value, list):
        parts = [t for t in (as_text(v) for v in value) if t]
        return "; ".join(parts) or None
    return str(value)


def as_list(value: Any) -> list[str]:
    """Değeri metin listesine çevirir. `;` ile ayrılmış dizgileri de böler."""
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[;\n]", value) if p.strip()]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str):
                out.extend(as_list(v))
            else:
                t = as_text(v)
                if t:
                    out.append(t)
        return out
    t = as_text(value)
    return [t] if t else []


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):  # OverflowError: "inf"
        m = re.search(r"\d{4}", str(value))
        return int(m.group()) if m else None


def fold(text: str) -> str:
    """Karşılaştırma için Türkçe karakter duyarlı küçük harfe çevirme ve aksan temizleme."""
    text = text.replace("I", "ı").replace("İ", "i").lower()
    text = text.replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def clean_doi(doi: Any) -> str | None:
    s = as_text(doi)
    if not s:
        return None
    s = re.sub(r"^(https?://(dx\.)?doi\.org/|doi\s*:?\s*(?=10\.))", "", s.strip(), flags=re.I)
    return s or None


_ORCID_RE = re.compile(r"^(?:https?://orcid\.org/)?(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dX])$", re.I)


def orcid_status(value: Any) -> str | None:
    """ORCID'in biçim ve denetim hanesi durumu: "valid", "bad_format", "bad_checksum"; boşsa None.

    Denetim hanesi ISO 7064 MOD 11-2 ile hesaplanır (ORCID'in kendi kuralı). Biçim hatası çoğu kez
    eksik ya da fazla rakamdır; denetim hanesi hatası ise yanlış yazılmış bir rakamı gösterir.
    """
    s = as_text(value)
    if not s:
        return None
    m = _ORCID_RE.match(s.strip())
    if not m:
        return "bad_format"
    digits = "".join(m.groups()).upper()
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return "valid" if digits[-1] == ("X" if check == 10 else str(check)) else "bad_checksum"


@dataclass
class Author:
    name: str
    position: int
    author_id: str | None = None
    orcid: str | None = None
    institution: str | None = None  # standart kurum adı; eşleşmemişse adresten tahmin
    city: str | None = None
    country: str | None = None
    affiliation: str | None = None  # makaledeki ham adres
    institution_code: str | None = None
    institution_type: str | None = None  # DEVLETUNIVERSITE, VAKIFUNIVERSITE, ...
    institution_matched: bool = False  # kurum TR Dizin tarafından eşleştirilmiş mi
    institution_status: str | None = None  # KAMU, OZEL, ...
    unit: str | None = None  # alt birim (fakülte, bölüm, anabilim dalı)


@dataclass
class Reference:
    text: str
    position: int
    target_id: str | None = None  # kaynak TR Dizin'de ise yayın kimliği
    year: int | None = None

    @property
    def doi(self) -> str | None:
        return extract_doi(self.text)

    @property
    def key(self) -> str:
        return reference_key(self)

    @property
    def label(self) -> str:
        return reference_label(self)


@dataclass
class Publication:
    id: str
    title: str | None
    year: int | None
    doi: str | None
    citation_count: int | None
    language: str | None
    document_type: str | None
    publication_type: str | None
    access_type: str | None
    database: str | None
    journal: str | None
    journal_id: str | None
    issn: str | None
    eissn: str | None
    volume: str | None
    issue: str | None
    start_page: str | None
    end_page: str | None
    abstract: str | None
    url: str
    title_en: str | None = None
    # Türkçe başlık (TR Dizin'de İngilizce yayınların çoğunda vardır, ama her zaman değil)
    title_tr: str | None = None
    publish_date: str | None = None
    citations_crossref: int | None = None
    citations_openalex: int | None = None
    citations_opencitations: int | None = None
    view_count: int | None = None
    download_count: int | None = None
    authors: list[Author] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    cited_by: list[str] = field(default_factory=list)  # atıf yapan TR Dizin yayın kimlikleri
    keywords_by_language: dict[str, list[str]] = field(default_factory=dict)  # {"TUR": [...], "ENG": [...]}
    has_details: bool = False  # kaynakça/atıf alanları yanıtta var mıydı
    # OpenAlex'ten doğrudan alınan bilgiler (`bibliotr openalex`; ham TR Dizin kaydında yoktur).
    # `citations_openalex` TR Dizin'in bildirdiği değerdir ve sistematik olarak eksik olabilir.
    openalex_id: str | None = None
    openalex_citations: int | None = None
    openalex_year: int | None = None
    openalex_matched_by: str | None = None  # "doi" | "title"

    @property
    def author_names(self) -> list[str]:
        return [a.name for a in self.authors]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# TR Dizin'de kurum girilmemiş yazarlar için görülen yer tutucular ("Girilmemiş", "Girilmemişş"...).
UNMATCHED_PREFIXES = ("girilmemis",)


def _is_unmatched(value: str | None, placeholders: set[str]) -> bool:
    if not value:
        return True
    f = fold(value)
    return f in placeholders or (placeholders is UNMATCHED_INSTITUTIONS and f.startswith(UNMATCHED_PREFIXES))


def _values(raw: Any, keys: Iterable[str]) -> list[str]:
    """Liste veya tekil değeri boş olmayan metin listesine çevirir (sırayı korur)."""
    v = first(raw, keys)
    items = v if isinstance(v, list) else [v]
    return [t for t in (as_text(x) for x in items) if t]


def _aligned(raw: Any, keys: Iterable[str], n: int | None = None) -> list[str]:
    """`_values` gibi, ancak boş değerleri atmaz: paralel listelerde sıra (indeks) korunur.

    `n` verilirse liste o uzunluğa kırpılır ya da boş dizgiyle tamamlanır.
    """
    v = first(raw, keys)
    items = v if isinstance(v, list) else [] if v is None else [v]
    out = [as_text(x) or "" for x in items]
    return out if n is None else (out + [""] * n)[:n]


# Adres parçasının yer adı değil birim/kurum olduğunu gösteren (katlanmış) sözcük kökleri.
_NOT_A_PLACE = re.compile(
    r"univers|fakult|facult|depart|bolum|anabilim|bilim dali|enstit|institut|school|okul|college|kolej|"
    r"laborat|cent(er|re)\b|merkez|hastane|hospital|clinic|klini|akadem|academy|campus|kampus|mudurlu|bakanli|ministry|"
    r"engineering|muhendis|research|arastirma|division|program|street|avenue|\broad\b|cadde|sokak|mahalle")


def _place_name(part: str) -> str | None:
    """Adres parçası yer adına benziyorsa posta kodu atılmış halini, benzemiyorsa None döndürür.

    Yalnızca posta kodundan ya da eyalet kısaltmasından ("40100", "CB2 1PZ", "OH") oluşan parça
    için boş dizgi döner: şehir bir önceki parçada aranmalıdır.
    """
    name = " ".join(w for w in part.split() if not any(c.isdigit() for c in w)).strip(" .-")
    if len(name) < 3:
        return ""
    if len(name.split()) > 3 or _NOT_A_PLACE.search(fold(name)):
        return None
    return name


_UNI_RE = re.compile(r"univer|universitas")
_SUBUNIT_RE = re.compile(r"\b(department|dept|faculty|school of|clinic|clinics|division|laborator|bolum|fakulte|"
                         r"anabilim|yuksekokul)")
_ORG_RE = re.compile(r"institut|enstitu|hospital|hastane|ministry|bakanl|research cent|college|academy|akademi")


def _organisation(parts: list[str]) -> str:
    """Adres parçalarından kurumu seçer. Türkçe adresler kurumla başlar ("X Üniversitesi, Fakülte, ...");
    yabancı adresler çoğu kez birimle ("Department of ..., X University, Şehir"). İlk parça bir
    üniversite değilse, alt birim olmayan bir üniversite parçası (yoksa herhangi bir üniversite parçası,
    o da yoksa bir kurum parçası) seçilir; hiçbiri yoksa ilk parça kalır."""
    folded = [fold(p) for p in parts]
    if _UNI_RE.search(folded[0]):
        return parts[0]
    unis = [i for i, f in enumerate(folded) if _UNI_RE.search(f)]
    if unis:
        whole = [i for i in unis if not _SUBUNIT_RE.search(folded[i])]
        return parts[(whole or unis)[0]]
    if _SUBUNIT_RE.search(folded[0]):
        orgs = [i for i, f in enumerate(folded[1:], 1) if _ORG_RE.search(f) and not _SUBUNIT_RE.search(f)]
        if orgs:
            return parts[orgs[0]]
    return parts[0]


def _split_affiliation(aff: str) -> tuple[str | None, str | None, str | None]:
    """'Kurum, Fakülte, Bölüm, Şehir, Ülke' biçimli adresten (kurum, şehir, ülke) tahmini.

    Adresler serbest metindir; yanlış değer üretmektense boş bırakılır. Ülke, yalnızca son parça
    bilinen bir ülke adıysa alınır; şehir, ülkeden önceki parça yer adına benziyorsa alınır
    ("... Radyoloji Anabilim Dalı" ya da posta kodu şehir sayılmaz).
    """
    from .wos import country_name  # döngüsel içe aktarmayı önle

    parts = [p.strip() for p in aff.split(",") if p.strip()]
    if not parts:
        return None, None, None
    country = parts[-1].rstrip(" .") if len(parts) >= 2 and country_name(parts[-1]) else None
    city = None
    if country and len(parts) >= 3:
        for part in parts[-2:-4:-1][: len(parts) - 2]:  # kurum (ilk parça) şehir olamaz
            name = _place_name(part)
            if name != "":
                city = name
                break
    return _organisation(parts), city, country


def parse_author(raw: Any, position: int) -> Author | None:
    if isinstance(raw, str):
        name = raw.strip()
        return Author(name=name, position=position) if name else None
    if not isinstance(raw, dict):
        return None
    name = as_text(first(raw, AUTHOR_NAME_KEYS))
    if not name:
        given = as_text(first(raw, ("firstName", "givenName")))
        family = as_text(first(raw, ("lastName", "surname", "familyName")))
        name = " ".join(p for p in (given, family) if p) or None
    if not name:
        return None

    affiliations = list(dict.fromkeys(as_list(first(raw, AUTHOR_AFFILIATION_KEYS))))
    affiliation = "; ".join(affiliations) or None
    inst_obj = raw.get("institution")

    if isinstance(inst_obj, dict):
        # Gerçek TR Dizin biçimi: her alan paralel listeler.
        roots = _aligned(raw, ("institution.rootTitle", "institution.title"))
        n = len(roots)
        codes = _aligned(raw, AUTHOR_INST_CODE_KEYS, n)
        cities = _aligned(raw, ("institution.city",), n)
        countries = ["" if _is_unmatched(c, UNMATCHED_COUNTRIES) else c
                     for c in _aligned(raw, ("institution.country",), n)]
        full_titles = _aligned(raw, ("institution.fullTitle",), n)
        types = _aligned(raw, AUTHOR_INST_TYPE_KEYS, n)
        statuses = _aligned(raw, AUTHOR_INST_STATUS_KEYS, n)
        matched_idx = [i for i, t in enumerate(roots)
                       if not _is_unmatched(t, UNMATCHED_INSTITUTIONS)
                       and not (codes[i] and _is_unmatched(codes[i], UNMATCHED_INSTITUTIONS))]
        matched = bool(matched_idx)
        if matched:
            # Şehir ve ülke kurum listesiyle aynı sırada tutulur (kurum başına bir değer); aynı kurumun
            # tekrarında ilk dolu değer alınır. Eşleşmemiş kurumların yerleri sona eklenir.
            by_inst: dict[str, list[str]] = {}
            for i in matched_idx:
                slot = by_inst.setdefault(roots[i], ["", ""])
                slot[0] = slot[0] or cities[i]
                slot[1] = slot[1] or countries[i]
            inst_cities = [c for c, _ in by_inst.values()]
            inst_countries = [c for _, c in by_inst.values()]
            rest = [i for i in range(n) if i not in matched_idx]
            other_countries = [c for c in dict.fromkeys(countries[i] for i in rest) if c]
            if any(inst_countries) or other_countries:
                inst_countries = [c or DEFAULT_COUNTRY for c in inst_countries]
            inst_cities += [c for c in dict.fromkeys(cities[i] for i in rest) if c and c not in inst_cities]
            inst_countries += [c for c in other_countries if c not in inst_countries]
            institution = "; ".join(by_inst)
            city = "; ".join(inst_cities) if any(inst_cities) else None
            country = "; ".join(inst_countries) if any(inst_countries) else None
        else:
            institution = None
            city = "; ".join(dict.fromkeys(c for c in cities if c)) or None
            country = "; ".join(dict.fromkeys(c for c in countries if c)) or None
        code = next((codes[i] for i in matched_idx if codes[i]), None)
        unit = "; ".join(dict.fromkeys(
            full_titles[i] for i in matched_idx if full_titles[i] and full_titles[i] != roots[i])) or None
        inst_type = next((types[i] for i in matched_idx if not _is_unmatched(types[i], {"diger"})), None)
        status = next((statuses[i] for i in matched_idx if statuses[i]), None)
    else:
        institution = as_text(inst_obj)
        matched = not _is_unmatched(institution, UNMATCHED_INSTITUTIONS)
        city = as_text(first(raw, ("city", "institutionCity")))
        country = as_text(first(raw, ("country", "institutionCountry")))
        code = unit = inst_type = status = None
        if _is_unmatched(country, UNMATCHED_COUNTRIES):
            country = None

    g_inst, g_city, g_country = _split_affiliation(affiliations[0]) if affiliations else (None, None, None)
    if _is_unmatched(g_inst, UNMATCHED_INSTITUTIONS):
        # Adres alanı da yer tutucu olabilir ("Girilmemiş"); o zaman kurum bilinmiyor demektir.
        g_inst = g_city = g_country = None
    if not matched:
        # TR Dizin kurumu eşleştirememiş (Tanımlanmamış Kurum / Yabancı Kurumlar): adresten tahmin et.
        institution = g_inst
        city = city or g_city
    country = country or g_country
    if not country and matched and isinstance(inst_obj, dict):
        country = DEFAULT_COUNTRY

    return Author(
        name=name,
        position=position,
        author_id=as_text(first(raw, AUTHOR_ID_KEYS)),
        orcid=as_text(first(raw, AUTHOR_ORCID_KEYS)),
        institution=institution,
        city=city,
        country=country,
        affiliation=affiliation,
        institution_code=code if matched else None,
        institution_type=inst_type,
        institution_matched=matched,
        institution_status=status,
        unit=unit if matched else None,
    )


def parse_authors(authors_raw: Any) -> list[Author]:
    """Yazarları `order` alanına göre sıralar (TR Dizin yazarları sırasız döndürebilir)."""
    if isinstance(authors_raw, str):
        authors_raw = as_list(authors_raw)
    if not isinstance(authors_raw, list):
        return []
    indexed = []
    for i, r in enumerate(authors_raw):
        order = as_int(first(r, AUTHOR_ORDER_KEYS)) if isinstance(r, dict) else None
        indexed.append((order if order is not None else 10_000 + i, i, r))
    indexed.sort(key=lambda t: (t[0], t[1]))
    out = []
    for _, _, r in indexed:
        a = parse_author(r, len(out) + 1)
        if a:
            out.append(a)
    return out


_YEAR_RE = re.compile(r"\((1[89]\d{2}|20\d{2})[a-z]?\)|\b(1[89]\d{2}|20\d{2})\b")


def parse_references(value: Any) -> list[Reference]:
    if not isinstance(value, list):
        return [Reference(text=t, position=i) for i, t in enumerate(as_list(value), 1)]
    out: list[Reference] = []
    for i, r in enumerate(value, 1):
        if isinstance(r, str):
            text, target, year, pos = r.strip(), None, None, i
        elif isinstance(r, dict):
            text = as_text(first(r, ("context", "text", "reference", "citation", "title"))) or ""
            target = as_int(first(r, ("targetPublication", "targetId", "publicationId")))
            year = as_int(first_scalar(r, ("year",)))
            pos = as_int(first(r, ("order", "position"))) or i
        else:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if year is None:
            year = guess_reference_year(text)
        out.append(Reference(text=text, position=pos, target_id=str(target) if target else None, year=year))
    out.sort(key=lambda r: r.position)
    return out


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>\]]+", re.I)
# Kaynak numarası: "[12]", "12.", "(12)", "Referans25." ("Ref 3", "Kaynak 4").
_NUMBERING_RE = re.compile(r"^\s*(\[\d+\]|\d+[.)-]|\(\d+\)|(?i:referans|ref\.?|kaynak)\s*\d+[.):-]?)\s*")
# "(2001)", "(2001a,", "(2024b – online first)"; "(2019-nCoV)" ya da "(2010-2018)" yayın yılı değildir
_APA_YEAR_RE = re.compile(
    r"\((1[89]\d{2}|20\d{2})[a-z]?(?:\s*[-–]\s*(?i:online|in press|epub|early|baskıda)[^()]{0,20})?[,)]")


# "2022;57:292", "2015;(219):1-8", dergi adına bitişik "Exerc1998; 30"
_VANCOUVER_YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})\s*[;:]\s*\(?\d")


def _plausible_year(y: int | None) -> int | None:
    """Gelecekteki yıllar (ISBN ya da rapor numarasından gelen "2084" gibi) yıl sayılmaz."""
    return y if y and y <= date.today().year + 1 else None


def clean_reference_text(text: str) -> str:
    """Kaynak metnindeki HTML kalıntıları (&nbsp;, &amp;) ve bölünmez boşluklar."""
    return re.sub(r"\s+", " ", html.unescape(text or "").replace("\xa0", " ")).strip()


def guess_reference_year(text: str) -> int | None:
    """Kaynak metninden yayın yılını tahmin eder (APA: "(2001)", Vancouver: "2022;57:292")."""
    text = _DOI_RE.sub("", clean_reference_text(text))
    for rx in (_APA_YEAR_RE, _VANCOUVER_YEAR_RE):
        for m in rx.finditer(text):
            if _plausible_year(int(m.group(1))):
                return int(m.group(1))
    for m in _YEAR_RE.finditer(text):
        y = _plausible_year(int(m.group(1) or m.group(2)))
        if y:
            return y
    return None


def extract_doi(text: str) -> str | None:
    """Kaynak metnindeki ilk DOI. Satır sonunda tireden bölünmüş DOI ("10.1038/s41430-020- 0634-3")
    birleştirilir; birleştirilemeyen, tireyle biten (yarım) DOI kullanılmaz."""
    text = text or ""
    m = _DOI_RE.search(text)
    if not m:
        # "doi: 10.1164/ ajrccm.166.1.at1102": önek ile sonek arasına boşluk girmiş.
        m2 = re.search(r"(10\.\d{4,9}/)\s+([A-Za-z0-9][^\s\"<>\]]*[.\d][^\s\"<>\]]*)", text)
        if not m2:
            return None
        doi = (m2.group(1) + m2.group(2)).rstrip(".,;)").lower()
        return None if doi.endswith("-") else doi
    doi, end = m.group(), m.end()
    # Satır sonunda "/" ya da kısa bir bölümden sonra bölünmüş DOI: "10.1016/j. ssmph.2017.11.004"
    if re.fullmatch(r"10\.\d{4,9}/[A-Za-z0-9]{0,4}\.", doi):
        cont = re.match(r"\s+([A-Za-z0-9][^\s\"<>\]]*[.\d][^\s\"<>\]]*)", text[end:])
        if cont:
            doi, end = doi + cont.group(1), end + cont.end()
    while doi.endswith("-"):
        cont = re.match(r"\s+([^\s\"<>\]]*\d[^\s\"<>\]]*)", text[end:])  # devamı rakam içermeli
        if not cont:
            break
        doi, end = doi + cont.group(1), end + cont.end()
    doi = doi.rstrip(".,;)").lower()
    return None if doi.endswith("-") else doi


_LEADING_INITIALS_RE = re.compile(
    r"^\s*((?:[A-ZÇĞİÖŞÜ]\.\s*-?\s*){1,3})([A-ZÇĞİÖŞÜ][^\s,.;:()\[\]“\"]+)(?![^\s,.;:()\[\]“\"])"
    r"(?=\s*,|\s+(?:and|ve)\b|\s*&)")


def leading_initials(text: str) -> tuple[str, str] | None:
    """Baş harfleri önde yazılmış ilk yazar ("I. B. Hickie, S. L. Naismith", "H. Liu and H. Motoda"):
    (soyadı, baş harfler)."""
    m = _LEADING_INITIALS_RE.match(text)
    if not m:
        return None
    return m.group(2), "".join(re.findall(r"[A-ZÇĞİÖŞÜ]", m.group(1)))


def _ref_parts(ref: Reference) -> tuple[str, int | None, list[str]]:
    """Kaynaktan (ilk yazar soyadı, yıl, başlığın ilk kelimeleri) çıkarır. Sezgiseldir.

    APA ("Soyad, A. (2001). Başlık...") ve Vancouver ("1. Soyad AB, ... Başlık. Dergi. 2001;...")
    biçimlerini tanır.
    """
    text = _NUMBERING_RE.sub("", clean_reference_text(ref.text))
    lead = leading_initials(text)
    if lead:
        surname = lead[0]
    else:
        m = re.match(r"\s*([^,(.]+)", text)
        surname = m.group(1).strip() if m else ""
        # "LOTKA AJ", "Ekstrand J", "Nixon II HL" gibi soyadından sonra gelen baş harfleri ve kuşak ekini at.
        tokens = surname.split()
        while len(tokens) > 1 and re.fullmatch(r"[A-ZÇĞİÖŞÜ]{1,3}|Jr|Sr", tokens[-1]):
            tokens.pop()
        surname = " ".join(tokens)

    apa = _APA_YEAR_RE.search(text)
    year = _plausible_year(ref.year) or guess_reference_year(text)
    # Parantezsiz APA benzeri: "Peters, H.P.F. ve Van Raan, A.F.J. 1993. Başlık..."
    bare = re.search(r"\b(1[89]\d{2}|20\d{2})[a-z]?\.\s", text)
    if apa:
        after = text[apa.end():]
    elif bare and not _VANCOUVER_YEAR_RE.search(text):
        after = text[bare.end():]
    else:
        # Vancouver: yazar listesinden sonraki ilk cümle başlıktır.
        dot = re.search(r"[a-zçğıöşü)A-ZÇĞİÖŞÜ]\.\s", text)
        after = text[dot.end():] if dot else text[len(surname):]
    after = re.sub(r"^[\s).,:;\"“”'‘’]+", "", after)
    words = re.findall(r"[^\W\d_]{2,}", after)[:5]
    return surname, year, words


def reference_key(ref: Reference) -> str:
    """Farklı yayınlarda aynı kaynağı eşleştirmek için anahtar (ortak atıf analizi).

    Öncelik: TR Dizin kimliği > DOI > (soyad, yıl, başlığın ilk kelimeleri).
    """
    if ref.target_id:
        return f"trdizin:{ref.target_id}"
    doi = ref.doi
    if doi:
        return f"doi:{doi}"
    surname, year, words = _ref_parts(ref)
    return f"{fold(surname)}|{year or ''}|{' '.join(fold(w) for w in words[:4])}"


def reference_label(ref: Reference) -> str:
    surname, year, words = _ref_parts(ref)
    title = " ".join(words[:5])
    return f"{surname} ({year or 't.y.'}) {title}".strip()


def _abstract_and_keywords(
    raw: dict[str, Any], kw_by_lang: dict[str, list[str]] | None = None
) -> tuple[str | None, list[str], dict[str, str]]:
    """Özet(ler), anahtar kelimeler ve dile göre başlıklar.

    `kw_by_lang` verilirse anahtar kelimeler özet diline göre de bu sözlüğe eklenir.
    """
    abstracts = first(raw, ABSTRACTS_KEYS)
    texts: list[str] = []
    kws: list[str] = []
    titles: dict[str, str] = {}
    if isinstance(abstracts, list):
        for a in abstracts:
            if isinstance(a, dict):
                t = as_text(first(a, ("abstract", "text", "content", "value")))
                if t:
                    texts.append(t)
                a_kws = as_list(first(a, KEYWORDS_KEYS))
                kws.extend(a_kws)
                if kw_by_lang is not None and a_kws:
                    lang = (as_text(a.get("language")) or "").upper()
                    kw_by_lang.setdefault(lang, []).extend(a_kws)
                title = as_text(a.get("title"))
                if title:
                    titles.setdefault((as_text(a.get("language")) or "").upper(), title)
            else:
                t = as_text(a)
                if t:
                    texts.append(t)
    elif abstracts is not None:
        t = as_text(abstracts)
        if t:
            texts.append(t)
    return ("\n\n".join(texts) or None), kws, titles


def _other_citations(raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in first(raw, OTHER_CITATIONS_KEYS) or []:
        if isinstance(item, dict) and item.get("origin"):
            c = as_int(item.get("count"))
            if c is not None:
                out[fold(str(item["origin"]))] = c
    return out


def dedupe(values: Iterable[str]) -> list[str]:
    """Büyük/küçük harf ve Türkçe karakter farkını yok sayarak tekrarları atar."""
    seen: dict[str, str] = {}
    for v in values:
        v = v.strip()
        if v and fold(v) not in seen:
            seen[fold(v)] = v
    return list(seen.values())


def has_details(raw: dict[str, Any]) -> bool:
    """Kayıt için ayrıntı (publicationById) isteğine gerek var mı?

    Arama yanıtı kaynakçayı içerir ama `citedReferences` alanı `null` gelir. Bu yüzden
    yalnızca kaynakçası olmayan ya da atıf alıp atıf yapanları eksik olan kayıtlar eksik sayılır.
    """
    if not any(k in raw for k in REFERENCES_KEYS):
        return False
    if (as_int(first(raw, CITATION_KEYS)) or 0) == 0:
        return True
    return bool(first(raw, CITING_KEYS))


def _page(v: Any) -> str | None:
    t = as_text(v)
    return None if t in (None, "0") else t


def normalize_publication(raw: dict[str, Any]) -> Publication:
    """Tek bir ham yayın kaydını `Publication` nesnesine çevirir."""
    pid = as_text(first(raw, ID_KEYS)) or ""
    kw_by_lang: dict[str, list[str]] = {}
    abstract, abstract_kws, titles = _abstract_and_keywords(raw, kw_by_lang)
    keywords = dedupe(as_list(first(raw, KEYWORDS_KEYS)) + abstract_kws)
    language = as_text(first(raw, LANGUAGE_KEYS))

    title = titles.get((language or "").upper()) or next(iter(titles.values()), None)
    title = title or as_text(first(raw, TITLE_KEYS))

    subjects_raw = first(raw, SUBJECTS_KEYS)
    subjects: list[str] = []
    if isinstance(subjects_raw, list):
        for s in subjects_raw:
            t = as_text(first(s, ("fullName", "path", "name", "title"))) if isinstance(s, dict) else as_text(s)
            if t:
                subjects.append(t)
    else:
        subjects = as_list(subjects_raw)

    cited_by = []
    for c in first(raw, CITING_KEYS) or []:
        cid = as_text(first(c, ("sourceId", "publicationId"))) if isinstance(c, dict) else as_text(c)
        if cid:
            cited_by.append(cid)
    other = _other_citations(raw)

    return Publication(
        id=pid,
        title=title,
        year=as_int(first(raw, YEAR_KEYS)),
        doi=clean_doi(first(raw, DOI_KEYS)),
        citation_count=as_int(first(raw, CITATION_KEYS)),
        language=language,
        document_type=as_text(first(raw, DOC_TYPE_KEYS)),
        publication_type=as_text(first(raw, PUB_TYPE_KEYS)),
        access_type=as_text(first(raw, ACCESS_KEYS)),
        database=as_text(first(raw, DATABASE_KEYS)),
        journal=as_text(first(raw, JOURNAL_NAME_KEYS)),
        journal_id=as_text(first(raw, JOURNAL_ID_KEYS)),
        issn=as_text(first(raw, ISSN_KEYS)),
        eissn=as_text(first(raw, EISSN_KEYS)),
        volume=as_text(first_scalar(raw, VOLUME_KEYS)),
        issue=as_text(first_scalar(raw, ISSUE_KEYS)),
        start_page=_page(first(raw, START_PAGE_KEYS)),
        end_page=_page(first(raw, END_PAGE_KEYS)),
        abstract=abstract,
        url=detail_url("publication", pid) if pid else "",
        title_en=titles.get("ENG"),
        title_tr=titles.get("TUR"),
        publish_date=(as_text(first_scalar(raw, PUBLISH_DATE_KEYS)) or "")[:10] or None,
        citations_crossref=other.get("crossref"),
        citations_openalex=other.get("openalex"),
        citations_opencitations=other.get("opencitations"),
        view_count=as_int(first(raw, VIEW_KEYS)),
        download_count=as_int(first(raw, DOWNLOAD_KEYS)),
        authors=parse_authors(first(raw, AUTHORS_KEYS)),
        keywords=keywords,
        subjects=dedupe(subjects),
        references=parse_references(first(raw, REFERENCES_KEYS)),
        cited_by=list(dict.fromkeys(cited_by)),
        keywords_by_language={k: dedupe(v) for k, v in kw_by_lang.items()},
        has_details=has_details(raw),
    )


def key_tree(obj: Any, prefix: str = "", max_list: int = 1, out: dict[str, str] | None = None) -> dict[str, str]:
    """JSON içindeki tüm yolları ve örnek değerlerini listeler (şema keşfi için)."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = "{}"
        for k, v in obj.items():
            key_tree(v, f"{prefix}.{k}" if prefix else str(k), max_list, out)
    elif isinstance(obj, list):
        if not obj:
            out[prefix] = "[]"
        for i, v in enumerate(obj[:max_list]):
            key_tree(v, f"{prefix}.{i}", max_list, out)
        if len(obj) > max_list:
            out.setdefault(f"{prefix}.*", f"(toplam {len(obj)} öğe)")
    else:
        s = repr(obj)
        out[prefix] = s if len(s) <= 80 else s[:77] + "..."
    return out
