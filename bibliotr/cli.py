"""Komut satırı arayüzü.

Örnekler:
    bibliotr search "bibliometri" --limit 10
    bibliotr harvest "bibliometri" --dataset biblio --facet database=SOCIAL --years 2015-2024
    bibliotr merge fa fa_tr fa_en --out ortusme.csv
    bibliotr openalex --dataset fa --mailto ad@kurum.edu.tr
    bibliotr stats --dataset biblio
    bibliotr export --dataset biblio --format wos --out biblio.txt
    bibliotr inspect "bibliometri"
    bibliotr thesaurus suggest --dataset biblio --field keyword
    bibliotr thesaurus suggest --dataset biblio --field keyword --accept 0,2 --reject 1
    bibliotr thesaurus export --format vosviewer --field keyword --out thesaurus_terms.txt
    bibliotr thesaurus auto --dataset biblio --rule orcid+institution --save
    bibliotr stats --dataset biblio --author-merge orcid+institution
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import analysis, export, subjects
import requests

from .client import AGGREGATION_LABELS, ENTITIES, TRDizinClient, TRDizinError, extract_records
from .normalize import key_tree, normalize_publication
from .storage import DatasetNotFound, Store

DEFAULT_DB = "data/trdizin.db"


def parse_facets(items: list[str] | None) -> list[tuple[str, str]]:
    """`--facet database=SOCIAL --facet publication_year=2021` -> [(facet-database, SOCIAL), ...]"""
    out = []
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"Facet 'ad=değer' biçiminde olmalı: {item}")
        k, v = item.split("=", 1)
        k = k.strip()
        key = k if k.startswith("facet-") else f"facet-{k}"
        if key == "facet-subject":
            out += [(key, s) for s in resolve_subject(v.strip())]
        else:
            out.append((key, v.strip()))
    return out


def resolve_subject(value: str) -> list[str]:
    """Konu değerini doğrular; grup/alan/konu adını tam konu değerlerine açar.

    ör. "Tıp" -> Tıp altındaki tüm konular, "spor bilimleri" -> "Fen > Tıp > Spor Bilimleri".
    """
    found = subjects.resolve(value)
    if found:
        if len(found) > 1:
            print(f"Konu '{value}' -> {len(found)} konu seçildi.", file=sys.stderr)
        return found
    hints = subjects.suggest(value)
    msg = f"Uyarı: '{value}' bilinen bir TR Dizin konusu değil; olduğu gibi gönderiliyor."
    if hints:
        msg += " Bunu mu demek istediniz: " + " | ".join(hints)
    print(msg, file=sys.stderr)
    return [value]


def cmd_subjects(args: argparse.Namespace) -> None:
    """Konu ağacını listeler (facet-subject için geçerli değerler)."""
    wanted = set(subjects.resolve(args.filter)) if args.filter else None
    if wanted is not None and not wanted:
        hints = subjects.suggest(args.filter)
        print(f"'{args.filter}' için konu bulunamadı." + (" Öneriler: " + " | ".join(hints) if hints else ""))
        return
    for group, values in subjects.groups().items():
        rows = [v for v in values if wanted is None or v in wanted]
        if rows:
            print(f"== {group} ({len(rows)})")
            print("\n".join(f"  {v}" for v in rows))


def parse_years(spec: str | None) -> list[int] | None:
    """'2015-2024' veya '2019,2021,2023' -> yıl listesi."""
    if not spec:
        return None
    years: list[int] = []
    try:
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                years.extend(range(min(a, b), max(a, b) + 1))
            elif part:
                years.append(int(part))
    except ValueError:
        raise SystemExit(f"Yıl '2015-2024' veya '2019,2021' biçiminde olmalı: {spec}") from None
    return years


def _client(args: argparse.Namespace) -> TRDizinClient:
    return TRDizinClient(min_interval=args.delay)


def cmd_search(args: argparse.Namespace) -> None:
    sp = _client(args).search(args.entity, args.query, args.order, args.page, args.limit, parse_facets(args.facet))
    print(f"Toplam: {sp.total}  (sayfa {sp.page}, {len(sp.records)} kayıt)")
    if args.json:
        print(json.dumps(sp.raw, ensure_ascii=False, indent=2))
        return
    for rec in sp.records:
        if args.entity == "publication":
            p = normalize_publication(rec)
            print(f"- [{p.year}] {p.title}\n    {'; '.join(p.author_names)} | {p.journal} | atıf: {p.citation_count}")
        else:
            name = rec.get("title") or rec.get("name") or rec.get("fullName")
            print(f"- {rec.get('id')}: {name}")
    if sp.aggregations and args.facets:
        print("\nFacet dağılımları:")
        for name, buckets in sp.aggregations.items():
            print(f"  {name}: " + ", ".join(f"{k} ({c})" for k, c in buckets[:10]))


def cmd_harvest(args: argparse.Namespace) -> None:
    client = _client(args)
    facets = parse_facets(args.facet)
    years = parse_years(args.years)

    def progress(sp):
        print(f"  sayfa {sp.page}: {len(sp.records)} kayıt (toplam {sp.total})", file=sys.stderr)

    journal = None
    if args.journal:
        journal = _get_journal(client, args.journal)
        indexed = journal.summary()["TR Dizin'de dizinlendiği yıllar"]
        print(f"Dergi: {journal.title} (id {journal.id}); dizinlendiği yıllar: {indexed}", file=sys.stderr)
        records = client.harvest_journal(
            journal, args.query, facets, years=years, indexed_years_only=not args.all_years,
            order=args.order, max_records=args.max, on_page=progress,
        )
    else:
        records = client.harvest(
            args.query, "publication", args.order, facets, years=years, max_records=args.max, on_page=progress
        )
    if args.details != "never":
        def detail_progress(i, total):
            if i == 1 or i % 25 == 0 or i == total:
                print(f"  ayrıntı {i}/{total}", file=sys.stderr)

        n_det = client.enrich(records, only_missing=args.details == "auto", on_progress=detail_progress)
        print(f"{n_det} yayının kaynakça/atıf ayrıntısı alındı.", file=sys.stderr)
    with Store(args.db) as store:
        params = {"facets": facets, "years": years, "order": args.order}
        if journal:
            params["journal"] = {"id": journal.id, "title": journal.title}
        ds = store.create_dataset(args.dataset, args.query or (journal.title if journal else None), params)
        n = store.add_records(ds, records)
    print(f"{n} yayın '{args.dataset}' veri setine kaydedildi ({args.db}).")


def _get_journal(client: TRDizinClient, journal_id: str):
    journal = client.journal_by_id(journal_id)
    if journal is None:
        raise SystemExit(f"Dergi bulunamadı: {journal_id}")
    return journal


def cmd_journal(args: argparse.Namespace) -> None:
    """Dergi bilgisi (journalById) ve isteğe bağlı olarak yayınlarının profili."""
    from .journals import years_frame

    client = _client(args)
    journal = _get_journal(client, args.id)
    if args.json:
        print(json.dumps(journal.to_dict(), ensure_ascii=False, indent=2))
        return
    for k, v in journal.summary().items():
        if v not in (None, ""):
            print(f"{k:32} {v}")
    print("\nKonular: " + "; ".join(journal.subjects))
    print("\nYıllara göre dizinlenme:")
    print(years_frame(journal).to_string(index=False))
    if args.profile:
        sp = client.profile("", client.journal_facets(journal))
        years = dict(sp.aggregations.get("facet-publication_year", []))
        print(f"\nTR Dizin'deki yayın sayısı (ada göre): {sp.total}")
        print("Yıllara göre yayın: " + ", ".join(f"{y}: {years[y]}" for y in sorted(years)))


def _print_wos_report(r: dict) -> None:
    print(f"Bibliometrix denetimi: {r['documents']} belge")
    if r["missing_mandatory"]:
        print(f"  Eksik zorunlu alanlar (belge sayısı): {r['missing_mandatory']}")
    print(f"  Ülkesi bulunan belge: {r['documents_with_country']}/{r['documents']}  "
          f"{dict(list(r['countries'].items())[:8])}")
    print(f"  Kurum alanı (C3) olan belge: {r['documents_with_C3']}/{r['documents']}")
    print(f"  Kaynak: {r['cited_references']} (benzersiz {r['unique_cited_references']}, "
          f"birden çok yayında geçen {r['cited_more_than_once']})")
    print(f"  Yerel atıf (LCS): {r['local_citations']} atıf, {r['documents_locally_cited']} belge")


def cmd_wos_check(args: argparse.Namespace) -> None:
    """Bir WoS düz metin dosyasının bibliometrix'te nasıl görüneceğini denetler."""
    from .wos import check_wos

    _print_wos_report(check_wos(Path(args.file).read_text(encoding="utf-8", errors="replace")))


