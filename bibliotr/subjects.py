"""TR Dizin konu (subject) ağacı.

TR Dizin arama sayfasındaki `subjectOptions` yapısının aynısıdır: her düğüm
`{"name", "value", "children"}` içerir. Yaprak düğümlerin `value` değeri doğrudan
`facet-subject` parametresine yazılır (ör. "Fen > Tıp > Spor Bilimleri").
Aynı parametre birden çok kez gönderilerek birden fazla konu (VEYA) seçilebilir.
"""

from __future__ import annotations

from typing import Any, Iterable

from .normalize import fold

# ---- ham liste (alan -> grup -> konular) -----------------------------------

_FEN_GROUPS: dict[str, list[str]] = {
    "Diş Hekimliği": ["Diş Hekimliği"],
    "Eczacılık": ["Farmakoloji ve Eczacılık", "Toksikoloji"],
    "Mühendislik": [
        "Bilgisayar Bilimleri, Bilgi Sistemleri",
        "Bilgisayar Bilimleri, Donanım ve Mimari",
        "Bilgisayar Bilimleri, Sibernitik",
        "Bilgisayar Bilimleri, Teori ve Metotlar",
        "Bilgisayar Bilimleri, Yapay Zeka",
        "Bilgisayar Bilimleri, Yazılım Mühendisliği",
        "Çevre Mühendisliği",
        "Denizcilik",
        "Endüstri Mühendisliği",
        "Enerji ve Yakıtlar",
        "Gıda Bilimi ve Teknolojisi",
        "Görüntüleme Bilimi ve Fotoğraf Teknolojisi",
        "Hücre ve Doku Mühendisliği",
        "İmalat Mühendisliği",
        "İnşaat Mühendisliği",
        "İnşaat ve Yapı Teknolojisi",
        "Jeokimya ve Jeofizik",
        "Jeoloji",
        "Maden İşletme ve Cevher Hazırlama",
        "Malzeme Bilimleri, Biyomalzemeler",
        "Malzeme Bilimleri, Kâğıt ve Ahşap",
        "Malzeme Bilimleri, Kaplamalar ve Filmler",
        "Malzeme Bilimleri, Kompozitler",
        "Malzeme Bilimleri, Özellik ve Test",
        "Malzeme Bilimleri, Seramik",
        "Malzeme Bilimleri, Tekstil",
        "Metalürji Mühendisliği",
        "Meteoroloji ve Atmosferik Bilimler",
        "Mimarlık",
        "Mühendislik, Biyotıp",
        "Mühendislik, Deniz",
        "Mühendislik, Elektrik ve Elektronik",
        "Mühendislik, Hava ve Uzay",
        "Mühendislik, Jeoloji",
        "Mühendislik, Kimya",
        "Mühendislik, Makine",
        "Mühendislik, Petrol",
        "Nanobilim ve Nanoteknoloji",
        "Nükleer Bilim ve Teknolojisi",
        "Orman Mühendisliği",
        "Polimer Bilimi",
        "Robotik",
        "Savunma Bilimleri",
        "Taşınım Bilimi ve Teknolojisi",
        "Telekomünikasyon",
        "Yeşil, Sürdürülebilir Bilim ve Teknoloji",
    ],
    "Temel Bilimler": [
        "Akustik",
        "Astronomi ve Astrofizik",
        "Balıkçılık",
        "Biyoloji",
        "Biyoloji Çeşitliliğinin Korunması",
        "Çevre Bilimleri",
        "Deniz ve Tatlı Su Biyolojisi",
        "Ekoloji",
        "Entomoloji",
        "Fizik, Akışkanlar ve Plazma",
        "Fizik, Atomik ve Moleküler Kimya",
        "Fizik, Katı Hal",
        "Fizik, Matematik",
        "Fizik, Nükleer",
        "Fizikokimya",
        "Fizik, Partiküller ve Alanlar",
        "Fizik, Uygulamalı",
        "Genetik ve Kalıtım",
        "İstatistik ve Olasılık",
        "Kimya, Analitik",
        "Kimya, İnorganik ve Nükleer",
        "Kimya, Organik",
        "Kimya, Tıbbi",
        "Kimya, Uygulamalı",
        "Kuş Bilimi",
        "Limnoloji",
        "Mantar Bilimi",
        "Matematik",
        "Mikroskopi",
        "Mineraloji",
        "Optik",
        "Oşinografi",
        "Paleontoloji",
        "Parazitoloji",
        "Spektroskopi",
        "Su Kaynakları",
        "Taşınım",
        "Termodinamik",
        "Viroloji",
        "Zooloji",
    ],
    "Tıp": [
        "Acil Tıp",
        "Adli Tıp",
        "Alerji",
        "Anatomi ve Morfoloji",
        "Androloji",
        "Anestezi",
        "Biyofizik",
        "Biyokimya ve Moleküler Biyoloji",
        "Biyoteknoloji ve Uygulamalı Mikrobiyoloji",
        "Cerrahi",
        "Dermatoloji",
        "Endokrinoloji ve Metabolizma",
        "Enfeksiyon Hastalıkları",
        "Fizyoloji",
        "Gastroenteroloji ve Hepatoloji",
        "Genel ve Dahili Tıp",
        "Geriatri ve Gerontoloji",
        "Göz Hastalıkları",
        "Halk ve Çevre Sağlığı",
        "Hematoloji",
        "Hücre Biyolojisi",
        "İmmünoloji",
        "Kadın Hastalıkları ve Doğum",
        "Kalp ve Kalp Damar Sistemi",
        "Klinik Nöroloji",
        "Kulak, Burun, Boğaz",
        "Mikrobiyoloji",
        "Nörolojik Bilimler",
        "Odyoloji ve Konuşma-Dil Patolojisi",
        "Onkoloji",
        "Ortopedi",
        "Patoloji",
        "Pediatri",
        "Periferik Damar Hastalıkları",
        "Psikiyatri",
        "Radyoloji, Nükleer Tıp, Tıbbi Görüntüleme",
        "Rehabilitasyon",
        "Romatoloji",
        "Sağlık Bilimleri ve Hizmetleri",
        "Solunum Sistemi",
        "Spor Bilimleri",
        "Tamamlayıcı ve Entegre Tıp",
        "Temel Sağlık Hizmetleri",
        "Tıbbi Araştırmalar Deneysel",
        "Tıbbi Etik",
        "Tıbbi İnformatik",
        "Tıbbi Laboratuar Teknolojisi",
        "Transplantasyon",
        "Tropik Tıp",
        "Üroloji ve Nefroloji",
        "Yoğun Bakım, Tıp",
    ],
    "Veterinerlik": ["Veterinerlik"],
    "Ziraat": [
        "Bahçe Bitkileri",
        "Bitki Bilimleri",
        "Tarımsal Ekonomi ve Politika",
        "Ziraat Mühendisliği",
        "Ziraat, Toprak Bilimi",
    ],
}

