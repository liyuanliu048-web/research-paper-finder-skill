from __future__ import annotations

import argparse
import json
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from _workspace import resolve_workspace_root
from paper_finder_common import clean_text, doi_url, join_keywords, normalize_doi, normalize_title, write_simple_xlsx
from topic_profile import QueryBlock, SeedWork, TopicProfile, build_query_blocks, load_topic_profile


REFERENCE_DIR = Path(__file__).resolve().parent.parent / "references"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_API_URL = "http://export.arxiv.org/api/query"
USER_AGENT = "CodexResearchPaperFinder/2026.03 (+https://openai.com)"
PARATEXT_PREFIXES = (
    "figure ",
    "editorial",
    "erratum",
    "corrigendum",
    "front matter",
    "preface",
    "table of contents",
)
SEMANTIC_SCHOLAR_FIELDS = (
    "paperId,title,abstract,year,venue,url,authors,externalIds,"
    "isOpenAccess,openAccessPdf,publicationTypes,fieldsOfStudy"
)


@dataclass
class Record:
    title: str
    authors: list[str]
    year: int | None
    journal: str
    doi: str
    url: str
    database_sources: set[str] = field(default_factory=set)
    abstract: str = ""
    keywords: str = ""
    note: str = ""
    origins: set[str] = field(default_factory=set)
    openalex_id: str = ""
    semantic_scholar_id: str = ""
    arxiv_id: str = ""
    entry_type: str = "JOUR"
    relevance_score: int = 0
    keep: bool = False

    @property
    def authors_str(self) -> str:
        return "; ".join(self.authors)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search and export a deduplicated research-paper candidate set.")
    parser.add_argument("--workdir", help="Directory where outputs should be written.")
    parser.add_argument("--topic-profile", help="Path to topic_profile.json when it is outside the workdir.")
    parser.add_argument("--prefix", default="paper_finder", help="Output filename prefix. Default: paper_finder")
    parser.add_argument(
        "--max-query-blocks",
        type=int,
        default=24,
        help="Maximum number of query blocks to execute. Default: 24",
    )
    parser.add_argument(
        "--skip-citation-expansion",
        action="store_true",
        help="Skip forward-citation and backward-reference expansion for a faster first pass.",
    )
    parser.add_argument(
        "--allow-auto-backref-seeds",
        action="store_true",
        help="Allow backward-reference expansion from auto-selected hits when no verified seed works are supplied.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Write only JSON outputs and skip XLSX, RIS, and Markdown derivatives.",
    )
    return parser.parse_args()


def sleep_for(source: str) -> None:
    if source == "semantic_scholar":
        time.sleep(0.8 + random.random() * 0.3)
    elif source == "arxiv":
        time.sleep(0.5 + random.random() * 0.2)
    else:
        time.sleep(0.18 + random.random() * 0.05)