def cmd_enrich(args: argparse.Namespace) -> None:
    """Kayıtlı bir veri setindeki yayınların eksik ayrıntılarını sonradan tamamlar."""
    client = _client(args)
    with Store(args.db) as store:
        records = store.raw_records(args.dataset)
        n = client.enrich(records, only_missing=not args.all,
                          on_progress=lambda i, t: print(f"  {i}/{t}", file=sys.stderr) if i % 25 == 0 else None)
        store.add_records(args.dataset, records)
    print(f"{n} yayın güncellendi.")


def cmd_detail(args: argparse.Namespace) -> None:
    rec = _client(args).publication_by_id(args.id)
    if args.json or rec is None:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return
    p = normalize_publication(rec)
    print(f"{p.title} ({p.year})\n{'; '.join(p.author_names)}\n{p.journal}")
    print(f"Atıf: TR Dizin {p.citation_count}, CrossRef {p.citations_crossref}, "
          f"OpenCitations {p.citations_opencitations}")
    print(f"Kaynak sayısı: {len(p.references)}, atıf yapan TR Dizin yayını: {len(p.cited_by)}")
    for a in p.authors:
        print(f"  {a.position}. {a.name} | {a.institution} | {a.city} | {a.country}")


def cmd_profile(args: argparse.Namespace) -> None:
    """Tek istekle tüm sonuç kümesinin facet dağılımları (veri çekmeden hızlı profil)."""
    facets = parse_facets(args.facet)
    sp = _client(args).profile(args.query, facets)
    print(f"Toplam yayın: {sp.total}\n")
    for name, buckets in sp.aggregations.items():
        if not buckets:
            continue
        other = sp.aggregation_other.get(name, 0)
        note = f"  (listede olmayan: {other})" if other else ""
        print(f"== {AGGREGATION_LABELS.get(name, name)} ({len(buckets)} değer){note}")
        for key, count in buckets[: args.top]:
            print(f"  {count:>7}  {key}")
        print()
    if args.out:
        meta = {"Sorgu": args.query or "-", "Facet'ler": "; ".join(f"{k}={v}" for k, v in facets) or "-",
                "Toplam yayın": str(sp.total)}
        Path(args.out).write_bytes(export.aggregations_to_excel(sp.aggregations, AGGREGATION_LABELS, meta))
        print(f"Yazıldı: {args.out}")


