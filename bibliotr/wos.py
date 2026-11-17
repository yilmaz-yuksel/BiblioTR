"""Bibliometrix / VOSviewer ile uyumlu Web of Science (WoS) düz metin dışa aktarımı.

Bibliometrix WoS dosyasını `isi2df()` ile okur ve sonra alanları konumsal kurallarla ayrıştırır.
Bu modül dosyayı o kurallara göre üretir (bibliometrix kaynak kodu: R/isi2df.R,
R/metaTagExtraction.R, R/histNetwork.R):

- `RP` zorunlu alanlardan biridir; yoksa uyarı verir ve sorumlu yazar ülkesi analizleri boş kalır.
- `C1`: her adres ayrı satırda "[Yazarlar] Kurum, Şehir, ÜLKE." biçiminde olmalı. Bibliometrix
  her "." işaretini adres sonu sayar ve ülkeyi adresin sonunda İngilizce adıyla ("TURKEY.") arar.
- `C3`: kurum analizleri (AU_UN) adres ayrıştırmak yerine doğrudan bu alanı kullanır.
- `CR`: her kaynak "SOYAD I, YIL, KAYNAK, Vcilt, Psayfa, DOI doi" biçiminde olmalı; ilk üç alan
  virgülle konumsal okunur. Aynı eser her yayında aynı dizgiyle yazılmalı ki ortak atıf ağı
  oluşsun. Veri setindeki bir yayına yapılan atıf, o yayının "İLK YAZAR, YIL, J9 ... DOI" etiketiyle
  birebir aynı yazılırsa yerel atıf (LCS, historiograph) hesaplanır.
- Türkçe karakterler Windows'ta R'ın büyük harfe çevirmesinde bozulabildiği için varsayılan olarak
  ASCII'ye çevrilir (WoS'un kendisi de ASCII kullanır).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

from .normalize import (
    _APA_YEAR_RE, _NUMBERING_RE, _VANCOUVER_YEAR_RE, _ref_parts, clean_reference_text, leading_initials, Author, Publication, Reference, fold,
)

# bibliometrix `countries` veri seti (data/countries.rda)
BIBLIOMETRIX_COUNTRIES: tuple[str, ...] = (
    'AFGHANISTAN', 'ALBANIA', 'ALGERIA', 'ANDORRA', 'ANGOLA', 'ANTIGUA', 'ARGENTINA', 'ARMENIA', 'AUSTRALIA',
    'AUSTRIA', 'AZERBAIJAN', 'BAHAMAS', 'BAHRAIN', 'BANGLADESH', 'BARBADOS', 'BELARUS', 'BELGIUM', 'BELIZE',
    'BENIN', 'BHUTAN', 'BOLIVIA', 'BOSNIA', 'BOTSWANA', 'BRAZIL', 'BRUNEI', 'BULGARIA', 'BURKINA FASO',
    'BURUNDI', 'CABO VERDE', 'CAMBODIA', 'CAMEROON', 'CANADA', 'CENTRAL AFRICAN REPUBLIC', 'CHAD', 'CHILE',
    'CHINA', 'COLOMBIA', 'COMOROS', 'CONGO', 'COSTA RICA', "COTE D'IVOIRE", 'CROATIA', 'CUBA', 'CYPRUS',
    'CZECH REPUBLIC', 'CZECHIA', 'DENMARK', 'DJIBOUTI', 'DOMINICA', 'DOMINICAN REPUBLIC', 'ECUADOR', 'EGYPT',
    'EL SALVADOR', 'ENGLAND', 'EQUATORIAL GUINEA', 'ERITREA', 'ESTONIA', 'ESWATINI', 'ETHIOPIA', 'FAROE',
    'FIJI', 'FINLAND', 'FRANCE', 'GABON', 'GAMBIA', 'GEORGIA', 'GERMANY', 'GHANA', 'GREECE', 'GUAM',
    'GUATEMALA', 'GUINEA', 'GUINEA-BISSAU', 'HAITI', 'HONDURAS', 'HONG KONG', 'HUNGARY', 'ICELAND', 'INDIA',
    'INDONESIA', 'IRAN', 'IRAQ', 'IRELAND', 'ISRAEL', 'ITALY', 'JAMAICA', 'JAPAN', 'JORDAN', 'KAZAKHSTAN',
    'KENYA', 'KIRIBATI', 'KOREA', 'KOSOVO', 'KUWAIT', 'KYRGYZSTAN', 'LAOS', 'LATVIA', 'LEBANON', 'LESOTHO',
    'LIBERIA', 'LIBYA', 'LIECHTENSTEIN', 'LITHUANIA', 'LUXEMBOURG', 'MACEDONIA', 'MADAGASCAR', 'MALAWI',
    'MALAYSIA', 'MALDIVES', 'MALI', 'MALTA', 'MARSHALL ISLANDS', 'MAURITANIA', 'MAURITIUS', 'MEXICO',
    'MICRONESIA', 'MOLDOVA', 'MONACO', 'MONGOLIA', 'MONTENEGRO', 'MOROCCO', 'MOZAMBIQUE', 'MYANMAR',
    'NAMIBIA', 'NAURU', 'NEPAL', 'NETHERLANDS', 'NEW ZEALAND', 'NICARAGUA', 'NIGER', 'NIGERIA',
    'NORTH KOREA', 'NORTH MACEDONIA', 'NORWAY', 'OMAN', 'PAKISTAN', 'PALAU', 'PANAMA', 'PAPUA NEW GUINEA',
    'PARAGUAY', 'PERU', 'PHILIPPINES', 'POLAND', 'PORTUGAL', 'QATAR', 'ROMANIA', 'RUSSIA', 'RWANDA',
    'SAINT KITTS AND NEVIS', 'SAINT LUCIA', 'SAMOA', 'SAN MARINO', 'SAO TOME AND PRINCIPE', 'SAUDI ARABIA',
    'SCOTLAND', 'SENEGAL', 'SERBIA', 'SEYCHELLES', 'SIERRA LEONE', 'SINGAPORE', 'SLOVAKIA', 'SLOVENIA',
    'SOLOMON ISLANDS', 'SOMALIA', 'SOUTH AFRICA', 'SOUTH SUDAN', 'SPAIN', 'SRI LANKA', 'SUDAN', 'SURINAME',
    'SWAZILAND', 'SWEDEN', 'SWITZERLAND', 'SYRIA', 'TAIWAN', 'TAJIKISTAN', 'TANZANIA', 'THAILAND', 'TOGO',
    'TONGA', 'TRINIDAD AND TOBAGO', 'TUNISIA', 'TURKEY', 'TURKIYE', 'TURKMENISTAN', 'U ARAB EMIRATES',
    'UGANDA', 'UKRAINE', 'UNITED ARAB EMIRATES', 'UNITED KINGDOM', 'UNITED STATES', 'URUGUAY', 'USA',
    'UZBEKISTAN', 'VANUATU', 'VATICANO', 'VENEZUELA', 'VIETNAM', 'WALES', 'YEMEN', 'ZAMBIA', 'ZIMBABWE',
)

NORTH_CYPRUS = "NORTH CYPRUS"

# KKTC'deki kurumlar (katlanmış ad parçaları) ve şehirler. TR Dizin bu kurumların yazarlarına ülke
# olarak sıklıkla "Türkiye", "Kıbrıs" ya da "Cyprus" yazar; ülke kurumdan belirlenir.
TRNC_INSTITUTIONS = (
    "yakin dogu universitesi", "near east university", "dogu akdeniz universitesi", "eastern mediterranean university",
    "girne amerikan", "girne american", "girne universitesi", "university of kyrenia", "lefke avrupa",
    "european university of lefke", "uluslararasi kibris", "cyprus international university", "uluslararasi final",
    "final international university", "kibris ilim", "cyprus science university", "kibris amerikan",
    "american university of cyprus", "kibris bati", "cyprus west university", "kibris saglik ve toplum",
    "kibris sosyal bilimler", "bahcesehir kibris", "ada kent", "akdeniz karpaz", "karpasia", "arkin yaratici",
    "rauf denktas", "ataturk ogretmen akademisi", "kuzey kibris", "northern cyprus", "north cyprus", "kktc",
    "lefkosa", "gazimagusa",
)
TRNC_CITIES = {"lefkosa", "gazimagusa", "magusa", "girne", "guzelyurt", "iskele", "lefke", "famagusta", "kyrenia"}

# TR Dizin'deki Türkçe ülke adları ve adreslerde sık görülen yazımlar -> bibliometrix adı
_COUNTRY_ALIASES = {
    "turkiye": "TURKEY", "turkey": "TURKEY", "tr": "TURKEY", "turkiye cumhuriyeti": "TURKEY",
    "amerika birlesik devletleri": "USA", "abd": "USA", "united states": "USA",
    "united states of america": "USA", "us": "USA",
    "birlesik krallik": "UNITED KINGDOM", "ingiltere": "UNITED KINGDOM", "uk": "UNITED KINGDOM",
    "england": "UNITED KINGDOM", "iskocya": "UNITED KINGDOM", "galler": "UNITED KINGDOM",
    # KKTC ayrı ülke sayılır (bibliometrix listesinde yok; WoS biçiminde "North Cyprus" yazılır).
    "kuzey kibris turk cumhuriyeti": NORTH_CYPRUS, "kktc": NORTH_CYPRUS, "north cyprus": NORTH_CYPRUS,
    "trnc": NORTH_CYPRUS, "northern cyprus": NORTH_CYPRUS, "kuzey kibris": NORTH_CYPRUS,
    "turkish republic of northern cyprus": NORTH_CYPRUS,
    # Yalın "Kıbrıs / Cyprus" belirsizdir: kurum ya da şehir KKTC'deyse resolve_country KKTC'ye çevirir.
    "kibris": "CYPRUS", "kibris cumhuriyeti": "CYPRUS", "guney kibris": "CYPRUS", "republic of cyprus": "CYPRUS",
    "iran": "IRAN", "kirgizistan": "KYRGYZSTAN", "misir": "EGYPT", "hindistan": "INDIA", "italya": "ITALY",
    "etiyopya": "ETHIOPIA", "cin": "CHINA", "almanya": "GERMANY", "polonya": "POLAND", "ispanya": "SPAIN",
    "brezilya": "BRAZIL", "nijerya": "NIGERIA", "suudi arabistan": "SAUDI ARABIA", "endonezya": "INDONESIA",
    "makedonya cumhuriyeti": "NORTH MACEDONIA", "kuzey makedonya": "NORTH MACEDONIA", "makedonya": "MACEDONIA",
    "avustralya": "AUSTRALIA", "cezayir": "ALGERIA", "guney kore": "KOREA", "kore": "KOREA", "south korea": "KOREA",
    "japonya": "JAPAN", "kanada": "CANADA", "litvanya": "LITHUANIA", "malezya": "MALAYSIA",
    "finlandiya": "FINLAND", "fransa": "FRANCE", "irak": "IRAQ", "portekiz": "PORTUGAL", "romanya": "ROMANIA",
    "tayland": "THAILAND", "tayvan": "TAIWAN", "yunanistan": "GREECE", "cek cumhuriyeti": "CZECH REPUBLIC",
    "cekya": "CZECH REPUBLIC", "isvec": "SWEDEN", "azerbaycan": "AZERBAIJAN", "belcika": "BELGIUM",
    "birlesik arap emirlikleri": "UNITED ARAB EMIRATES", "bae": "UNITED ARAB EMIRATES",
    "bosna-hersek": "BOSNIA", "bosna hersek": "BOSNIA", "bosnia and herzegovina": "BOSNIA",
    "gana": "GHANA", "hollanda": "NETHERLANDS", "karadag": "MONTENEGRO",
    "kosova": "KOSOVO", "rusya": "RUSSIA", "rusya federasyonu": "RUSSIA", "russian federation": "RUSSIA",
    "singapur": "SINGAPORE", "slovenya": "SLOVENIA", "sirbistan": "SERBIA", "ukrayna": "UKRAINE",
    "yeni zelanda": "NEW ZEALAND", "irlanda": "IRELAND", "israil": "ISRAEL", "kazakistan": "KAZAKHSTAN",
    "ozbekistan": "UZBEKISTAN", "turkmenistan": "TURKMENISTAN", "gurcistan": "GEORGIA",
    "bulgaristan": "BULGARIA", "macaristan": "HUNGARY", "avusturya": "AUSTRIA", "isvicre": "SWITZERLAND",
    "danimarka": "DENMARK", "norvec": "NORWAY", "katar": "QATAR", "kuveyt": "KUWAIT", "urdun": "JORDAN",
    "lubnan": "LEBANON", "suriye": "SYRIA", "tunus": "TUNISIA", "fas": "MOROCCO", "somali": "SOMALIA",
    "guney afrika": "SOUTH AFRICA", "meksika": "MEXICO", "arjantin": "ARGENTINA", "kolombiya": "COLOMBIA",
    "afganistan": "AFGHANISTAN", "banglades": "BANGLADESH", "filipinler": "PHILIPPINES",
    "arnavutluk": "ALBANIA", "estonya": "ESTONIA", "letonya": "LATVIA", "hirvatistan": "CROATIA",
    "slovakya": "SLOVAKIA", "luksemburg": "LUXEMBOURG", "tacikistan": "TAJIKISTAN", "mogolistan": "MONGOLIA",
    "tanzanya": "TANZANIA", "kamerun": "CAMEROON", "nijer": "NIGER", "cad": "CHAD", "libya": "LIBYA",
    "pakistan": "PAKISTAN", "vietnam": "VIETNAM", "sudan": "SUDAN", "kenya": "KENYA", "uganda": "UGANDA",
    "mali": "MALI", "yemen": "YEMEN", "kuba": "CUBA", "sili": "CHILE", "viet nam": "VIETNAM",
    "guney afrika cumhuriyeti": "SOUTH AFRICA", "avusturalya": "AUSTRALIA", "umman": "OMAN", "bahreyn": "BAHRAIN", "senegal": "SENEGAL",
}

_TR_ASCII = str.maketrans({
    "ı": "i", "İ": "I", "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ç": "c", "Ç": "C", "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U", "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...",
})


def ascii_fold(text: str | None) -> str | None:
    """Türkçe ve aksanlı karakterleri ASCII'ye çevirir ("Yıldız" -> "Yildiz")."""
    if text is None:
        return None
    text = unicodedata.normalize("NFKD", text.translate(_TR_ASCII))
    return "".join(c for c in text if not unicodedata.combining(c)).encode("ascii", "ignore").decode()