def fetch_json(url: str, source: str, retries: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                sleep_for(source)
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def fetch_text(url: str, source: str, retries: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/atom+xml,application/xml,text/xml,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8", errors="ignore")
                sleep_for(source)
                return payload
        except urllib.error.URLError as exc:
            last_error = exc
            if source == "arxiv" and "CERTIFICATE_VERIFY_FAILED" in str(exc.reason):
                context = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=30, context=context) as response:
                    payload = response.read().decode("utf-8", errors="ignore")
                    sleep_for(source)
                    return payload
            if attempt < retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep((2**attempt) + random.random())
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def openalex_abstract(item: dict) -> str:
    inverted = item.get("abstract_inverted_index") or {}
    if not inverted:
        return ""
    max_pos = max((pos for positions in inverted.values() for pos in positions), default=-1)
    words = [""] * (max_pos + 1)
    for word, positions in inverted.items():
        for pos in positions:
            words[pos] = word
    return clean_text(" ".join(words))


def openalex_authors(item: dict) -> list[str]:
    authors: list[str] = []
    for authorship in item.get("authorships") or []:
        author = clean_text(((authorship.get("author") or {}).get("display_name")))
        if author:
            authors.append(author)
    return authors


def semantic_scholar_authors(item: dict) -> list[str]:
    authors: list[str] = []
    for author in item.get("authors") or []:
        name = clean_text(author.get("name"))
        if name:
            authors.append(name)
    return authors


def arxiv_authors(entry: ET.Element) -> list[str]:
    authors: list[str] = []
    for author in entry.findall("atom:author", ARXIV_NS):
        name = clean_text(author.findtext("atom:name", default="", namespaces=ARXIV_NS))
        if name:
            authors.append(name)
    return authors


def completeness_score(record: Record) -> int:
    score = 0
    score += 8 if record.doi else 0
    score += 6 if record.abstract else 0
    score += 4 if record.url else 0
    score += 4 if record.journal else 0
    score += 2 if record.openalex_id else 0
    score += 2 if record.semantic_scholar_id else 0
    score += 2 if record.arxiv_id else 0
    score += min(len(record.authors), 5)
    score += min(len(record.origins), 8)
    return score


def record_key(record: Record) -> tuple[str, str]:
    if record.doi:
        return ("doi", record.doi)
    return ("title", f"{normalize_title(record.title)}|{record.year or ''}")


def merge_records(existing: Record, incoming: Record) -> Record:
    best = existing if completeness_score(existing) >= completeness_score(incoming) else incoming
    other = incoming if best is existing else existing
    return Record(
        title=best.title or other.title,
        authors=best.authors or other.authors,
        year=best.year or other.year,
        journal=best.journal or other.journal,
        doi=best.doi or other.doi,
        url=best.url or other.url,
        database_sources=set(existing.database_sources) | set(incoming.database_sources),
        abstract=best.abstract or other.abstract,
        keywords=join_keywords(
            (best.keywords.split("; ") if best.keywords else []) + (other.keywords.split("; ") if other.keywords else [])
        ),
        note=best.note or other.note,
        origins=set(existing.origins) | set(incoming.origins),
        openalex_id=best.openalex_id or other.openalex_id,
        semantic_scholar_id=best.semantic_scholar_id or other.semantic_scholar_id,
        arxiv_id=best.arxiv_id or other.arxiv_id,
        entry_type=best.entry_type if best.entry_type != "JOUR" else other.entry_type,
        relevance_score=max(existing.relevance_score, incoming.relevance_score),
        keep=existing.keep or incoming.keep,
    )


def year_within_scope(record: Record, profile: TopicProfile) -> bool:
    if record.year is None:
        return True
    if profile.recent_end is not None and record.year > profile.recent_end:
        return False
    if profile.recent_start is None:
        return True
    if record.year >= profile.recent_start:
        return True
    return profile.include_classics_before_recent


def term_hits(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def analyze_record(record: Record, profile: TopicProfile) -> tuple[bool, int, list[str]]:
    text = " ".join([record.title, record.abstract, record.keywords, record.journal, " ".join(record.authors)]).lower()
    title_lower = record.title.lower()
    if any(title_lower.startswith(prefix) for prefix in PARATEXT_PREFIXES):
        return False, -10, ["paratext"]

    core_hits = term_hits(text, profile.core_terms)
    mechanism_hits = term_hits(text, profile.mechanism_terms)
    context_hits = term_hits(text, profile.context_terms)
    method_hits = term_hits(text, profile.method_terms)
    theory_hits = term_hits(text, profile.theory_terms)
    exclusion_hits = term_hits(text, profile.exclusion_terms)
    author_hits = [author for author in profile.seed_authors if author.lower() in text]
    seed_origin = any(origin.startswith("seed:") or origin.startswith("oa_cites:") or origin.startswith("oa_backref:") for origin in record.origins)

    if exclusion_hits and not core_hits:
        return False, -10, ["explicit-exclusion"]

    score = 0
    reasons: list[str] = []
    if any(term.lower() in title_lower for term in profile.core_terms):
        score += 6
        reasons.append("title-core")
    elif core_hits:
        score += 4
        reasons.append("core")
    if mechanism_hits:
        score += min(3, len(mechanism_hits))
        reasons.append("mechanism")
    if context_hits:
        score += min(3, len(context_hits))
        reasons.append("context")
    if method_hits:
        score += min(2, len(method_hits))
        reasons.append("method")
    if theory_hits:
        score += min(2, len(theory_hits))
        reasons.append("theory")
    if author_hits:
        score += 1
        reasons.append("seed-author")
    if seed_origin:
        score += 2
        reasons.append("seed-tracking")
    if record.doi:
        score += 1
    if not core_hits and not seed_origin:
        score -= 3
        reasons.append("weak-core-link")

    keep = False
    if seed_origin and score >= 2:
        keep = True
    elif core_hits and score >= 5:
        keep = True
    elif score >= 8 and (mechanism_hits or context_hits or theory_hits or author_hits):
        keep = True

    if keep and not year_within_scope(record, profile):
        return False, score, reasons + ["out-of-scope-year"]
    return keep, score, reasons


def add_candidate(pool: dict[tuple[str, str], Record], record: Record, profile: TopicProfile) -> bool:
    keep, score, reasons = analyze_record(record, profile)
    if not keep:
        return False
    record.keep = True
    record.relevance_score = score
    record.note = "; ".join(reasons)
    key = record_key(record)
    existing = pool.get(key)
    if existing is None:
        pool[key] = record
        return True
    pool[key] = merge_records(existing, record)
    return completeness_score(record) > completeness_score(existing)


def build_openalex_record(item: dict, origin: str) -> Record:
    journal = clean_text((((item.get("primary_location") or {}).get("source") or {}).get("display_name")))
    if not journal:
        journal = clean_text((item.get("primary_location") or {}).get("raw_source_name"))
    doi = normalize_doi(item.get("doi"))
    keywords = join_keywords(
        [kw.get("display_name", "") for kw in item.get("keywords", [])]
        + [topic.get("display_name", "") for topic in item.get("topics", [])[:8]]
    )
    url = clean_text((item.get("primary_location") or {}).get("landing_page_url")) or doi_url(doi) or clean_text(item.get("id"))
    return Record(
        title=clean_text(item.get("display_name") or item.get("title")),
        authors=openalex_authors(item),
        year=item.get("publication_year"),
        journal=journal,
        doi=doi,
        url=url,
        database_sources={"OpenAlex"},
        abstract=openalex_abstract(item),
        keywords=keywords,
        origins={origin},
        openalex_id=clean_text(item.get("id")).rsplit("/", 1)[-1],
        entry_type="CHAP" if item.get("type") == "book-chapter" else "BOOK" if item.get("type") in {"book", "edited-book"} else "JOUR",
    )


def build_semantic_scholar_record(item: dict, origin: str) -> Record:
    external_ids = item.get("externalIds") or {}
    doi = normalize_doi(external_ids.get("DOI"))
    arxiv_id = clean_text(external_ids.get("ArXiv"))
    open_access_pdf = clean_text(((item.get("openAccessPdf") or {}).get("url")))
    url = open_access_pdf or clean_text(item.get("url")) or doi_url(doi)
    keywords = join_keywords(
        list(item.get("fieldsOfStudy") or [])
        + list(item.get("publicationTypes") or [])
        + [clean_text(item.get("venue"))]
    )
    return Record(
        title=clean_text(item.get("title")),
        authors=semantic_scholar_authors(item),
        year=item.get("year"),
        journal=clean_text(item.get("venue")),
        doi=doi,
        url=url,
        database_sources={"Semantic Scholar"},
        abstract=clean_text(item.get("abstract")),
        keywords=keywords,
        origins={origin},
        semantic_scholar_id=clean_text(item.get("paperId")),
        arxiv_id=arxiv_id,
        entry_type="PREPRINT" if arxiv_id else "JOUR",
    )


def build_arxiv_record(entry: ET.Element, origin: str) -> Record:
    title = clean_text(entry.findtext("atom:title", default="", namespaces=ARXIV_NS))
    summary = clean_text(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS))
    published = clean_text(entry.findtext("atom:published", default="", namespaces=ARXIV_NS))
    year = int(published[:4]) if re.match(r"^\d{4}", published) else None
    doi = normalize_doi(entry.findtext("arxiv:doi", default="", namespaces=ARXIV_NS))
    arxiv_abs_url = clean_text(entry.findtext("atom:id", default="", namespaces=ARXIV_NS))
    arxiv_id = arxiv_abs_url.rsplit("/", 1)[-1]
    journal_ref = clean_text(entry.findtext("arxiv:journal_ref", default="", namespaces=ARXIV_NS))
    pdf_url = ""
    for link in entry.findall("atom:link", ARXIV_NS):
        if clean_text(link.get("type")) == "application/pdf":
            pdf_url = clean_text(link.get("href"))
            break
    return Record(
        title=title,
        authors=arxiv_authors(entry),
        year=year,
        journal=journal_ref,
        doi=doi,
        url=pdf_url or arxiv_abs_url or doi_url(doi),
        database_sources={"arXiv"},
        abstract=summary,
        keywords="preprint; arXiv",
        origins={origin},
        arxiv_id=arxiv_id,
        entry_type="PREPRINT",
    )


def openalex_search(profile: TopicProfile, block: QueryBlock, page: int, per_page: int) -> dict:
    params = {
        "search": block.query,
        "page": str(page),
        "per-page": str(per_page),
        "sort": "relevance_score:desc",
    }
    filters = []
    if profile.recent_start is not None:
        filters.append(f"from_publication_date:{profile.recent_start}-01-01")
    if profile.recent_end is not None:
        filters.append(f"to_publication_date:{profile.recent_end}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    return fetch_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), "openalex")


def openalex_work_by_doi(doi: str) -> dict:
    query = urllib.parse.urlencode({"filter": f"doi:https://doi.org/{doi}", "per-page": "1"})
    data = fetch_json(f"https://api.openalex.org/works?{query}", "openalex")
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"OpenAlex DOI not found: {doi}")
    return results[0]


def openalex_work_by_id(work_id: str) -> dict:
    short_id = clean_text(work_id).rsplit("/", 1)[-1]
    return fetch_json(f"https://api.openalex.org/works/{urllib.parse.quote(short_id)}", "openalex")


def openalex_find_work_by_title(title: str) -> dict:
    params = {
        "search": clean_text(title),
        "per-page": "5",
        "sort": "relevance_score:desc",
    }
    data = fetch_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), "openalex")
    wanted = normalize_title(title)
    for item in data.get("results") or []:
        candidate = clean_text(item.get("display_name") or item.get("title"))
        if normalize_title(candidate) == wanted:
            return item
    raise RuntimeError(f"OpenAlex title not found: {title}")