def cmd_merge(args: argparse.Namespace) -> None:
    """Birden çok veri setini tekilleştirerek birleştirir ve örtüşme tablosunu gösterir."""
    import pandas as pd

    with Store(args.db) as store:
        sets = {name: store.dataset_members(name) for name in args.sources}
        n = store.merge_datasets(args.target, args.sources)
    table = analysis.overlap_table(sets)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(table.to_string(index=False, na_rep=""))
    print(f"\n{n} tekil yayın '{args.target}' veri setine kaydedildi.")
    if args.out:
        out = Path(args.out)
        if out.suffix.lower() == ".xlsx":
            table.to_excel(out, index=False)
        else:
            table.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"Yazıldı: {out}")


def cmd_openalex(args: argparse.Namespace) -> None:
    """Veri setindeki yayınları OpenAlex'te DOI ve başlıkla bulur, doğrudan atıf sayılarını saklar."""
    from . import openalex

    with Store(args.db) as store:
        pubs = store.publications(args.dataset)
        known = store.openalex_map()
    todo = pubs if args.refresh else [p for p in pubs if p.id not in known]
    print(f"{len(pubs)} yayın; OpenAlex'te aranacak {len(todo)} "
          f"(DOI'li {sum(1 for p in todo if openalex.norm_doi(p.doi))}).", file=sys.stderr)

    def progress(stage, i, n):
        if stage == "title" and (i == 1 or i % 25 == 0 or i == n):
            print(f"  başlıkla arama {i}/{n}", file=sys.stderr)

    import os

    api_key = args.api_key or os.environ.get("OPENALEX_API_KEY") or None
    client = openalex.OpenAlexClient(mailto=args.mailto, api_key=api_key)
    report: dict = {}
    matches = openalex.match_publications(todo, client, by_title=not args.no_title, on_progress=progress,
                                          report=report)
    with Store(args.db) as store:
        store.save_openalex(matches)
        pubs = store.publications(args.dataset)
    found = [p for p in pubs if p.openalex_id]
    by_doi = sum(1 for p in found if p.openalex_matched_by == "doi")
    reported_zero = sum(1 for p in found if (p.citations_openalex or 0) == 0 and (p.openalex_citations or 0) > 0)
    print(f"OpenAlex'te bulunan: {len(found)}/{len(pubs)} (DOI ile {by_doi}, başlıkla {len(found) - by_doi})")
    print(f"Atıf alan: OpenAlex doğrudan {sum(1 for p in found if (p.openalex_citations or 0) > 0)}, "
          f"TR Dizin'in bildirdiği OpenAlex {sum(1 for p in found if (p.citations_openalex or 0) > 0)}")
    print(f"TR Dizin'de OpenAlex atıfı 0 görünüp gerçekte atıf almış: {reported_zero}")
    print(f"Toplam atıf: OpenAlex doğrudan {sum(p.openalex_citations or 0 for p in found)}, "
          f"TR Dizin {sum(p.citation_count or 0 for p in found)}")
    calls = report.get("calls", {})
    print(f"OpenAlex istekleri: liste {calls.get('list', 0)}, arama {calls.get('search', 0)}"
          + ("" if api_key else " (API anahtarı yok: günde yaklaşık 100 arama)"))
    if report.get("title_not_searched"):
        print(f"\n!! {report['title_not_searched']} yayın başlıkla aranamadı."
              + (f" {report['stopped']}" if report.get("stopped") else "")
              + " Bulunanlar saklandı; komutu yeniden çalıştırınca yalnızca kalanlar aranır.")