def is_trnc(institution: str | None = None, city: str | None = None) -> bool:
    """Kurum ya da şehir KKTC'de mi?"""
    if city and fold(city).strip(" .") in TRNC_CITIES:
        return True
    inst = fold(institution or "")
    return bool(inst) and any(k in inst for k in TRNC_INSTITUTIONS)


def resolve_country(country: str | None, institution: str | None = None, city: str | None = None) -> str | None:
    """Bir adres için ülke: kurum ya da şehir KKTC'deyse NORTH CYPRUS, değilse country_name(country)."""
    if is_trnc(institution, city):
        return NORTH_CYPRUS
    return country_name(country)


def country_name(value: str | None) -> str | None:
    """Ülke adını bibliometrix'in ülke listesindeki adına çevirir; bilinmiyorsa None.

    Tek istisna KKTC'dir: NORTH CYPRUS döner (bibliometrix bunu "CYPRUS." eşleşmesiyle Kıbrıs sayar).
    """
    if not value:
        return None
    key = fold(value).strip(" .")
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    up = (ascii_fold(value) or "").upper().strip(" .")
    return up if up in BIBLIOMETRIX_COUNTRIES else None


def _clean_field(text: str, keep_commas: bool = False) -> str:
    """Konumsal ayrıştırmayı bozan karakterleri (; . ve gerekirse ,) temizler."""
    # "T.C." gibi noktalı kısaltmaları birleştir: "T.C." -> "TC"
    text = re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", ""), text)
    text = re.sub(r"[;.\[\]]" if keep_commas else r"[;.,\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def institution_short(name: str) -> str:
    """Adres için kurum adı: "HACETTEPE ÜNİVERSİTESİ" -> "HACETTEPE UNIV" (WoS kısaltması)."""
    t = (ascii_fold(name) or "").upper()
    t = re.sub(r"\bUNIVERSITES[I]\b|\bUNIVERSITY\b|\bUNIVERSITESI\b", "UNIV", t)
    return _clean_field(t)


@dataclass
class WosOptions:
    ascii: bool = True               # Türkçe karakterleri ASCII'ye çevir
    keywords: str = "all"            # DE alanı: "all" | "tr" | "en"
    id_keywords: str = "en"          # ID (Keywords Plus) alanı: "en" | "tr" | "all" | "none"
    title: str = "original"          # TI: "original" (yayın dili) | "en" | "tr"
    # Kısa adı (Soyad, baş harfler) aynı olan farklı yazarları ayırt edilebilir yaz: bibliometrix ve
    # VOSviewer yazarları yalnızca adla tanır, yoksa "Yilmaz, A" adlı herkes tek kişi sayılır.
    disambiguate_authors: bool = True
    # yazar anahtarı -> AU yazımı (to_wos doldurur; her yazar için, kanonik addan)
    author_map: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    # yazar anahtarı -> kanonik ad (en sık yazım; AF ve C1 için)
    author_labels: dict[str, str] = field(default_factory=dict, repr=False, compare=False)


# ---- yardımcılar -------------------------------------------------------------

def _t(text: str | None, opts: WosOptions) -> str | None:
    if text is None:
        return None
    return ascii_fold(text) if opts.ascii else text


def _split_name(name: str) -> tuple[str, str]:
    from .export import split_name

    return split_name(name)


def _title_case_upper(s: str) -> str:
    from .export import _title_case

    return _title_case(s)


def author_short(name: str) -> str:
    """"Ali Veli YILDIZ" -> "Yıldız, AV" (WoS AU)."""
    last, first = _split_name(name)
    initials = "".join(p[0].upper() for p in re.split(r"[\s\-.]+", first) if p)
    last = _title_case_upper(last)
    return f"{last}, {initials}" if initials else last


def author_full(name: str) -> str:
    last, first = _split_name(name)
    last = _title_case_upper(last)
    first = " ".join(_title_case_upper(w) for w in first.split())
    return f"{last}, {first}" if first else last


def author_map(pubs: Sequence[Publication], opts: WosOptions) -> dict[str, str]:
    """Her yazar anahtarı için AU yazımı.

    Aynı kimliğin farklı kayıtlardaki yazımları ("Ayşe Demir" / "Ayşe Yıldız Demir") tek bir
    kanonik addan (en sık yazım) üretilir; böylece bibliometrix ve VOSviewer aynı kişiyi bölmez.
    `disambiguate_authors` açıksa kısa adı (Soyad, baş harfler) çakışan farklı yazarlar ayırt
    edilebilir yazılır: önce tam ad ("Yilmaz, Ahmet" / "Yilmaz, Ali"); tam ad da aynıysa en çok
    yayını olan yalın kalır, diğerlerine sıra numarası eklenir ("Yilmaz, Ali 2").
    """
    from .analysis import _author_labels, author_key

    labels = _author_labels(pubs)
    counts: dict[str, int] = {}
    for p in pubs:
        for a in p.authors:
            k = author_key(a)
            counts[k] = counts.get(k, 0) + 1
    groups: dict[str, list[str]] = {}
    for k, name in labels.items():
        groups.setdefault((_t(author_short(name), opts) or "").upper(), []).append(k)
    out: dict[str, str] = {}
    for ks in groups.values():
        if len(ks) < 2 or not opts.disambiguate_authors:
            for k in ks:
                out[k] = _t(author_short(labels[k]), opts) or labels[k]
            continue
        full: dict[str, list[str]] = {}
        for k in ks:
            # Noktasız yazılır: bibliometrix atıf etiketinde noktayı boşluğa çevirir ama belge
            # etiketinde çevirmez; "Asci, F. Hulya" ile yerel atıflar eşleşmez.
            name = re.sub(r"\s+", " ", (_t(author_full(labels[k]), opts) or labels[k]).replace(".", " ")).strip()
            full.setdefault(name, []).append(k)
        for name, same in full.items():
            same.sort(key=lambda k: (-counts[k], k))
            for i, k in enumerate(same):
                out[k] = name if i == 0 else f"{name} {i + 1}"
    return out


def _canonical_name(a: Author, opts: WosOptions) -> str:
    """Yazarın kanonik adı (kimliğin en sık yazımı); harita yoksa kayıttaki ad."""
    if opts.author_labels:
        from .analysis import author_key

        return opts.author_labels.get(author_key(a)) or a.name
    return a.name


def _au(a: Author, opts: WosOptions) -> str:
    """Yazarın AU yazımı (kanonik addan; çakışma varsa ayırt edici biçim)."""
    if opts.author_map:
        from .analysis import author_key

        mapped = opts.author_map.get(author_key(a))
        if mapped:
            return mapped
    return _t(author_short(a.name), opts) or a.name


def journal_j9(p: Publication, opts: WosOptions) -> str | None:
    """J9 (kısaltılmış kaynak adı): SR ve yerel atıf etiketinin parçası; virgül içeremez."""
    if not p.journal:
        return None
    return _clean_field((_t(p.journal, opts) or "").upper())


def _keywords(p: Publication, which: str) -> list[str]:
    if which == "none":
        return []
    if which == "all" or not p.keywords_by_language:
        return p.keywords if which == "all" else []
    lang = {"tr": "TUR", "en": "ENG"}[which]
    return p.keywords_by_language.get(lang, [])


# ---- adresler ------------------------------------------------------------------

def _author_addresses(a: Author, opts: WosOptions) -> list[tuple[str, str | None]]:
    """(adres, ülke) listesi. Birden çok kurumlu yazar için birden çok adres."""
    insts = [s.strip() for s in (a.institution or "").split(";") if s.strip()]
    # Şehir ve ülke kurumlarla aynı sıradadır; boş değerler atılmaz ki sıra kaymasın.
    cities = [s.strip() for s in a.city.split(";")] if a.city else []
    countries = [s.strip() for s in a.country.split(";")] if a.country else []
    out = []
    for i, inst in enumerate(insts or [""]):
        city = cities[i] if i < len(cities) else (cities[0] if cities else "")
        ctry = resolve_country(countries[i] if i < len(countries) else (countries[0] if countries else None),
                               inst, city)
        parts = [institution_short(inst) if inst else "", _clean_field((_t(city, opts) or "").title())]
        parts = [p for p in parts if p]
        if not parts and not ctry:
            continue
        if ctry:
            parts.append(ctry.title() if ctry != "USA" else "USA")
        out.append((", ".join(parts) + ".", ctry))
    return out


def c1_lines(p: Publication, opts: WosOptions) -> list[str]:
    """C1: her benzersiz adres bir satır; köşeli parantezde o adresteki yazarlar."""
    order: dict[str, list[str]] = {}
    for a in p.authors:
        for addr, _ in _author_addresses(a, opts):
            canon = _canonical_name(a, opts)
            name = _clean_field(_t(author_full(canon), opts) or canon, keep_commas=True)
            order.setdefault(addr, []).append(name)
    return [f"[{'; '.join(dict.fromkeys(names))}] {addr}" for addr, names in order.items()]


def rp_line(p: Publication, opts: WosOptions) -> str | None:
    """RP: sorumlu yazar bilgisi TR Dizin'de yok; ilk yazar ve ilk adresi kullanılır."""
    for a in p.authors:
        addrs = _author_addresses(a, opts)
        if addrs:
            return f"{_au(a, opts)} (corresponding author), {addrs[0][0]}"
    return None


def c3_value(p: Publication, opts: WosOptions) -> str | None:
    """C3: standart kurum adları (bibliometrix kurum analizi bunu doğrudan kullanır)."""
    insts = []
    for a in p.authors:
        for s in (a.institution or "").split(";"):
            s = s.strip()
            if s:
                insts.append(_clean_field((_t(s, opts) or "").upper(), keep_commas=True))
    insts = list(dict.fromkeys(i for i in insts if i))
    return "; ".join(insts) or None


# ---- kaynaklar (CR) ------------------------------------------------------------

_APA_SOURCE_RE = re.compile(r",\s*(\d{1,4})\s*(?:\([^)]*\))?\s*[,:]\s*(?:pp?\.?\s*)?([A-Za-z]?\d+)")
_VANC_MONTHS = (r"(?<![^\W\d_])(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
                r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|ocak|şubat|mart|nisan|mayıs|haziran|temmuz"
                r"|ağustos|eylül|ekim|kasım|aralık)(?![^\W\d_])\.?(?:\s*\d{1,2})?")
# "Dergi. 2001;12(3):45" ve ay adlı "Dergi. Nisan 2021;32(1):44"
_VANC_SOURCE_RE = re.compile(
    r"\.\s*([^.]{2,}?)\.?\s*(?i:" + _VANC_MONTHS + r"\s+)?(?:1[89]\d{2}|20\d{2})\s*;\s*(\d+)?\s*(?:\([^)]*\))?"
    r"\s*:\s*([A-Za-z]?\d+)")


_INITIALS_RE = re.compile(
    r"^\s*[,.]?\s*(?:(?:II|III|IV|Jr\.?|Sr\.?)[\s,]+)?([A-ZÇĞİÖŞÜ](?:\.?\s*-?\s*[A-ZÇĞİÖŞÜ]){0,3})(?:\.|(?=[\s,;(&]|$))")
_GIVEN_NAME_RE = re.compile(r"^\s*,\s*([A-ZÇĞİÖŞÜ])[a-zçğıöşü]+")


def _initials(text_after_surname: str) -> str:
    """Soyadından hemen sonra gelen baş harfler ("K, Anderson" / ", W.R., O'connell" / ", Brendan and").

    Yalnızca soyadına bitişik yazar bilgisi alınır: kurumsal yazarlarda ("World Health Organization. Global
    recommendations...") başlığın ilk harfi, APA'da sonraki yazarın harfi baş harf sayılmaz."""
    m = _INITIALS_RE.match(text_after_surname)
    if m:
        return re.sub(r"[^A-ZÇĞİÖŞÜ]", "", m.group(1))[:4]
    m = _GIVEN_NAME_RE.match(text_after_surname)
    return m.group(1) if m else ""


# Kaynak metinlerinde künye dışı kalıntılar: bağlantılar, erişim notları, PMID.
_REF_NOISE_RE = re.compile(
    r"(?i)https?://\S+|www\.\S+|\[(?:link|internet|online|pdf)\]|\[(?:cited|erişim|accessed)[^\]]*\]"
    r"|\((?:pmid|pmcid)[^)]*\)|(?:available|retrieved)\s+(?:from|at)\s*:?.*$|(?:erişim|geliş)\s+tarihi\s*:?.*$"
    r"|accessed\s*(?:on)?\s*:?.*$|\bsource:\s*$|\[(?:article|book|text|abstract)\s+in\s+[^\]]*\]")
# Cilt ve sayfa kalıpları (öncelik sırasıyla): 7(2), 90-113 · 84 (2).pp:191-215 · 25:311-328 · , 54, 48–53
_LOC_PATTERNS = (
    re.compile(r"(?<![\d/])(\d{1,4})\s*\(([^()]{1,15})\)\s*[,:.;]?\s*(?:(?:pp?|ss?|sayfa)\.?\s*:?\s*)?([A-Za-z]?\d+)(?!\d|\.\d)"),
    re.compile(r"(?<![\d/])(\d{1,4})\s*:\s*(?:p{1,2}\.?\s*)?([A-Za-z]?\d+)"),
    re.compile(r"[,.]\s*(?:vol\.?|volume|cilt|c\.)?\s*(\d{1,4})\s*,\s*(?:(?:pp?|ss?)\.?\s*)?([A-Za-z]?\d+)\s*[-–]\s*\d+"),
    re.compile(r"(?<![\d/])(\d{1,4})\s*\(([^()]{1,15})\)\s*\.?\s*$"),
    re.compile(r"(?<![\d/])(\d{1,4})\s*\(([^()]{1,15})\)\s*[.,;]"),
)
_PAGE_ONLY_RE = re.compile(r",\s*(?:(?:pp?|ss?)\.?\s*)?(\d+)\s*[-–]\s*\d+\s*\.?\s*$")
_BOOK_RE = re.compile(
    r"(?i)\b(?:yayın(?:ları|evi)?|yayıncılık|kitabevi|basımevi|matbaa\w*|press|publish\w*|pearson|wiley|springer"
    r"|sage|routledge|elsevier|mcgraw|kinetics|lippincott|wilkins|sons|crc|nobel|pegem|\d+(?:st|nd|rd|th)\s+ed\.?|\d+\.\s*baskı)\b")
_NOT_SOURCE_RE = re.compile(r"(?i)\b(?:tez(?:i)?|thesis|dissertation|enstitüsü|kanun|yönetmeli\w*|resmi gazete)\b")
_TITLE_NOISE = {"link", "pp", "erisim", "accessed", "internet", "cited", "available", "retrieved", "online"}


_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|oca|şub|nis|haz|tem|ağu|eyl|eki|kas|ara)\w*\.?"
_DATE_TAIL_RE = re.compile(
    r"(?i)[\s,.;:]*(?:\(?(?:1[89]\d{2}|20\d{2})[a-z]?\)?(?:\s+" + _MONTHS + r"(?:\s*\d{1,2})?)?"
    r"(?:\s*[;:]?\s*\d*\s*suppl\w*\.?)?[\s,.;:]*)+$")
