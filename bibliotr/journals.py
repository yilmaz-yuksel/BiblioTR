"""TR Dizin dergi kayıtları (`api/journalById/{id}`).

Dergi kaydı yayınları içermez; derginin kimlik bilgilerini, TR Dizin'de dizinlendiği
yılları (`journalYear`), konularını ve reddedildiği yılları verir. Bir derginin
yayınlarını çekmek için dergi adıyla `facet-journalName` araması yapılır ve sonuçlar
`journal.id` ile süzülür (aynı adlı başka dergiler karışmasın diye).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .normalize import as_int, as_list, as_text, first


@dataclass
class JournalYear:
    year: int
    databases: list[str] = field(default_factory=list)
    periods: list[int] = field(default_factory=list)  # yayın ayları
    subjects: list[str] = field(default_factory=list)
    missing_issue_count: int = 0
    missing_referee_report_count: int = 0
    expert_warning_count: int = 0


@dataclass
class Journal:
    id: str
    title: str | None
    other_title: str | None = None
    issn: str | None = None
    eissn: str | None = None
    first_publish_year: int | None = None
    databases: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    editors: list[str] = field(default_factory=list)
    web_address: str | None = None
    publication_format: str | None = None
    citation_count: int | None = None
    is_active: bool | None = None
    registration_date: str | None = None
    years: list[JournalYear] = field(default_factory=list)  # dizinlendiği yıllar
    rejected_years: list[int] = field(default_factory=list)
    old_journal: str | None = None
    new_journal: str | None = None

    @property
    def indexed_years(self) -> list[int]:
        return [y.year for y in self.years]

    @property
    def subjects(self) -> list[str]:
        """Tüm yıllardaki konuların birleşimi (en sık görülen önce)."""
        from collections import Counter

        c = Counter(s for y in self.years for s in y.subjects)
        return [s for s, _ in c.most_common()]

    @property
    def search_names(self) -> list[str]:
        """facet-journalName için denenecek adlar."""
        return [n for n in dict.fromkeys([self.title, self.other_title]) if n]

    @property
    def url(self) -> str:
        return f"https://search.trdizin.gov.tr/tr/dergi/detay/{self.id}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["indexed_years"] = self.indexed_years
        return d

    def summary(self) -> dict[str, Any]:
        """Kısa, tabloya uygun özet."""
        years = self.indexed_years
        return {
            "Dergi": self.title,
            "Diğer adı": self.other_title,
            "ISSN / e-ISSN": " / ".join(x for x in (self.issn, self.eissn) if x) or None,
            "İlk yayın yılı": self.first_publish_year,
            "TR Dizin'de dizinlendiği yıllar": _year_ranges(years),
            "Dizinlenen yıl sayısı": len(years),
            "Reddedildiği yıllar": ", ".join(map(str, self.rejected_years)) or None,
            "Veritabanı": ", ".join(self.databases) or None,
            "Yayın dili": ", ".join(self.languages) or None,
            "Editör": "; ".join(self.editors) or None,
            "TR Dizin atıf sayısı": self.citation_count,
            "Aktif": {True: "Evet", False: "Hayır"}.get(self.is_active),
            "Web": self.web_address,
            "TR Dizin sayfası": self.url,
        }


def _year_ranges(years: list[int]) -> str | None:
    """[2003, 2004, 2009, 2010, 2011] -> "2003-2004, 2009-2011"."""
    if not years:
        return None
    ys = sorted(set(years))
    out, start, prev = [], ys[0], ys[0]
    for y in ys[1:] + [None]:
        if y is not None and y == prev + 1:
            prev = y
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if y is not None:
            start = prev = y
    return ", ".join(out)


def _journal_ref(value: Any) -> str | None:
    """oldJournal/newJournal alanı nesne ya da kimlik olabilir."""
    if isinstance(value, dict):
        return as_text(first(value, ("title", "name", "id")))
    return as_text(value)


def normalize_journal(raw: dict[str, Any]) -> Journal:
    years = []
    for y in raw.get("journalYear") or []:
        if not isinstance(y, dict):
            continue
        year = as_int(y.get("year"))
        if year is None:
            continue
        subjects = [
            as_text(first(s, ("fullName", "name"))) if isinstance(s, dict) else as_text(s)
            for s in (y.get("subjects") or [])
        ]
        years.append(JournalYear(
            year=year,
            databases=as_list(y.get("databases")),
            periods=sorted({p for p in (as_int(x) for x in (y.get("periods") or [])) if p is not None}),
            subjects=[s for s in subjects if s],
            missing_issue_count=as_int(y.get("missingIssueCount")) or 0,
            missing_referee_report_count=as_int(y.get("missingRefereeReportCount")) or 0,
            expert_warning_count=as_int(y.get("expertWarningCount")) or 0,
        ))
    years.sort(key=lambda y: y.year)
    rejected = sorted({
        y for y in (as_int(r.get("year") if isinstance(r, dict) else r) for r in (raw.get("rejectYearList") or []))
        if y is not None
    })
    editors = [
        as_text(p.get("name")) for p in (raw.get("person") or [])
        if isinstance(p, dict) and (as_text(p.get("type")) or "EDITOR").upper() == "EDITOR"
    ]
    active = raw.get("isActive")
    return Journal(
        id=as_text(first(raw, ("id", "_id"))) or "",
        title=as_text(first(raw, ("title", "name", "orderTitle"))),
        other_title=as_text(raw.get("otherTitle")),
        issn=as_text(raw.get("issn")),
        eissn=as_text(raw.get("eissn")),
        first_publish_year=as_int(raw.get("firstPublishYear")),
        databases=as_list(raw.get("journalDatabase")),
        languages=as_list(raw.get("publicationLanguage")),
        editors=[e for e in editors if e],
        web_address=as_text(raw.get("webAddress")),
        publication_format=as_text(raw.get("publicationFormats")),
        citation_count=as_int(raw.get("orderCitationCount")),
        is_active=active if isinstance(active, bool) else None,
        registration_date=(as_text(raw.get("registrationDate")) or "")[:10] or None,
        years=years,
        rejected_years=rejected,
        old_journal=_journal_ref(raw.get("oldJournal")),
        new_journal=_journal_ref(raw.get("newJournal")),
    )


def years_frame(journal: Journal):
    """Yıl bazında dizinlenme tablosu (pandas)."""
    import pandas as pd

    return pd.DataFrame([
        {
            "Yıl": y.year,
            "Veritabanı": ", ".join(y.databases),
            "Yayın ayları": ", ".join(map(str, y.periods)),
            "Yayın ayı sayısı": len(y.periods),
            "Eksik sayı": y.missing_issue_count,
            "Eksik hakem raporu": y.missing_referee_report_count,
            "Uzman uyarısı": y.expert_warning_count,
            "Konu sayısı": len(y.subjects),
        }
        for y in journal.years
    ])


def record_journal_id(raw: dict[str, Any]) -> str | None:
    """Ham yayın kaydındaki dergi kimliği."""
    return as_text(first(raw, ("journal.id", "journalId")))
