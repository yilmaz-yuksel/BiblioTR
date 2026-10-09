from bibliotr import Store, TRDizinClient, normalize_publication
from bibliotr.export import to_wos
from bibliotr.normalize import fold
from bibliotr.query import discover_phrase_variants, phrase_queries

client = TRDizinClient()
seeds = ["fiziksel aktivite", "fiziksel etkinlik", "physical activity"]
records, queried, todo = {}, set(), seeds
while todo:                                   # stop when no new form appears
    queried |= {fold(p) for p in todo}
    for field in ("title", "keyword"):        # exact phrases, field by field
        for q in phrase_queries(field, todo):
            records |= {str(r["id"]): r for r in client.harvest(q)}
    pubs = [normalize_publication(r) for r in records.values()]
    found = discover_phrase_variants(pubs, seeds)        # suffixed forms in the records
    todo = [v["form"] for k, v in found.items() if k not in queried]

client.enrich(list(records.values()))         # reference lists and citing records
with Store("pa.db") as store:
    store.add_records(store.create_dataset("pa"), records.values())
    with open("pa_wos.txt", "w", encoding="utf-8") as f:
        f.write(to_wos(store.publications("pa")))