_DATE_PIECE_RE = re.compile(r"(?i)^(?:(?:1[89]\d{2}|20\d{2})\b.*|" + _MONTHS + r"\s*\d{0,2})$")


def _year_tail(seg: str) -> str:
    """Kaynak adından sonra kalan tarih ve noktalama: "Dergi, 2014, " / "Dergi 2017 Dec 7;" -> "Dergi"."""
    return _DATE_TAIL_RE.sub("", seg)


def _last_piece(seg: str) -> str | None:
    seg = _year_tail(seg).strip(" ,.;:\"'“”‘’")
    pieces = [x.strip(" ,.;:\"'“”‘’") for x in re.split(r"[”\"]|\.\s|,\s", seg)]
    for piece in reversed(pieces):
        piece = _year_tail(piece).strip(" ,.;:")
        if _DATE_PIECE_RE.match(piece):
            continue
        if re.findall(r"[^\W\d_]{2,}", piece) and not re.fullmatch(r"[\d\W]+", piece):
            return piece if len(piece) <= 100 else None
    return None


def _generic_locator(body: str) -> tuple[str | None, str | None, str | None]:
    """APA ya da Vancouver kalıbına uymayan dergi kaynaklarında (kaynak, cilt, sayfa)."""
    for rx in _LOC_PATTERNS:
        for m in rx.finditer(body):
            vol = m.group(1)
            if re.fullmatch(r"1[89]\d{2}|20\d{2}", vol) and rx is not _LOC_PATTERNS[0]:
                continue
            page = (m.group(3) if rx is _LOC_PATTERNS[0]
                    else None if rx in (_LOC_PATTERNS[3], _LOC_PATTERNS[4]) else m.group(2))
            source = _last_piece(body[: m.start()])
            if source:
                return source, vol, page
    m = _PAGE_ONLY_RE.search(body)
    if m:
        source = _last_piece(body[: m.start()])
        if source:
            return source, None, m.group(1)
    return None, None, None


