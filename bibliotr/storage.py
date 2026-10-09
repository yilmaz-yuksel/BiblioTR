"""SQLite tabanlı veri seti deposu.

Her çekim bir "veri seti" (dataset) olarak kaydedilir: sorgu, facet'ler ve
bulunan yayınlar. Yayınlar kimliğe göre tekilleştirilir, ham JSON da saklanır
ki alan eşlemesi değişirse yeniden çekmeye gerek kalmasın (`reindex`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .normalize import Publication, normalize_publication

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    query       TEXT,
    params_json TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
    id               TEXT PRIMARY KEY,
    title            TEXT,
    year             INTEGER,
    doi              TEXT,
    citation_count   INTEGER,
    language         TEXT,
    document_type    TEXT,
    publication_type TEXT,
    access_type      TEXT,
    database         TEXT,
    journal          TEXT,
    journal_id       TEXT,
    issn             TEXT,
    eissn            TEXT,
    volume           TEXT,
    issue            TEXT,
    start_page       TEXT,
    end_page         TEXT,
    abstract         TEXT,
    url              TEXT,
    title_en         TEXT,
    title_tr         TEXT,
    publish_date     TEXT,
    citations_crossref      INTEGER,
    citations_openalex      INTEGER,
    citations_opencitations INTEGER,
    view_count       INTEGER,
    download_count   INTEGER,
    has_details      INTEGER,
    raw_json         TEXT NOT NULL,
    fetched_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_authors (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    name           TEXT NOT NULL,
    author_id      TEXT,
    orcid          TEXT,
    institution    TEXT,
    city           TEXT,
    country        TEXT,
    affiliation    TEXT,
    institution_code TEXT,
    institution_type TEXT,
    institution_status TEXT,
    unit           TEXT,
    PRIMARY KEY (publication_id, position)
);
CREATE TABLE IF NOT EXISTS publication_keywords (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    keyword        TEXT NOT NULL,
    PRIMARY KEY (publication_id, keyword)
);
CREATE TABLE IF NOT EXISTS publication_subjects (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    subject        TEXT NOT NULL,
    PRIMARY KEY (publication_id, subject)
);
CREATE TABLE IF NOT EXISTS publication_references (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    reference      TEXT NOT NULL,
    target_id      TEXT,
    year           INTEGER,
    ref_key        TEXT,
    PRIMARY KEY (publication_id, position)
);
CREATE TABLE IF NOT EXISTS publication_citing (
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    citing_id      TEXT NOT NULL,
    PRIMARY KEY (publication_id, citing_id)
);
CREATE TABLE IF NOT EXISTS dataset_publications (
    dataset_id     INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    publication_id TEXT NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    PRIMARY KEY (dataset_id, publication_id)
);
CREATE TABLE IF NOT EXISTS openalex_works (
    publication_id   TEXT PRIMARY KEY REFERENCES publications(id) ON DELETE CASCADE,
    openalex_id      TEXT NOT NULL,
    cited_by_count   INTEGER,
    publication_year INTEGER,
    doi              TEXT,
    type             TEXT,
    language         TEXT,
    matched_by       TEXT NOT NULL,
    title_similarity REAL,
    title            TEXT,
    fetched_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thesaurus (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    json       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_year ON publications(year);
CREATE INDEX IF NOT EXISTS idx_pub_doi ON publications(doi);
CREATE INDEX IF NOT EXISTS idx_auth_name ON publication_authors(name);
"""

# Eski veritabanlarına sonradan eklenen sütunlar (otomatik taşıma için).
ADDED_COLUMNS = {
    "publications": {
        "title_en": "TEXT", "title_tr": "TEXT", "publish_date": "TEXT", "citations_crossref": "INTEGER",
        "citations_openalex": "INTEGER", "citations_opencitations": "INTEGER",
        "view_count": "INTEGER", "download_count": "INTEGER", "has_details": "INTEGER",
    },
    "publication_authors": {"affiliation": "TEXT", "institution_code": "TEXT", "institution_type": "TEXT",
                            "institution_status": "TEXT", "unit": "TEXT"},
    "publication_references": {"target_id": "TEXT", "year": "INTEGER", "ref_key": "TEXT"},
    "openalex_works": {"title": "TEXT"},
}