def cmd_datasets(args: argparse.Namespace) -> None:
    with Store(args.db) as store:
        for d in store.list_datasets():
            print(f"- {d['name']}: {d['n']} yayın | sorgu: {d['query']!r} | güncelleme: {d['updated_at']}")


def _publications(store: Store, args: argparse.Namespace):
    """Veri setinin yayınları; `--raw` verilmediyse kayıtlı eşanlamlılar sözlüğü uygulanmış halde."""
    from . import thesaurus

    pubs = store.publications(args.dataset)
    if getattr(args, "raw", False):
        return pubs
    thes = store.load_thesaurus()
    if len(thes):
        print(f"Eşanlamlılar sözlüğü uygulandı ({len(thes)} girdi; kapatmak için --raw).", file=sys.stderr)
    rule = getattr(args, "author_merge", "none") or "none"
    if rule != "none":
        thes, merged = thesaurus.apply_author_rule(pubs, rule, thes)
        print(f"Yazar birleştirme kuralı '{rule}': {merged} yazar kimliği birleştirildi (kaydedilmedi).",
              file=sys.stderr)
    return thesaurus.apply(pubs, thes)


def cmd_stats(args: argparse.Namespace) -> None:
    with Store(args.db) as store:
        pubs = _publications(store, args)
    for k, v in analysis.summary(pubs).items():
        print(f"{k:32} {v}")
    print("\nYıllara göre:")
    print(analysis.per_year(pubs).to_string(index=False))
    for field in ("author", "journal", "keyword", "institution"):
        print(f"\nEn sık {analysis.FIELD_LABELS[field].lower()}:")
        print(analysis.top_counts(pubs, field, args.top).to_string(index=False))


def cmd_export(args: argparse.Namespace) -> None:
    with Store(args.db) as store:
        pubs = _publications(store, args)
    out = Path(args.out)
    fmt = args.format
    if fmt == "csv":
        out.write_bytes(export.to_csv(pubs))
    elif fmt == "xlsx":
        out.write_bytes(export.to_excel(pubs))
    elif fmt == "wos":
        from .wos import WosOptions, check_wos

        opts = WosOptions(ascii=not args.wos_unicode, keywords=args.wos_keywords, id_keywords=args.wos_id,
                          title=args.wos_title, disambiguate_authors=not args.wos_same_names)
        text = export.to_wos(pubs, opts)
        out.write_text(text, encoding="utf-8")
        _print_wos_report(check_wos(text))
    elif fmt == "ris":
        out.write_text(export.to_ris(pubs), encoding="utf-8")
    elif fmt == "bib":
        out.write_text(export.to_bibtex(pubs), encoding="utf-8")
    elif fmt == "vosviewer":
        g = analysis.field_network(pubs, args.network, args.min_occurrence, 1, args.top_nodes)
        map_txt, net_txt = export.to_vosviewer(g)
        stem = out.with_suffix("")
        Path(f"{stem}_map.txt").write_text(map_txt, encoding="utf-8")
        Path(f"{stem}_network.txt").write_text(net_txt, encoding="utf-8")
        print(f"Yazıldı: {stem}_map.txt, {stem}_network.txt")
        return
    print(f"Yazıldı: {out} ({len(pubs)} yayın)")