def _title_sentence(body: str, apa) -> str | None:
    """Kitap başlığı: APA'da yıldan sonraki ilk cümle, diğerlerinde yazar listesinden sonraki cümle."""
    after = body[apa.end():] if apa else body
    if not apa:
        dot = re.search(r"(?:[a-zçğıöşü]{2}|\b[A-ZÇĞİÖŞÜ]{1,3})\.\s+(?=[A-ZÇĞİÖŞÜ0-9“\"])", after)
        after = after[dot.end():] if dot else after
    after = re.sub(r"^[\s).,:;\"“”'‘’]+", "", after)
    after = re.sub(r"^\(?(?:1[89]\d{2}|20\d{2})[a-z]?\)?[.,:]\s*", "", after)  # "2016. Başlık"
    after = re.sub(r"(?i)\s*\((?:\d+\.?\s*(?:st|nd|rd|th)?\s*)?(?:ed\b|edition|editio|bs\b|baskı)[^)]*\)", "", after)
    m = re.match(r"(.+?)(?:\.\s|\.$|$)", after)
    title = m.group(1).strip(" ,.;:\"“”") if m else None
    if not title:
        return None
    # "Başlık, 81, Pegem Yayıncılık, Ankara" -> "Başlık"; "Başlık (5th ed.)" -> "Başlık"
    kept = []
    for piece in title.split(", "):
        if kept and (re.search(r"\d|^s{1,2}[.:]", piece) or _BOOK_RE.search(piece)):
            break
        kept.append(piece)
    title = re.sub(r"\s*\([^)]*(?:\d|ed\b|edit|baskı)[^)]*\)\s*$", "", ", ".join(kept)).strip(" ,.;:")
    return title if 3 < len(title) <= 150 else None