# TR Dizin'de grup düğümlerinin `value` değerleri tam yol değildir (ve biri "Muhendislik"
# diye yazılmıştır). Yalnızca yaprak değerleri aramada kullanılır.
_FEN_GROUP_VALUES = {"Mühendislik": "Muhendislik"}

_SOSYAL_TOPICS: list[str] = [
    "Aile Çalışmaları",
    "Antropoloji",
    "Arkeoloji",
    "Asya Çalışmaları",
    "Beslenme ve Diyetetik",
    "Beşeri Bilimler",
    "Bilgi, Belge Yönetimi",
    "Bilim Felsefesi ve Tarihi",
    "Coğrafya",
    "Çevre Çalışmaları",
    "Davranış Bilimleri",
    "Dil ve Dil Bilim",
    "Din Bilimi",
    "Edebi Teori ve Eleştiri",
    "Edebiyat",
    "Eğitim, Eğitim Araştırmaları",
    "Eğitim, Özel",
    "Ergonomi",
    "Etik",
    "Etnik Çalışmalar",
    "Felsefe",
    "Film, Radyo, Televizyon",
    "Folklor",
    "Halkla İlişkiler",
    "Hemşirelik",
    "Hukuk",
    "İktisat",
    "İletişim",
    "İş",
    "İşletme",
    "İşletme Finans",
    "Kadın Araştırmaları",
    "Kamu Yönetimi",
    "Kentsel Çalışmalar",
    "Kriminoloji ve Ceza Bilimi",
    "Kültürel Çalışmalar",
    "Madde Bağımlılığı",
    "Mantık",
    "Müzik",
    "Nüfus İstatistikleri Bilimi",
    "Ortaçağ ve Rönesans Çalışmaları",
    "Otelcilik, Konaklama, Spor ve Turizm",
    "Psikoloji",
    "Sağlık Politikaları ve Hizmetleri",
    "Sanat",
    "Siyasi Bilimler",
    "Sosyal Çalışma",
    "Sosyoloji",
    "Tarih",
    "Tiyatro",
    "Uluslararası İlişkiler",
]