def _numbers(text: str | None) -> list[int]:
    try:
        return [int(x) for x in (text or "").replace(" ", "").split(",") if x]
    except ValueError:
        raise SystemExit(f"Öneri numaraları '0,2,5' biçiminde olmalı: {text}") from None


def cmd_thesaurus(args: argparse.Namespace) -> None:
    from . import thesaurus as th

    with Store(args.db) as store:
        thes = store.load_thesaurus()
        action = args.action
        if action == "suggest":
            pubs = store.publications(args.dataset)
            sugs = th.suggest(pubs, args.field, thes, args.threshold, args.min_pairs)
            accept, reject = _numbers(args.accept), _numbers(args.reject)
            if args.accept_all:
                accept = list(range(len(sugs)))
            if accept or reject:
                bad = [i for i in accept + reject if not 0 <= i < len(sugs)]
                if bad:
                    raise SystemExit(f"Geçersiz öneri numarası: {', '.join(map(str, bad))} "
                                     f"({len(sugs)} öneri var; numaralar 0-{len(sugs) - 1})" if sugs
                                     else "Öneri yok; kabul edilecek ya da ayrı tutulacak bir şey bulunmuyor.")
                for i in accept:
                    th.accept(thes, sugs[i])
                for i in reject:
                    th.reject(thes, sugs[i])
                store.save_thesaurus(thes)
                print(f"{len(accept)} öneri sözlüğe eklendi, {len(reject)} öneri 'ayrı tut' olarak kaydedildi. "
                      f"Sözlükte {len(thes)} girdi var.")
                return
            if not sugs:
                print("Öneri yok.")
                return
            print(f"{len(sugs)} öneri ({th.FIELD_LABELS[args.field]}). Kabul: --accept 0,3  Ayrı tut: --reject 1\n")
            for i, sg in enumerate(sugs[: args.limit]):
                print(f"[{i}] {sg.target_label} ({sg.target_count})  <-  "
                      + " | ".join(f"{m[1]} ({m[2]})" for m in sg.members) + f"   · {sg.reason}")
            if len(sugs) > args.limit:
                print(f"... {len(sugs) - args.limit} öneri daha (--limit)")
        elif action == "auto":
            pubs = store.publications(args.dataset)
            new, merged = th.apply_author_rule(pubs, args.rule, thes)
            pairs = th.author_rule_pairs(th.apply(pubs, thes), args.rule)
            basis = {}
            for _, _, why in pairs:
                basis[why] = basis.get(why, 0) + 1
            print(f"Kural '{args.rule}': {len(pairs)} çift ({', '.join(f'{k} {v}' for k, v in basis.items()) or '-'}), "
                  f"{merged} yazar kimliği birleşir.")
            if args.save:
                store.save_thesaurus(new)
                print(f"Sözlüğe kaydedildi. Sözlükte {len(new)} girdi var.")
            else:
                print("Kaydetmek için --save; kaydetmeden analizde kullanmak için stats/export --author-merge.")
        elif action == "add":
            thes.add(args.field, args.variant, args.target, name=args.name)
            store.save_thesaurus(thes)
            print(f"Eklendi: {args.variant} -> {args.target}")
        elif action == "remove":
            ok = thes.remove(args.field, args.variant)
            store.save_thesaurus(thes)
            print("Silindi." if ok else "Sözlükte böyle bir girdi yok.")
        elif action == "list":
            df = thes.frame()
            if args.field:
                df = df[df["field"] == args.field]
            print(df.drop(columns=["field", "key"]).to_string(index=False) if not df.empty else "Sözlük boş.")
        elif action == "import":
            n = thes.merge(th.Thesaurus.from_json(Path(args.file).read_text(encoding="utf-8")))
            store.save_thesaurus(thes)
            print(f"{n} girdi eklendi. Sözlükte {len(thes)} girdi var.")
        elif action == "export":
            if args.format == "json":
                text = thes.to_json()
            elif args.format == "vosviewer":
                text = th.vosviewer_thesaurus(thes, args.field)
            else:
                text = th.bibliometrix_synonyms(thes, args.field)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"Yazıldı: {args.out}")
        elif action == "homonyms":
            df = th.homonyms(store.publications(args.dataset))
            print(df.to_string(index=False) if not df.empty else "Kısa adı çakışan farklı yazar yok.")