def reference_fields(ref: Reference) -> dict[str, str | None]:
    """Kaynak metninden (yazar, yıl, kaynak, cilt, sayfa, DOI) çıkarır. Sezgiseldir.

    Önce APA ve Vancouver kalıpları denenir; uymazsa genel cilt/sayfa kalıpları ("7(2), 90-113",
    "25:311-328", ", 54, 48–53") ve kitaplar için yayınevi işaretleri kullanılır. Tez, yasa ve
    benzeri dergi ya da kitap olmayan kaynaklarda kaynak adı boş kalır.
    """
    text = _NUMBERING_RE.sub("", clean_reference_text(ref.text))
    doi = ref.doi
    body = re.sub(r"(?i)(https?://(dx\.)?doi\.org/|doi:?\s*)?10\.\d{4,9}/\s?\S+", "", text)
    body = _REF_NOISE_RE.sub("", body).strip()
    surname, year, words = _ref_parts(ref)
    words = [w for w in words if fold(w) not in _TITLE_NOISE]
    lead = leading_initials(body)
    if lead:
        initials = lead[1]
    else:
        after = body[body.find(surname) + len(surname):] if surname and surname in body else body
        initials = _initials(after[:40])
    author = f"{surname} {initials}".strip() if surname else None

    source = volume = page = None
    apa = _APA_YEAR_RE.search(body)
    if apa:
        rest = body[apa.end():]
        # Cilt yerine yıl ("Dergi, 2015, 26 (1), 1–8"): yıl olan eşleşmeyi atla.
        # Örtüşen eşleşmeler de aranır: ", 2015, 26 (1), 1–8" içinde yıl eşleşmesi ", 26 (1), 1"i yutmasın.
        found, pos = [], 0
        while (x := _APA_SOURCE_RE.search(rest, pos)) is not None:
            found.append(x)
            pos = x.start() + 1
        m = next((x for x in found if not re.fullmatch(r"1[89]\d{2}|20\d{2}", x.group(1))), None)
        year_only = m is None and bool(found)  # "Dergi, 2018, 7198274": cilt yok, makale numarası var
        m = m or (found[0] if found else None)
        if m:
            seg = _year_tail(rest[: m.start()])
            pieces = [s.strip(" ,.\"'“”") for s in re.split(r"[”\"]\s*,?|\.\s", seg) if s.strip(" ,.\"'“”")]
            source = pieces[-1] if pieces else None
            volume, page = (None if year_only else m.group(1)), m.group(2)
            # "Dergi, 36, 9, 1567-1573": ikinci sayı sayıdır, sayfa ondan sonra gelir.
            nxt = re.match(r"\s*,\s*(?:pp?\.?\s*)?(\d+)\s*[-–]\s*\d+", rest[m.end():])
            if nxt and not re.search(r"[-–]", rest[m.start():m.end()]):
                page = nxt.group(1)
    elif _VANCOUVER_YEAR_RE.search(body):
        m = _VANC_SOURCE_RE.search(body)
        if m:
            source, volume, page = m.group(1).strip(), m.group(2), m.group(3)
    if not source and not _NOT_SOURCE_RE.search(body):
        g_source, g_volume, g_page = _generic_locator(body[apa.end():] if apa else body)
        if g_source:
            source, volume, page = g_source, g_volume, g_page
        elif _BOOK_RE.search(body):
            source = _title_sentence(body, apa)
    return {"author": author, "year": str(year) if year else None, "source": source,
            "volume": volume, "page": page, "doi": doi, "title": " ".join(words[:6]) or None}