def semantic_scholar_search(block: QueryBlock, offset: int, limit: int) -> dict:
    params = {
        "query": block.query,
        "offset": str(offset),
        "limit": str(limit),
        "fields": SEMANTIC_SCHOLAR_FIELDS,
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    return fetch_json(url, "semantic_scholar")


def semantic_scholar_paper_by_identifier(identifier: str) -> dict | None:
    if not identifier:
        return None
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/"
        f"{urllib.parse.quote(identifier, safe='')}?fields={urllib.parse.quote(SEMANTIC_SCHOLAR_FIELDS, safe=',')}"
    )
    try:
        return fetch_json(url, "semantic_scholar")
    except Exception:
        return None


def build_arxiv_search_query(block: QueryBlock) -> str:
    tokens = [token for token in re.split(r"\s+", clean_text(block.query)) if len(token) >= 3]
    if not tokens:
        return 'all:"literature"'
    return " AND ".join(f'all:"{token}"' for token in tokens[:4])


def arxiv_search(block: QueryBlock, start: int, max_results: int) -> ET.Element:
    params = {
        "search_query": build_arxiv_search_query(block),
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = ARXIV_API_URL + "?" + urllib.parse.urlencode(params)
    return ET.fromstring(fetch_text(url, "arxiv"))


def choose_recent_backref_seeds(records: list[Record], profile: TopicProfile, limit: int = 8) -> list[Record]:
    eligible = [
        record
        for record in records
        if record.keep and record.openalex_id and record.relevance_score >= 8 and (record.year or 0) >= (profile.recent_start or 0)
    ]
    eligible.sort(key=lambda rec: (-rec.relevance_score, -(rec.year or 0), rec.title.lower()))
    unique: list[Record] = []
    seen: set[str] = set()
    for record in eligible:
        key = record.doi or normalize_title(record.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
        if len(unique) >= limit:
            break
    return unique


def format_ris_record(record: Record) -> str:
    lines = [f"TY  - {record.entry_type}"]
    for author in record.authors:
        lines.append(f"AU  - {author}")
    lines.append(f"TI  - {record.title}")
    if record.year:
        lines.append(f"PY  - {record.year}")
    if record.journal:
        lines.append(f"JO  - {record.journal}")
    if record.doi:
        lines.append(f"DO  - {record.doi}")
    if record.url:
        lines.append(f"UR  - {record.url}")
    if record.abstract:
        lines.append(f"AB  - {record.abstract}")
    if record.keywords:
        for keyword in record.keywords.split("; "):
            if keyword:
                lines.append(f"KW  - {keyword}")
    if record.note:
        lines.append(f"N1  - {record.note}")
    lines.append("ER  -")
    return "\n".join(lines)


def write_markdown_report(
    path: Path,
    profile: TopicProfile,
    output_prefix: str,
    raw_count: int,
    dedup_count: int,
    source_distribution: dict[str, int],
    query_logs: list[dict[str, object]],
    access_notes: list[str],
    backref_seeds: list[Record],
    query_blocks: list[QueryBlock],
) -> None:
    lines = [
        "# Research Paper Finder Report",
        "",
        "## Summary",
        f"- Topic: {profile.topic_name or 'Unspecified topic'}",
        f"- Research goal: {profile.research_goal or 'Not provided'}",
        f"- Output prefix: `{output_prefix}`",
        f"- Generated on: {datetime.now(timezone.utc).date().isoformat()}",
        f"- Query blocks planned: {len(query_blocks)}",
        f"- Estimated kept additions before dedup: {raw_count}",
        f"- Deduplicated records: {dedup_count}",
        "",
        "## Databases",
        f"- Requested: {', '.join(profile.preferred_databases or ['openalex', 'semantic_scholar', 'arxiv'])}",
        f"- Automated: {', '.join(profile.enabled_databases)}",
    ]
    if profile.unsupported_databases:
        lines.append(f"- Manual-only follow-up suggested: {', '.join(profile.unsupported_databases)}")
    lines.extend(["", "## Access Notes"])
    lines.extend(f"- {note}" for note in access_notes)
    lines.extend(["", "## Source Distribution"])
    if source_distribution:
        for source_label, count in sorted(source_distribution.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {source_label}: {count}")
    else:
        lines.append("- No kept records.")
    lines.extend(["", "## Query Blocks"])
    for block in query_blocks:
        lines.append(f"- {block.kind} | {block.label} | {block.query}")
    lines.extend(["", "## Query Log"])
    if query_logs:
        for log in query_logs:
            lines.append(
                f"- {log.get('source')} | {log.get('label')} | raw {log.get('raw')} | kept {log.get('kept')} | {log.get('note')}"
            )
    else:
        lines.append("- No query log entries.")
    lines.extend(["", "## Backward-Reference Seeds"])
    if backref_seeds:
        for seed in backref_seeds:
            lines.append(f"- {seed.title} ({seed.year or 'n.d.'}) | score {seed.relevance_score}")
    else:
        lines.append("- No backward-reference seeds selected.")
    lines.extend(
        [
            "",
            "## Limits",
            "- Structured APIs can miss records with delayed indexing, missing abstracts, or weak identifier links.",
            "- Specialist databases may still require a separate manual supplement.",
            "- This run builds a candidate set. Final inclusion decisions should be made during screening.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_identifiers(seed: SeedWork) -> list[str]:
    candidates = [
        seed.semantic_scholar_id,
        f"DOI:{seed.doi}" if seed.doi else "",
        seed.doi,
        f"ARXIV:{seed.arxiv_id}" if seed.arxiv_id else "",
        seed.arxiv_id,
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        cleaned = clean_text(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def load_seed_record(seed: SeedWork, candidate_pool: dict[tuple[str, str], Record], profile: TopicProfile, query_logs: list[dict[str, object]]) -> str:
    origin_label = clean_text(seed.best_label) or "seed"
    kept = 0
    openalex_id = ""

    try:
        item = None
        if seed.openalex_id:
            item = openalex_work_by_id(seed.openalex_id)
        elif seed.doi:
            item = openalex_work_by_doi(seed.doi)
        elif seed.title:
            item = openalex_find_work_by_title(seed.title)
        if item:
            record = build_openalex_record(item, f"seed:{origin_label}")
            openalex_id = record.openalex_id
            kept += int(add_candidate(candidate_pool, record, profile))
            query_logs.append(
                {"label": f"seed:{origin_label}", "source": "OpenAlex", "raw": 1, "kept": kept, "note": "seed recovery"}
            )
    except Exception as exc:
        query_logs.append(
            {"label": f"seed:{origin_label}", "source": "OpenAlex", "raw": 0, "kept": 0, "note": f"seed fetch failed: {exc}"}
        )

    semantic_kept = 0
    for identifier in seed_identifiers(seed):
        paper = semantic_scholar_paper_by_identifier(identifier)
        if not paper:
            continue
        semantic_kept += int(add_candidate(candidate_pool, build_semantic_scholar_record(paper, f"seed:{origin_label}"), profile))
        query_logs.append(
            {
                "label": f"seed:{origin_label}",
                "source": "Semantic Scholar",
                "raw": 1,
                "kept": semantic_kept,
                "note": f"seed recovery via {identifier}",
            }
        )
        break
    return openalex_id


def main() -> None:
    args = parse_args()
    workdir = Path(args.workdir).resolve() if args.workdir else resolve_workspace_root(__file__)
    workdir.mkdir(parents=True, exist_ok=True)
    explicit_profile = Path(args.topic_profile).resolve() if args.topic_profile else None
    profile = load_topic_profile(workdir, reference_dir=REFERENCE_DIR, explicit_path=explicit_profile)

    candidate_pool: dict[tuple[str, str], Record] = {}
    raw_kept_counter = 0
    query_logs: list[dict[str, object]] = []
    access_notes = [
        "OpenAlex used as the primary structured metadata and citation source.",
        "Semantic Scholar Graph API used as a complementary discovery and metadata source.",
        "arXiv API used as the preprint supplement.",
    ]
    if profile.unsupported_databases:
        access_notes.append(
            "Manual follow-up still needed for unsupported databases: " + ", ".join(profile.unsupported_databases)
        )
    if not args.skip_citation_expansion and not profile.seed_works and not args.allow_auto_backref_seeds:
        access_notes.append(
            "Automatic backward-reference expansion was skipped because no verified seed works were supplied."
        )

    seed_openalex_ids: dict[str, str] = {}
    for seed in profile.seed_works:
        openalex_id = load_seed_record(seed, candidate_pool, profile, query_logs)
        if openalex_id:
            seed_openalex_ids[seed.best_label] = openalex_id

    query_modules = build_query_blocks(profile, limit=max(args.max_query_blocks, 1))
    enabled = set(profile.enabled_databases)

    for block in query_modules:
        if "openalex" in enabled:
            raw = 0
            kept_before = len(candidate_pool)
            pages = 2 if block.kind in {"topic", "core"} else 1
            try:
                for page in range(1, pages + 1):
                    data = openalex_search(profile, block, page, 80 if block.kind in {"topic", "core"} else 50)
                    results = data.get("results") or []
                    raw += len(results)
                    for item in results:
                        add_candidate(candidate_pool, build_openalex_record(item, block.label), profile)
                kept_after = len(candidate_pool)
                delta = max(kept_after - kept_before, 0)
                raw_kept_counter += delta
                query_logs.append({"label": block.label, "source": "OpenAlex", "raw": raw, "kept": delta, "note": block.query})
            except Exception as exc:
                query_logs.append({"label": block.label, "source": "OpenAlex", "raw": 0, "kept": 0, "note": f"failed: {exc}"})

        if "semantic_scholar" in enabled:
            raw = 0
            kept_before = len(candidate_pool)
            try:
                offsets = [0, 50] if block.kind in {"topic", "core"} else [0]
                for offset in offsets:
                    data = semantic_scholar_search(block, offset=offset, limit=50)
                    items = data.get("data") or []
                    raw += len(items)
                    for item in items:
                        add_candidate(candidate_pool, build_semantic_scholar_record(item, block.label), profile)
                kept_after = len(candidate_pool)
                delta = max(kept_after - kept_before, 0)
                raw_kept_counter += delta
                query_logs.append(
                    {"label": block.label, "source": "Semantic Scholar", "raw": raw, "kept": delta, "note": block.query}
                )
            except Exception as exc:
                query_logs.append(
                    {"label": block.label, "source": "Semantic Scholar", "raw": 0, "kept": 0, "note": f"failed: {exc}"}
                )

    for block in query_modules[: min(len(query_modules), 8)]:
        if "arxiv" not in enabled or block.kind == "author":
            continue
        raw = 0
        kept_before = len(candidate_pool)
        try:
            root = arxiv_search(block, start=0, max_results=40 if block.kind in {"topic", "core"} else 20)
            entries = root.findall("atom:entry", ARXIV_NS)
            raw = len(entries)
            for entry in entries:
                add_candidate(candidate_pool, build_arxiv_record(entry, block.label), profile)
            kept_after = len(candidate_pool)
            delta = max(kept_after - kept_before, 0)
            raw_kept_counter += delta
            query_logs.append({"label": block.label, "source": "arXiv", "raw": raw, "kept": delta, "note": build_arxiv_search_query(block)})
        except Exception as exc:
            query_logs.append({"label": block.label, "source": "arXiv", "raw": 0, "kept": 0, "note": f"failed: {exc}"})

    recent_backref_seeds: list[Record] = []
    if not args.skip_citation_expansion:
        for label, openalex_id in seed_openalex_ids.items():
            raw = 0
            kept_before = len(candidate_pool)
            try:
                for page in range(1, 3):
                    filters = [f"cites:{openalex_id}"]
                    if profile.recent_start is not None:
                        filters.append(f"from_publication_date:{profile.recent_start}-01-01")
                    if profile.recent_end is not None:
                        filters.append(f"to_publication_date:{profile.recent_end}-12-31")
                    params = {
                        "filter": ",".join(filters),
                        "page": str(page),
                        "per-page": "80",
                        "sort": "cited_by_count:desc",
                    }
                    data = fetch_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), "openalex")
                    results = data.get("results") or []
                    raw += len(results)
                    for item in results:
                        add_candidate(candidate_pool, build_openalex_record(item, f"oa_cites:{label}"), profile)
                kept_after = len(candidate_pool)
                delta = max(kept_after - kept_before, 0)
                raw_kept_counter += delta
                query_logs.append(
                    {
                        "label": f"oa_cites:{label}",
                        "source": "OpenAlex",
                        "raw": raw,
                        "kept": delta,
                        "note": f"forward citations from {label}",
                    }
                )
            except Exception as exc:
                query_logs.append(
                    {"label": f"oa_cites:{label}", "source": "OpenAlex", "raw": 0, "kept": 0, "note": f"failed: {exc}"}
                )

        should_expand_backrefs = bool(profile.seed_works) or args.allow_auto_backref_seeds
        if should_expand_backrefs:
            recent_backref_seeds = choose_recent_backref_seeds(list(candidate_pool.values()), profile)
            fetched_backref_ids: set[str] = set()
            for seed in recent_backref_seeds:
                raw = 0
                kept_before = len(candidate_pool)
                try:
                    seed_item = openalex_work_by_id(seed.openalex_id)
                    for ref_id in (seed_item.get("referenced_works") or [])[:40]:
                        short_id = clean_text(ref_id).rsplit("/", 1)[-1]
                        if not short_id or short_id in fetched_backref_ids:
                            continue
                        fetched_backref_ids.add(short_id)
                        raw += 1
                        try:
                            ref_item = openalex_work_by_id(short_id)
                            add_candidate(candidate_pool, build_openalex_record(ref_item, f"oa_backref:{seed.openalex_id}"), profile)
                        except Exception:
                            continue
                    kept_after = len(candidate_pool)
                    delta = max(kept_after - kept_before, 0)
                    raw_kept_counter += delta
                    query_logs.append(
                        {
                            "label": f"oa_backref:{seed.openalex_id}",
                            "source": "OpenAlex",
                            "raw": raw,
                            "kept": delta,
                            "note": seed.title[:80],
                        }
                    )
                except Exception as exc:
                    query_logs.append(
                        {"label": f"oa_backref:{seed.openalex_id}", "source": "OpenAlex", "raw": 0, "kept": 0, "note": f"failed: {exc}"}
                    )
        elif candidate_pool:
            query_logs.append(
                {
                    "label": "oa_backref_auto_seeding",
                    "source": "OpenAlex",
                    "raw": 0,
                    "kept": 0,
                    "note": "skipped: no verified seed works; rerun with --allow-auto-backref-seeds to opt in",
                }
            )

    deduped_records = list(candidate_pool.values())
    deduped_records.sort(key=lambda rec: (-(rec.relevance_score), -(rec.year or 0), rec.title.lower()))

    headers = [
        "Title",
        "Authors",
        "Year",
        "Journal / Source",
        "DOI",
        "URL",
        "Database / Source",
        "Abstract",
        "Keywords",
        "OpenAlex ID",
        "Semantic Scholar ID",
        "arXiv ID",
        "Relevance Score",
        "Relevance Note",
        "Source Tracking",
    ]
    rows: list[list[object]] = []
    source_distribution: dict[str, int] = {}
    for record in deduped_records:
        source_label = "; ".join(sorted(record.database_sources))
        source_distribution[source_label] = source_distribution.get(source_label, 0) + 1
        rows.append(
            [
                record.title,
                record.authors_str,
                record.year or "",
                record.journal,
                record.doi,
                record.url,
                source_label,
                record.abstract,
                record.keywords,
                record.openalex_id,
                record.semantic_scholar_id,
                record.arxiv_id,
                record.relevance_score,
                record.note,
                "; ".join(sorted(record.origins)),
            ]
        )

    output_prefix = clean_text(args.prefix) or "paper_finder"
    results_json_path = workdir / f"{output_prefix}_results.json"
    source_log_json_path = workdir / f"{output_prefix}_source_log.json"
    results_xlsx_path = workdir / f"{output_prefix}_results.xlsx"
    results_ris_path = workdir / f"{output_prefix}_results.ris"
    source_log_md_path = workdir / f"{output_prefix}_source_log.md"

    results_json = {
        "tool": "research-paper-finder",
        "topic_profile_path": str(profile.source_path) if profile.source_path else "",
        "generated_on": datetime.now(timezone.utc).isoformat(),
        "output_prefix": output_prefix,
        "requested_databases": profile.preferred_databases or ["openalex", "semantic_scholar", "arxiv"],
        "enabled_databases": profile.enabled_databases,
        "unsupported_databases": profile.unsupported_databases,
        "query_blocks": [{"label": block.label, "kind": block.kind, "query": block.query} for block in query_modules],
        "headers": headers,
        "records": [
            {
                "title": record.title,
                "authors": record.authors,
                "year": record.year,
                "journal_source": record.journal,
                "doi": record.doi,
                "url": record.url,
                "database_sources": sorted(record.database_sources),
                "abstract": record.abstract,
                "keywords": record.keywords,
                "openalex_id": record.openalex_id,
                "semantic_scholar_id": record.semantic_scholar_id,
                "arxiv_id": record.arxiv_id,
                "entry_type": record.entry_type,
                "relevance_score": record.relevance_score,
                "relevance_note": record.note,
                "source_tracking": sorted(record.origins),
            }
            for record in deduped_records
        ],
    }
    source_log_json = {
        "tool": "research-paper-finder",
        "generated_on": datetime.now(timezone.utc).isoformat(),
        "output_prefix": output_prefix,
        "raw_kept_count": raw_kept_counter,
        "deduplicated_count": len(deduped_records),
        "source_distribution": source_distribution,
        "access_notes": access_notes,
        "query_logs": query_logs,
        "backref_seeds": [
            {
                "title": seed.title,
                "authors": seed.authors,
                "year": seed.year,
                "doi": seed.doi,
                "openalex_id": seed.openalex_id,
            }
            for seed in recent_backref_seeds
        ],
    }

    results_json_path.write_text(json.dumps(results_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_log_json_path.write_text(json.dumps(source_log_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.json_only:
        write_simple_xlsx(results_xlsx_path, headers=headers, rows=rows, sheet_name="paper_results")
        results_ris_path.write_text(
            ("\n\n".join(format_ris_record(record) for record in deduped_records) + "\n") if deduped_records else "",
            encoding="utf-8",
        )
        write_markdown_report(
            source_log_md_path,
            profile=profile,
            output_prefix=output_prefix,
            raw_count=raw_kept_counter,
            dedup_count=len(deduped_records),
            source_distribution=source_distribution,
            query_logs=query_logs,
            access_notes=access_notes,
            backref_seeds=recent_backref_seeds,
            query_blocks=query_modules,
        )

    print(f"Deduplicated candidates: {len(deduped_records)}")
    print(results_json_path)
    print(source_log_json_path)
    if not args.json_only:
        print(results_xlsx_path)
        print(results_ris_path)
        print(source_log_md_path)


if __name__ == "__main__":
    main()