PUB_COLUMNS = (
    "id", "title", "year", "doi", "citation_count", "language", "document_type",
    "publication_type", "access_type", "database", "journal", "journal_id", "issn",
    "eissn", "volume", "issue", "start_page", "end_page", "abstract", "url", "title_en",
    "title_tr", "publish_date", "citations_crossref", "citations_openalex", "citations_opencitations",
    "view_count", "download_count", "has_details",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetNotFound(KeyError):
    """İstenen adda veri seti yok."""

    def __str__(self) -> str:  # KeyError iletiyi tırnak içinde gösterir
        return str(self.args[0]) if self.args else ""


class Store:
    def __init__(self, path: str | Path = "data/trdizin.db", readonly: bool = False):
        """`readonly=True`: dosyayı değiştirmeden açar (şema oluşturma ve taşıma yapılmaz).

        Dondurulmuş (SHA-256 özeti kayıtlı) veri setlerini okumak için kullanılır; normal açış
        eksik tabloları oluşturduğu için dosyanın baytlarını değiştirir.
        """
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(path)
            self.conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True,
                                        check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Eski şemada yeni sütunları eklemeden önce tabloları oluşturma betiği çalışabilsin diye
        # önce eksik sütunları ekle, sonra şemayı (IF NOT EXISTS) uygula.
        migrated = self._migrate()
        self.conn.executescript(SCHEMA)
        if migrated:
            self.reindex()

    def _migrate(self) -> bool:
        changed = False
        for table, cols in ADDED_COLUMNS.items():
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # tablo henüz yok; SCHEMA oluşturacak
            for col, typ in cols.items():
                if col not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                    changed = True
        self.conn.commit()
        return changed

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- veri setleri --------------------------------------------------

    def create_dataset(self, name: str, query: str | None = None, params: dict | None = None) -> int:
        """Veri setini oluşturur; varsa kimliğini döndürür ve sorgu bilgisini günceller."""
        now = _now()
        params_json = json.dumps(params or {}, ensure_ascii=False)
        with self.conn:
            row = self.conn.execute("SELECT id FROM datasets WHERE name = ?", (name,)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE datasets SET query = ?, params_json = ?, updated_at = ? WHERE id = ?",
                    (query, params_json, now, row["id"]),
                )
                return int(row["id"])
            cur = self.conn.execute(
                "INSERT INTO datasets (name, query, params_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, query, params_json, now, now),
            )
            return int(cur.lastrowid)

    def list_datasets(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT d.*, COUNT(dp.publication_id) AS n
               FROM datasets d LEFT JOIN dataset_publications dp ON dp.dataset_id = d.id
               GROUP BY d.id ORDER BY d.updated_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d.pop("params_json") or "{}")
            out.append(d)
        return out

    def dataset_id(self, name: str) -> int:
        row = self.conn.execute("SELECT id FROM datasets WHERE name = ?", (name,)).fetchone()
        if not row:
            raise DatasetNotFound(f"Veri seti bulunamadı: {name}")
        return int(row["id"])

    def dataset_members(self, name: str) -> set[str]:
        """Veri setindeki yayın kimlikleri."""
        rows = self.conn.execute(
            "SELECT publication_id FROM dataset_publications WHERE dataset_id = ?", (self.dataset_id(name),)
        ).fetchall()
        return {r[0] for r in rows}

    def merge_datasets(self, target: str, sources: Iterable[str]) -> int:
        """Kaynak veri setlerinin birleşimini (tekilleştirilmiş) hedef veri setine ekler.

        Yayınlar kimliğe göre tek kez saklandığı için kaynaklar değişmeden kalır ve her
        yayının hangi kaynak veri setinden geldiği izlenebilir. Hedefteki toplam yayın
        sayısını döndürür.
        """
        sources = list(dict.fromkeys(sources))
        if target in sources:
            raise ValueError("Hedef veri seti kaynaklar arasında olamaz")
        src_ids = [self.dataset_id(s) for s in sources]  # olmayan varsa KeyError
        ds_id = self.create_dataset(target, None, {"merged_from": sources})
        marks = ", ".join("?" for _ in src_ids)
        with self.conn:
            self.conn.execute(
                f"""INSERT OR IGNORE INTO dataset_publications (dataset_id, publication_id)
                    SELECT DISTINCT ?, publication_id FROM dataset_publications
                    WHERE dataset_id IN ({marks})""",
                (ds_id, *src_ids),
            )
        return len(self.dataset_members(target))

    def delete_dataset(self, name: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM datasets WHERE name = ?", (name,))
            # Hiçbir veri setine bağlı olmayan yayınları temizle.
            self.conn.execute(
                "DELETE FROM publications WHERE id NOT IN (SELECT publication_id FROM dataset_publications)"
            )

    # ---- yayınlar ------------------------------------------------------

    def _write_publication(self, pub: Publication, raw: dict[str, Any], now: str) -> None:
        values = [getattr(pub, c) for c in PUB_COLUMNS]
        cols = ", ".join(PUB_COLUMNS)
        marks = ", ".join("?" for _ in PUB_COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in PUB_COLUMNS[1:])
        self.conn.execute(
            f"""INSERT INTO publications ({cols}, raw_json, fetched_at) VALUES ({marks}, ?, ?)
                ON CONFLICT(id) DO UPDATE SET {updates},
                    raw_json = excluded.raw_json, fetched_at = excluded.fetched_at""",
            (*values, json.dumps(raw, ensure_ascii=False), now),
        )
        for table in ("publication_authors", "publication_keywords", "publication_subjects",
                      "publication_references", "publication_citing"):
            self.conn.execute(f"DELETE FROM {table} WHERE publication_id = ?", (pub.id,))
        self.conn.executemany(
            """INSERT INTO publication_authors
               (publication_id, position, name, author_id, orcid, institution, city, country,
                affiliation, institution_code, institution_type, institution_status, unit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(pub.id, a.position, a.name, a.author_id, a.orcid, a.institution, a.city, a.country,
              a.affiliation, a.institution_code, a.institution_type, a.institution_status, a.unit)
             for a in pub.authors],
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO publication_keywords VALUES (?, ?)", [(pub.id, k) for k in pub.keywords]
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO publication_subjects VALUES (?, ?)", [(pub.id, s) for s in pub.subjects]
        )
        self.conn.executemany(
            """INSERT OR IGNORE INTO publication_references
               (publication_id, position, reference, target_id, year, ref_key) VALUES (?, ?, ?, ?, ?, ?)""",
            [(pub.id, r.position, r.text, r.target_id, r.year, r.key) for r in pub.references],
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO publication_citing VALUES (?, ?)", [(pub.id, c) for c in pub.cited_by]
        )

    def add_records(self, dataset: str | int, records: Iterable[dict[str, Any]]) -> int:
        """Ham kayıtları normalize edip veri setine ekler. Eklenen kayıt sayısını döndürür."""
        ds_id = dataset if isinstance(dataset, int) else self.dataset_id(dataset)
        now = _now()
        n = 0
        with self.conn:
            for raw in records:
                pub = normalize_publication(raw)
                if not pub.id:
                    continue
                self._write_publication(pub, raw, now)
                self.conn.execute(
                    "INSERT OR IGNORE INTO dataset_publications VALUES (?, ?)", (ds_id, pub.id)
                )
                n += 1
            self.conn.execute("UPDATE datasets SET updated_at = ? WHERE id = ?", (now, ds_id))
        return n

    def reindex(self) -> int:
        """Saklanan ham JSON'dan tüm yayınları yeniden normalize eder."""
        rows = self.conn.execute("SELECT raw_json, fetched_at FROM publications").fetchall()
        with self.conn:
            for r in rows:
                raw = json.loads(r["raw_json"])
                self._write_publication(normalize_publication(raw), raw, r["fetched_at"])
        return len(rows)

    def raw_records(self, dataset: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT p.raw_json FROM publications p"
        args: list[Any] = []
        if dataset:
            sql += " JOIN dataset_publications dp ON dp.publication_id = p.id WHERE dp.dataset_id = ?"
            args.append(self.dataset_id(dataset))
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [json.loads(r["raw_json"]) for r in self.conn.execute(sql, args)]

    def publications(self, dataset: str | None = None) -> list[Publication]:
        """Veri setindeki yayınları (normalize edilmiş) döndürür; varsa OpenAlex bilgisi eklenir."""
        pubs = [normalize_publication(r) for r in self.raw_records(dataset)]
        oa = self.openalex_map()
        for p in pubs:
            m = oa.get(p.id)
            if m:
                p.openalex_id, p.openalex_citations = m["openalex_id"], m["cited_by_count"]
                p.openalex_year, p.openalex_matched_by = m["publication_year"], m["matched_by"]
        return pubs

    # ---- OpenAlex ------------------------------------------------------

    def save_openalex(self, matches: Iterable[Any]) -> int:
        """`openalex.OpenAlexMatch` kayıtlarını saklar (aynı yayın için öncekinin üzerine yazar)."""
        now = _now()
        n = 0
        with self.conn:
            for m in matches:
                self.conn.execute(
                    """INSERT OR REPLACE INTO openalex_works (publication_id, openalex_id, cited_by_count,
                       publication_year, doi, type, language, matched_by, title_similarity, title, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (m.publication_id, m.openalex_id, m.cited_by_count, m.publication_year, m.doi, m.type,
                     m.language, m.matched_by, m.title_similarity, getattr(m, "title", None), now),
                )
                n += 1
        return n

    def data_version(self, name: str) -> str:
        """Önbellek anahtarı: veri setinin kayıtları ya da OpenAlex eşleşmeleri değişince değişir."""
        row = self.conn.execute("SELECT updated_at FROM datasets WHERE name = ?", (name,)).fetchone()
        try:
            oa = self.conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM openalex_works").fetchone()
        except sqlite3.OperationalError:  # tablo yok: eski, salt okunur açılmış dosya
            oa = (0, None)
        return f"{row['updated_at'] if row else ''}|{oa[0]}|{oa[1] or ''}"

    def openalex_map(self) -> dict[str, dict[str, Any]]:
        try:
            rows = self.conn.execute("SELECT * FROM openalex_works").fetchall()
        except sqlite3.OperationalError:  # tablo yok: OpenAlex'ten önce oluşturulmuş, salt okunur açılmış dosya
            return {}
        return {r["publication_id"]: dict(r) for r in rows}

    # ---- eşanlamlılar sözlüğü --------------------------------------------

    def load_thesaurus(self):
        """Kayıtlı sözlük (tüm veri setleri için ortak); yoksa boş sözlük."""
        from .thesaurus import Thesaurus

        try:
            row = self.conn.execute("SELECT json FROM thesaurus WHERE id = 1").fetchone()
        except sqlite3.OperationalError:  # tablo yok: eski, salt okunur açılmış dosya
            return Thesaurus()
        return Thesaurus.from_json(row["json"]) if row else Thesaurus()

    def save_thesaurus(self, thes) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO thesaurus (id, json, updated_at) VALUES (1, ?, ?)",
                              (thes.to_json(), _now()))

    def thesaurus_version(self) -> str:
        """Önbellek anahtarı: sözlük her kaydedildiğinde değişir."""
        try:
            row = self.conn.execute("SELECT json FROM thesaurus WHERE id = 1").fetchone()
        except sqlite3.OperationalError:
            return ""
        return hashlib.sha1(row["json"].encode("utf-8")).hexdigest()[:12] if row else ""