def reference_cr(ref: Reference, opts: WosOptions) -> str:
    f = reference_fields(ref)
    src = f["source"] or f["title"]
    parts = [
        _clean_field(_t(f["author"], opts) or "") or "[ANONYMOUS]",
        f["year"],
        _clean_field((_t(src, opts) or "").upper()) if src else None,
        f"V{f['volume']}" if f["volume"] else None,
        f"P{f['page']}" if f["page"] else None,
        # Eski SICI DOI'leri ";" içerebilir (ör. "...15:3.0.co;2-o"); bibliometrix ve wosfile CR alanını
        # ";" ile böldüğü için tek kaynak ikiye ayrılır. DOI'de URL kodlaması (%3B) geçerlidir.
        f"DOI {f['doi'].replace(';', '%3B')}" if f["doi"] else None,
    ]
    return ", ".join(p for p in parts if p)


def document_label(p: Publication, opts: WosOptions) -> str | None:
    """Bir yayına kaynakçada yapılan atıfın, bibliometrix'in yerel atıf etiketiyle aynı yazımı."""
    if not p.authors or not p.year:
        return None
    first = _au(p.authors[0], opts).replace(",", " ")
    first = re.sub(r"\s+", " ", first).strip()
    parts = [first, str(p.year), journal_j9(p, opts) or "",
             f"V{p.volume}" if p.volume else None, f"P{p.start_page}" if p.start_page else None,
             f"DOI {p.doi}" if p.doi else None]
    return ", ".join(x for x in parts if x is not None)


