"""Aşama 3, adım 1: TR Dizin gelişmiş (alan) arama sözdizimi yoklaması.

TR Dizin arama API'sinin sorgu sözdizimi (alan araması, VEYA, iç parantez, ekli
biçimler, alanların birleştirilmesi) belgelenmemiştir. Bu betik veri çekmeden, her
sorgu için tek bir istekle toplam kayıt sayısını ve ilk 10 kaydın başlığını alır,
ardından fiziksel aktivite çalışmasının alan araması iddialarını yorumlar
(makalede Tablo 3 ve Bölüm 4.1).

Çalıştırma (deponun kök dizininde, TR Dizin'e erişimi olan bir bilgisayarda):

    python paper/scripts/01_probe_queries.py

`--set fa_alan` eski çağrılarla uyumluluk için kabul edilir; tek yoklama kümesi budur.

Çıktılar `paper/results/probe_fa_alan_<tarih>/` altına yazılır:
    counts.csv        her sorgu için toplam kayıt sayısı
    probe.json        her sorgunun ilk 10 kaydının başlığı (ham kanıt)
    field.txt         alan araması yoklamasının yorumu

Her sorgu için tek istek atılır (12 istek); istekler arasında 1 saniye beklenir.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bibliotr.client import TRDizinClient  # noqa: E402
from bibliotr.normalize import normalize_publication  # noqa: E402


@dataclass(frozen=True)
class Probe:
    group: str
    q: str
    purpose: str


def _field(field: str, *phrases: str, brackets: bool = False) -> str:
    inner = " OR ".join(f'("{p}")' if brackets else f'"{p}"' for p in phrases)
    return f"({field} : ( {inner} ))"


FA, PA = "fiziksel aktivite", "physical activity"
T_FA, T_PA, K_FA = _field("title", FA), _field("title", PA), _field("keyword", FA)
FIELD_PA = [
    Probe("default", FA, "Varsayılan arama: iki sözcük, tırnaksız"),
    Probe("default", "gamify", "Varsayılan arama: bulanık eşleşme örneği (family?)"),
    Probe("table3", T_FA, "Tablo 3: başlıkta TR ifade"),
    Probe("table3", T_PA, "Tablo 3: başlıkta EN ifade"),
    Probe("table3", _field("title", FA, PA), "Tablo 3: başlıkta iki ifadenin VEYA'sı"),
    Probe("table3", _field("title", FA, PA, brackets=True), "Tablo 3: aynı VEYA, iç parantezli"),
    Probe("table3", _field("title", "fiziksel", "physical"), "Tablo 3: tek sözcüklerin VEYA'sı"),
    Probe("table3", K_FA, "Tablo 3: anahtar kelimede TR ifade"),
    Probe("exact", _field("title", "fiziksel aktivitenin"), "Ekli biçim tek başına"),
    Probe("exact", _field("title", FA, "fiziksel aktivitenin"), "Temel ve ekli biçimin VEYA'sı"),
    Probe("fields", f"{T_FA} | {K_FA}", "İki alan cümlesi '|' ile"),
    Probe("fields", f"{T_FA} OR {K_FA}", "İki alan cümlesi 'OR' ile"),
]


def interpret_field(n: dict[str, int | None]) -> list[str]:
    """Alan araması yoklamasının yorumu (Bölüm 4.1'in iddiaları)."""
    out = []

    def g(q):
        return n.get(q)

    t_or, t_or_b = g(_field("title", FA, PA)), g(_field("title", FA, PA, brackets=True))
    if None not in (g(T_FA), g(T_PA), t_or):
        ok = max(g(T_FA), g(T_PA)) <= t_or <= g(T_FA) + g(T_PA)
        out.append(f"Alan içinde VEYA: {g(T_FA)} ve {g(T_PA)} -> {t_or} "
                   + ("(birleşim; VEYA çalışıyor)." if ok else "(birleşim değil)."))
    if None not in (t_or, t_or_b):
        out.append(f"İç parantez: {t_or_b} " + ("(aynı)." if t_or == t_or_b else f"(farklı: {t_or})."))
    suf, both = g(_field("title", "fiziksel aktivitenin")), g(_field("title", FA, "fiziksel aktivitenin"))
    if None not in (g(T_FA), suf, both):
        out.append(f"Ekli biçim: '{FA}' {g(T_FA)}, 'fiziksel aktivitenin' {suf}, VEYA {both}: "
                   + ("temel ifade ekli biçimi eşleştirmiyor (tam sözcük)." if both > g(T_FA)
                      else "ekli biçim temel ifadenin sonuçlarına katkı yapmıyor."))
    for label, q in (("|", f"{T_FA} | {K_FA}"), ("OR", f"{T_FA} OR {K_FA}")):
        v = g(q)
        if None not in (v, g(T_FA), g(K_FA)):
            kind = ("hiç kayıt yok: alanlar tek sorguda birleştirilemiyor" if v == 0 else
                    "kesişim" if v <= min(g(T_FA), g(K_FA)) else
                    "birleşim" if v >= max(g(T_FA), g(K_FA)) else "belirsiz")
            out.append(f"Alanlar '{label}' ile: başlık {g(T_FA)}, anahtar kelime {g(K_FA)} -> {v} ({kind}).")
    if None not in (g(FA), g(T_FA)):
        out.append(f"Varsayılan arama '{FA}': {g(FA)} (alan aramasında başlık {g(T_FA)}).")
    if g("gamify") is not None:
        out.append(f"Varsayılan arama 'gamify': {g('gamify')}.")
    return out


def run_field(client, outdir: Path) -> dict[str, int | None]:
    """Gelişmiş alan araması yoklaması: her sorgu için toplam ve ilk 10 kaydın başlığı."""
    outdir.mkdir(parents=True, exist_ok=True)
    totals: dict[str, int | None] = {}
    evidence = []
    for i, p in enumerate(FIELD_PA, 1):
        try:
            page = client.search("publication", p.q, "relevance-DESC", 1, 10)
            total = page.total
            sample = [{"id": pub.id, "year": pub.year, "title": pub.title}
                      for pub in (normalize_publication(r) for r in page.records[:10])]
            err = None
        except Exception as e:  # ağ hatası tüm yoklamayı durdurmasın
            total, sample, err = None, [], str(e)
        totals[p.q] = total
        evidence.append({"group": p.group, "q": p.q, "purpose": p.purpose, "total": total, "sample": sample,
                         "error": err})
        print(f"[{i:>2}/{len(FIELD_PA)}] {total if total is not None else 'HATA':>7}  {p.q}", file=sys.stderr)
    with open(outdir / "counts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "query", "purpose", "total"])
        for e in evidence:
            w.writerow([e["group"], e["q"], e["purpose"], e["total"]])
    (outdir / "probe.json").write_text(json.dumps(
        {"date": date.today().isoformat(), "probes": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = interpret_field(totals)
    (outdir / "field.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nÇıktılar: {outdir}")
    return totals


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=("fa_alan",), default="fa_alan",
                    help="Yoklama kümesi: fiziksel aktivite alan araması (tek seçenek; uyumluluk için)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--delay", type=float, default=1.2, help="İstekler arası bekleme, saniye (1.2 sn = dakikada 50 istek; TR Dizin limiti dakikada 60/300 istek)")
    args = ap.parse_args(argv)
    client = TRDizinClient(min_interval=args.delay)
    run_field(client, Path(args.out or ROOT / "paper" / "results" / f"probe_fa_alan_{date.today():%Y%m%d}"))


if __name__ == "__main__":
    main()