def cmd_inspect(args: argparse.Namespace) -> None:
    """Gerçek API yanıtının alan yapısını gösterir; alan eşlemesini doğrulamak için."""
    client = _client(args)
    raw = client.search_raw(args.entity, args.query, "relevance-DESC", 1, 10, parse_facets(args.facet))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ham yanıt kaydedildi: {out}\n")
    print("== Yanıtın üst yapısı ==")
    top = {k: v for k, v in raw.items() if k != "hits"} if isinstance(raw, dict) else raw
    for path, val in key_tree(top).items():
        if not path.startswith("aggregations."):
            print(f"  {path} = {val}")
    if isinstance(raw, dict) and isinstance(raw.get("aggregations"), dict):
        print(f"  aggregations: {', '.join(raw['aggregations'])}")
    records = extract_records(raw)
    print(f"\n== İlk kaydın alanları ({len(records)} kayıt bulundu) ==")
    if records:
        for path, val in key_tree(records[0]).items():
            print(f"  {path} = {val}")
        if args.entity == "publication":
            p = normalize_publication(records[0])
            print("\n== Normalize edilmiş hali (boş alanlar eşleşmemiş olabilir) ==")
            for k, v in p.to_dict().items():
                print(f"  {k}: {v!r}"[:200])


def cmd_lookup(args: argparse.Namespace) -> None:
    client = _client(args)
    fn = {"doi": client.by_doi, "orcid": client.by_orcid, "issn": client.by_issn}[args.kind]
    print(json.dumps(fn(args.value), ensure_ascii=False, indent=2))