def canonical_references(pubs: Sequence[Publication], opts: WosOptions) -> dict[str, str]:
    """Kaynak anahtarı -> tek bir CR dizgisi (aynı eser her yayında aynı yazılır).

    Kaynak veri setindeki bir yayınsa (TR Dizin kimliği ya da DOI ile), o yayının kendi
    etiketi kullanılır; böylece bibliometrix yerel atıfları bulur.
    """
    by_id = {p.id: p for p in pubs}
    by_doi = {p.doi.lower(): p for p in pubs if p.doi}
    best: dict[str, tuple[int, str]] = {}
    for p in pubs:
        for r in p.references:
            key = r.key
            # TR Dizin'in bağı metne dayanır; DOI yalnızca TR Dizin'in bağlamadığı kaynaklarda kullanılır
            target = by_id.get(r.target_id) if r.target_id else by_doi.get((r.doi or "").lower())
            if target is not None and target.id != p.id:
                label = document_label(target, opts)
                if label:
                    best[key] = (99, label)
                    continue
            cr = reference_cr(r, opts)
            score = cr.count(", ")  # daha çok alanı dolu olan yazım tercih edilir
            if key not in best or score > best[key][0]:
                best[key] = (score, cr)
    return {k: v for k, (_, v) in best.items()}


# ---- kayıt ve dosya --------------------------------------------------------------

LANGUAGE_NAMES = {
    "TUR": "Turkish", "ENG": "English", "GER": "German", "FRE": "French", "ARA": "Arabic",
    "RUS": "Russian", "ITA": "Italian", "SPA": "Spanish", "KUR": "Kurdish", "AZE": "Azerbaijani",
    "KAZ": "Kazakh", "OTH": "Other",
}