def _leaf(name: str, value: str) -> dict[str, Any]:
    return {"name": name, "value": value, "children": []}


def _build_tree() -> list[dict[str, Any]]:
    fen_children = [_leaf("Multidisipliner – Fen", "Fen > Multidisipliner – Fen")]
    for group, topics in _FEN_GROUPS.items():
        fen_children.append({
            "name": group,
            "value": _FEN_GROUP_VALUES.get(group, group),
            "children": [_leaf(t, f"Fen > {group} > {t}") for t in topics],
        })
    sosyal_children = [_leaf("Multidisipliner – Sosyal", "Sosyal > Multidisipliner – Sosyal")]
    sosyal_children += [_leaf(t, f"Sosyal > Sosyal > {t}") for t in _SOSYAL_TOPICS]
    return [
        {"name": "Fen", "value": "Fen", "children": fen_children},
        {"name": "Sosyal", "value": "Sosyal", "children": sosyal_children},
    ]


# TR Dizin'deki `subjectOptions` ile aynı yapı
SUBJECT_TREE: list[dict[str, Any]] = _build_tree()


def iter_leaves(nodes: Iterable[dict[str, Any]] = SUBJECT_TREE, path: tuple[str, ...] = ()):
    """(yol, yaprak düğüm) ikililerini döndürür; yol ör. ("Fen", "Tıp")."""
    for node in nodes:
        if node["children"]:
            yield from iter_leaves(node["children"], path + (node["name"],))
        else:
            yield path, node


def all_subjects() -> list[str]:
    """Aramada kullanılabilecek tüm konu değerleri."""
    return [leaf["value"] for _, leaf in iter_leaves()]


def groups() -> dict[str, list[str]]:
    """Grup etiketi -> o gruptaki konu değerleri. Ör. "Fen > Tıp" -> [...].

    Alt grubu olmayan konular (Multidisipliner, Sosyal konuları) alanın kendi grubunda toplanır.
    """
    out: dict[str, list[str]] = {}
    for path, leaf in iter_leaves():
        out.setdefault(" > ".join(path), []).append(leaf["value"])
    return out


def label(value: str) -> str:
    """Okunur etiket: "Fen > Tıp > Spor Bilimleri" -> "Fen › Tıp › Spor Bilimleri",
    "Sosyal > Sosyal > Psikoloji" -> "Sosyal › Psikoloji"."""
    parts: list[str] = []
    for p in value.split(" > "):
        if not parts or parts[-1] != p:
            parts.append(p)
    return " › ".join(parts)


def leaf_name(value: str) -> str:
    """Konu değerinin son kısmı (ör. "Spor Bilimleri")."""
    return value.split(" > ")[-1]


def resolve(query: str) -> list[str]:
    """Bir konu, grup ya da alan ifadesini konu değerleri listesine çevirir.

    - Tam konu değeri: "Fen > Tıp > Spor Bilimleri" -> [o konu]
    - Grup: "Fen > Tıp" veya "Tıp" -> Tıp altındaki tüm konular
    - Alan: "Sosyal" -> tüm sosyal konular
    - Konu adı: "spor bilimleri" -> eşleşen konu(lar)
    Büyük/küçük harf ve Türkçe karakter farkı yok sayılır. Eşleşme yoksa boş liste döner.
    """
    q = fold(query)
    if not q:
        return []
    subjects = all_subjects()
    exact = [s for s in subjects if fold(s) == q]
    if exact:
        return exact
    for label, values in groups().items():
        parts = label.split(" > ")
        if q in (fold(label), fold(parts[-1])):
            return values
    field = [s for s in subjects if fold(s.split(" > ")[0]) == q]
    if field:
        return field
    return [s for s in subjects if fold(leaf_name(s)) == q]


def is_known(value: str) -> bool:
    return any(fold(value) == fold(s) for s in all_subjects())


def suggest(value: str, n: int = 5) -> list[str]:
    """Yanlış yazılmış bir konu için en yakın geçerli değerleri önerir."""
    import difflib

    subjects = all_subjects()
    folded = {fold(s): s for s in subjects}
    matches = difflib.get_close_matches(fold(value), list(folded), n=n, cutoff=0.5)
    by_leaf = difflib.get_close_matches(fold(value), [fold(leaf_name(s)) for s in subjects], n=n, cutoff=0.6)
    leaf_hits = [s for s in subjects if fold(leaf_name(s)) in by_leaf]
    return list(dict.fromkeys([folded[m] for m in matches] + leaf_hits))[:n]