def cmd_reindex(args: argparse.Namespace) -> None:
    with Store(args.db) as store:
        print(f"{store.reindex()} yayın yeniden işlendi.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bibliotr", description="TR Dizin veri toplama ve bibliyometrik analiz aracı")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite veritabanı yolu (varsayılan: {DEFAULT_DB})")
    p.add_argument("--delay", type=float, default=1.0, help="İstekler arası en az bekleme süresi, saniye")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="Tek sayfa arama yap ve sonuçları göster")
    s.add_argument("query", nargs="?", default="", help="Arama ifadesi (yalnız facet ile aramak için boş bırakın)")
    s.add_argument("--entity", choices=ENTITIES, default="publication")
    s.add_argument("--order", default="relevance-DESC")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--limit", type=int, default=20, choices=(10, 20, 50, 100))
    s.add_argument("--facet", action="append", help="ad=değer (tekrarlanabilir), ör. database=SCIENCE")
    s.add_argument("--json", action="store_true", help="Ham JSON yanıtı yazdır")
    s.add_argument("--facets", action="store_true", help="Facet dağılımlarını da göster")
    s.set_defaults(func=cmd_search)

    h = sub.add_parser("harvest", help="Bir sorgunun tüm yayınlarını çekip veri setine kaydet")
    h.add_argument("query", nargs="?", default="", help="Arama ifadesi (isteğe bağlı)")
    h.add_argument("--dataset", required=True, help="Veri seti adı")
    h.add_argument("--journal", help="Dergi kimliği (ör. 488): o derginin yayınlarını çek")
    h.add_argument("--all-years", action="store_true",
                   help="--journal ile: yalnızca dizinlendiği yılları değil, tüm yılları tara")
    h.add_argument("--order", default="publicationYear-DESC")
    h.add_argument("--facet", action="append")
    h.add_argument("--years", help="Yıl yıl bölerek çek: 2015-2024 veya 2019,2021")
    h.add_argument("--max", type=int, help="En fazla kayıt sayısı")
    h.add_argument("--details", choices=("auto", "always", "never"), default="auto",
                   help="Kaynakça ve atıf yapanlar için publicationById ile ayrıntı çek "
                        "(auto: arama yanıtında yoksa; her yayın için bir istek)")
    h.set_defaults(func=cmd_harvest)

    en = sub.add_parser("enrich", help="Kayıtlı veri setinin eksik kaynakça/atıf ayrıntılarını tamamla")
    en.add_argument("--dataset", required=True)
    en.add_argument("--all", action="store_true", help="Ayrıntısı olanları da yeniden çek")
    en.set_defaults(func=cmd_enrich)

    dt = sub.add_parser("detail", help="Tek yayının tam kaydı (publicationById)")
    dt.add_argument("id")
    dt.add_argument("--json", action="store_true")
    dt.set_defaults(func=cmd_detail)

    pr = sub.add_parser("profile", help="Veri çekmeden tüm sonuç kümesinin dağılımları (yıl, dergi, kurum...)")
    pr.add_argument("query", nargs="?", default="")
    pr.add_argument("--facet", action="append")
    pr.add_argument("--top", type=int, default=15, help="Her dağılımda gösterilecek değer sayısı")
    pr.add_argument("--out", help="Tüm dağılımları Excel'e yaz (ör. profil.xlsx)")
    pr.set_defaults(func=cmd_profile)

    wc = sub.add_parser("wos-check", help="WoS dosyasının bibliometrix uyumluluğunu denetle")
    wc.add_argument("file")
    wc.set_defaults(func=cmd_wos_check)

    jr = sub.add_parser("journal", help="Dergi bilgisi (journalById): dizinlendiği yıllar, konular, editör")
    jr.add_argument("id", help="Dergi kimliği, ör. 488")
    jr.add_argument("--profile", action="store_true", help="Derginin yayınlarının yıllara göre sayısını da getir")
    jr.add_argument("--json", action="store_true")
    jr.set_defaults(func=cmd_journal)

    sj = sub.add_parser("subjects", help="TR Dizin konu listesini göster (facet-subject değerleri)")
    sj.add_argument("filter", nargs="?", help="Alan, grup veya konu adı, ör. 'Tıp' ya da 'Sosyal'")
    sj.set_defaults(func=cmd_subjects)

    d = sub.add_parser("datasets", help="Kayıtlı veri setlerini listele")
    d.set_defaults(func=cmd_datasets)

    m = sub.add_parser("merge", help="Veri setlerini tekilleştirerek birleştir, örtüşme tablosunu göster")
    m.add_argument("target", help="Oluşturulacak / güncellenecek veri seti")
    m.add_argument("sources", nargs="+", help="Birleştirilecek veri setleri")
    m.add_argument("--out", help="Örtüşme tablosunu CSV veya .xlsx olarak yaz")
    m.set_defaults(func=cmd_merge)

    oa = sub.add_parser("openalex", help="Yayınları OpenAlex'te DOI ve başlıkla bul, doğrudan atıf sayılarını sakla")
    oa.add_argument("--dataset", required=True)
    oa.add_argument("--mailto", help="OpenAlex için iletişim e-postası")
    oa.add_argument("--api-key", help="OpenAlex API anahtarı (varsayılan: OPENALEX_API_KEY ortam değişkeni); "
                                      "anahtarsız günlük kota yaklaşık 100 başlık araması")
    oa.add_argument("--no-title", action="store_true", help="Yalnızca DOI ile eşleştir")
    oa.add_argument("--refresh", action="store_true", help="Daha önce bulunanları da yeniden sorgula (güncel atıf)")
    oa.set_defaults(func=cmd_openalex)

    st = sub.add_parser("stats", help="Veri setinin özet istatistikleri")
    st.add_argument("--dataset")
    st.add_argument("--top", type=int, default=10)
    st.add_argument("--raw", action="store_true", help="Eşanlamlılar sözlüğünü uygulama")
    st.add_argument("--author-merge", choices=("none", "orcid", "orcid+institution"), default="none",
                    help="Yazar kimliklerini kurala göre birleştir (kaydetmeden): orcid | orcid+institution")
    st.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="Veri setini dışa aktar")
    e.add_argument("--dataset")
    e.add_argument("--format", choices=("csv", "xlsx", "wos", "ris", "bib", "vosviewer"), required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--network", choices=("author", "keyword", "institution", "city", "country", "subject",
                                          "reference"),
                   default="keyword", help="vosviewer için ağ türü")
    e.add_argument("--min-occurrence", type=int, default=2)
    e.add_argument("--wos-keywords", choices=("all", "tr", "en"), default="all",
                   help="wos: DE (yazar anahtar kelimeleri) alanına yazılacak dil")
    e.add_argument("--wos-id", choices=("en", "tr", "all", "none"), default="en",
                   help="wos: ID (Keywords Plus) alanı; TR Dizin'de yok, varsayılan İngilizce anahtar kelimeler")
    e.add_argument("--wos-title", choices=("original", "en"), default="original",
                   help="wos: TI alanı yayın dilinde mi, İngilizce başlık mı")
    e.add_argument("--wos-unicode", action="store_true",
                   help="wos: Türkçe karakterleri koru (varsayılan ASCII; Windows'ta R ile daha güvenli)")
    e.add_argument("--wos-same-names", action="store_true",
                   help="wos: kısa adı aynı farklı yazarları ayırt etme (varsayılan: tam ad / sıra no ile ayırt et)")
    e.add_argument("--top-nodes", type=int, default=200)
    e.add_argument("--raw", action="store_true", help="Eşanlamlılar sözlüğünü uygulama")
    e.add_argument("--author-merge", choices=("none", "orcid", "orcid+institution"), default="none",
                   help="Yazar kimliklerini kurala göre birleştir (kaydetmeden): orcid | orcid+institution")
    e.set_defaults(func=cmd_export)

    tz = sub.add_parser("thesaurus", help="Eşanlamlılar sözlüğü: yazar, kurum, anahtar kelime birleştirme")
    tzs = tz.add_subparsers(dest="action", required=True)
    fields = ("keyword", "institution", "author")
    t_s = tzs.add_parser("suggest", help="Birleştirme önerileri (--accept / --reject ile karar ver)")
    t_s.add_argument("--dataset")
    t_s.add_argument("--field", choices=fields, default="keyword")
    t_s.add_argument("--threshold", type=float, help="Benzer yazılış eşiği (0-1)")
    t_s.add_argument("--min-pairs", type=int, default=2,
                     help="Türkçe-İngilizce karşılık için en az kaç yayında birlikte geçmeli (0: kapalı)")
    t_s.add_argument("--limit", type=int, default=50)
    t_s.add_argument("--accept", help="Sözlüğe eklenecek öneri numaraları, ör. 0,2,5")
    t_s.add_argument("--reject", help="'Ayrı tut' denecek öneri numaraları")
    t_s.add_argument("--accept-all", action="store_true", help="Tüm önerileri kabul et (önce listeyi inceleyin)")
    t_au = tzs.add_parser("auto", help="Kurala göre yazar kimliklerini birleştir (aynı ORCID; aynı ad + ortak kurum)")
    t_au.add_argument("--dataset")
    t_au.add_argument("--rule", choices=("orcid", "orcid+institution"), default="orcid+institution")
    t_au.add_argument("--save", action="store_true", help="Birleştirmeleri sözlüğe kaydet")
    t_a = tzs.add_parser("add", help="Elle girdi ekle (yazarda anahtar: id:123, orcid:..., name:...)")
    t_a.add_argument("field", choices=fields)
    t_a.add_argument("variant")
    t_a.add_argument("target")
    t_a.add_argument("--name", help="Yazar için görünen ad")
    t_r = tzs.add_parser("remove", help="Girdiyi sil")
    t_r.add_argument("field", choices=fields)
    t_r.add_argument("variant")
    t_l = tzs.add_parser("list", help="Sözlüğü göster")
    t_l.add_argument("--field", choices=fields)
    t_i = tzs.add_parser("import", help="JSON sözlük dosyasındaki girdileri ekle")
    t_i.add_argument("file")
    t_e = tzs.add_parser("export", help="Sözlüğü dosyaya yaz")
    t_e.add_argument("--format", choices=("json", "vosviewer", "bibliometrix"), default="json")
    t_e.add_argument("--field", choices=fields, default="keyword", help="vosviewer / bibliometrix için alan")
    t_e.add_argument("--out", required=True)
    t_h = tzs.add_parser("homonyms", help="Kısa adı aynı olan farklı yazarları listele")
    t_h.add_argument("--dataset")
    tz.set_defaults(func=cmd_thesaurus)

    i = sub.add_parser("inspect", help="Gerçek API yanıtının alan yapısını incele")
    i.add_argument("query")
    i.add_argument("--entity", choices=ENTITIES, default="publication")
    i.add_argument("--facet", action="append")
    i.add_argument("--out", default="data/sample_response.json")
    i.set_defaults(func=cmd_inspect)

    lk = sub.add_parser("lookup", help="DOI, ORCID veya ISSN ile doğrudan kayıt getir")
    lk.add_argument("kind", choices=("doi", "orcid", "issn"))
    lk.add_argument("value")
    lk.set_defaults(func=cmd_lookup)

    r = sub.add_parser("reindex", help="Saklanan ham JSON'dan normalize alanları yeniden üret")
    r.set_defaults(func=cmd_reindex)
    return p


def _safe_streams() -> None:
    """Windows'ta çıktı dosyaya yönlendirilince akış yerel kodlamayla (ör. cp1254) yazılır; o kodlamada
    olmayan bir karakter (β, ₂ ...) içeren başlık komutu çökertmesin, '?' olarak yazılsın."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc not in ("utf8", "utf8sig") and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> None:
    _safe_streams()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    try:
        args.func(args)
    except (TRDizinError, DatasetNotFound, requests.RequestException, OSError) as e:
        if args.verbose:
            raise
        raise SystemExit(f"Hata: {e}") from None


if __name__ == "__main__":
    main()