def _field(tag: str, values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    vals = [re.sub(r"\s+", " ", str(v)).strip() for v in values if v is not None and str(v).strip()]
    if not vals:
        return []
    return [f"{tag} {vals[0]}"] + [f"   {v}" for v in vals[1:]]


def record_lines(p: Publication, opts: WosOptions, cr_map: dict[str, str]) -> list[str]:
    from .export import DOC_TYPES

    t = lambda s: _t(s, opts)  # noqa: E731
    title = p.title
    if opts.title == "en" and p.title_en:
        title = p.title_en
    elif opts.title == "tr" and p.title_tr:
        title = p.title_tr
    rec = ["PT J"]
    rec += _field("AU", [_au(a, opts) for a in p.authors])
    rec += _field("AF", [t(author_full(_canonical_name(a, opts))) for a in p.authors])
    rec += _field("TI", t(title))
    rec += _field("SO", (t(p.journal) or "").upper() or None)
    rec += _field("J9", journal_j9(p, opts))
    rec += _field("JI", journal_j9(p, opts))
    rec += _field("LA", LANGUAGE_NAMES.get((p.language or "").upper(), p.language))
    rec += _field("DT", DOC_TYPES.get((p.publication_type or "").upper(), "Article"))
    de = _keywords(p, opts.keywords)
    rec += _field("DE", "; ".join(_clean_field(t(k) or "", keep_commas=True) for k in de) or None)
    ide = _keywords(p, opts.id_keywords) if opts.id_keywords != "none" else []
    rec += _field("ID", "; ".join(_clean_field(t(k) or "", keep_commas=True) for k in ide) or None)
    rec += _field("AB", t(p.abstract))
    rec += _field("C1", c1_lines(p, opts))
    rec += _field("C3", c3_value(p, opts))
    rec += _field("RP", rp_line(p, opts))
    crs = list(dict.fromkeys(cr_map.get(r.key) or reference_cr(r, opts) for r in p.references))
    rec += _field("CR", crs)
    rec += _field("NR", str(len(crs)) if crs else None)
    rec += _field("TC", str(p.citation_count or 0))
    rec += _field("Z9", str(p.citation_count or 0))
    rec += _field("SN", p.issn)
    rec += _field("EI", p.eissn)
    rec += _field("PY", str(p.year) if p.year else None)
    rec += _field("VL", p.volume)
    rec += _field("IS", t(p.issue))
    rec += _field("BP", p.start_page)
    rec += _field("EP", p.end_page)
    rec += _field("DI", p.doi)
    subjects = "; ".join(_clean_field(t(s) or "", keep_commas=True) for s in p.subjects) or None
    rec += _field("WC", subjects)
    rec += _field("SC", subjects)
    rec += _field("UT", f"TRDIZIN:{p.id}")
    rec.append("ER")
    return rec


def to_wos(pubs: Sequence[Publication], options: WosOptions | None = None) -> str:
    opts = options or WosOptions()
    if not opts.author_map:
        from .analysis import _author_labels

        opts = replace(opts, author_map=author_map(pubs, opts), author_labels=_author_labels(pubs))
    cr_map = canonical_references(pubs, opts)
    out = ["FN Clarivate Analytics Web of Science", "VR 1.0"]
    for p in pubs:
        out.append("")
        out.extend(record_lines(p, opts, cr_map))
    out += ["", "EF", ""]
    return "\n".join(out)


# ---- denetim: bibliometrix dosyayı nasıl görecek? ---------------------------------

def parse_wos(text: str) -> list[dict[str, list[str]]]:
    """WoS düz metnini isi2df gibi etiket -> satırlar sözlüklerine ayırır."""
    recs, cur, tag = [], None, None
    for line in text.splitlines():
        if len(line) <= 1 or line[:3] in ("FN ", "VR ", "EF"):
            continue
        t = line[:3].strip() if line[:3] != "   " else tag
        if t == "PT":
            cur = {}
            recs.append(cur)
        if cur is None:
            continue
        if t == "ER":
            continue
        tag = t
        cur.setdefault(t, []).append(line[3:].strip())
    return recs


def check_wos(text: str) -> dict:
    """Bibliometrix'in (isi2df + metaTagExtraction + histNetwork) göreceği sonucun Python taklidi.

    Döndürülen sözlük: eksik zorunlu alanlar, ülkesi bulunan belge oranı, kurum alanı,
    kaynak sayıları ve yerel atıf (LCS) eşleşmeleri.
    """
    recs = parse_wos(text)
    n = len(recs)
    mandatory = ["AU", "DE", "C1", "RP", "CR", "PY", "SO", "TI", "TC"]
    missing = {tag: sum(1 for r in recs if tag not in r) for tag in mandatory}
    countries_dot = [c + "." for c in BIBLIOMETRIX_COUNTRIES]

    def upper(s: str) -> str:
        return s.upper()

    docs_with_country, country_counts, docs_with_c3 = 0, {}, 0
    labels, cr_labels = {}, []
    for i, r in enumerate(recs):
        c1 = upper(" ".join(r.get("C1", [])) or " ".join(r.get("RP", [])))
        c1 = re.sub(r"\[.*?\] ", "", c1)
        found = [c.rstrip(".") for c in countries_dot if c in c1]
        if found:
            docs_with_country += 1
            for c in set(found):
                country_counts[c] = country_counts.get(c, 0) + 1
        if r.get("C3"):
            docs_with_c3 += 1
        au = r.get("AU", ["NA"])[0]
        first = re.sub(r"\s+", " ", upper(au).replace(",", " ")).strip()
        py = (r.get("PY") or [""])[0]
        j9 = upper((r.get("J9") or r.get("SO") or [""])[0])
        # bibliometrix (histNetwork): DOI'siz belgenin etiketi "… DOI " ile biter; kaynakçadaki DOI'siz
        # atıf da aynı biçimi alır, bu yüzden DOI'siz belgeler de yerel atıf alabilir.
        di = upper((r.get("DI") or [""])[0])
        labels[f"{re.sub(chr(92) + 's+', ' ', f'{first}, {py}, {j9}')} DOI {di}"] = i
        for ref in r.get("CR", []):
            ref = upper(ref)
            fields = ref.split(",")
            au_ = re.sub(r"[ ]{2,}", "", fields[0].replace(".", " ")).strip()
            py_ = fields[1].strip() if len(fields) > 1 else ""
            so_ = fields[2].strip() if len(fields) > 2 else ""
            di_ = ref.split("DOI", 1)[1].strip() if "DOI" in ref else ""
            cr_labels.append((i, f"{au_}, {py_}, {so_} DOI {di_}"))
    lcs = {}
    for citing, lab in cr_labels:
        if lab in labels and labels[lab] != citing:
            lcs[labels[lab]] = lcs.get(labels[lab], 0) + 1
    all_crs = [lab for _, lab in cr_labels]
    cr_counts = Counter(all_crs)
    return {
        "documents": n,
        "missing_mandatory": {k: v for k, v in missing.items() if v},
        "documents_with_country": docs_with_country,
        "countries": dict(sorted(country_counts.items(), key=lambda kv: -kv[1])),
        "documents_with_C3": docs_with_c3,
        "cited_references": len(all_crs),
        "unique_cited_references": len(cr_counts),
        "cited_more_than_once": sum(1 for c in cr_counts.values() if c > 1),
        "local_citations": sum(lcs.values()),
        "documents_locally_cited": len(lcs),
    }
