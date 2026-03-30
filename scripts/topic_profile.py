from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from paper_finder_common import clean_text, normalize_doi, normalize_title


PROFILE_ENV_VARS = ("TOPIC_PROFILE_PATH", "RESEARCH_PAPER_FINDER_PROFILE")
PROFILE_FILE_NAMES = ("topic_profile.json", "paper_finder_topic.json", "review_topic.json")


def unique_clean(values: list[object]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = clean_text(str(item) if item is not None else "")
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return unique


def slug_label(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", clean_text(text)).strip("_").lower()
    return text[:48] or "query"


@dataclass(frozen=True)
class SeedWork:
    label: str = ""
    title: str = ""
    doi: str = ""
    openalex_id: str = ""
    semantic_scholar_id: str = ""
    arxiv_id: str = ""

    @property
    def best_label(self) -> str:
        return self.label or self.title or self.doi or self.openalex_id or self.semantic_scholar_id or self.arxiv_id


@dataclass(frozen=True)
class QueryBlock:
    label: str
    query: str
    kind: str


@dataclass
class TopicProfile:
    topic_name: str
    research_goal: str
    recent_start: int | None
    recent_end: int | None
    include_classics_before_recent: bool
    keyword_slots: dict[str, list[str]] = field(default_factory=dict)
    seed_authors: list[str] = field(default_factory=list)
    seed_works: list[SeedWork] = field(default_factory=list)
    preferred_databases: list[str] = field(default_factory=list)
    include_rules: list[str] = field(default_factory=list)
    priority_rules: list[str] = field(default_factory=list)
    exclude_rules: list[str] = field(default_factory=list)
    source_path: Path | None = None

    def slot(self, name: str) -> list[str]:
        return unique_clean(list(self.keyword_slots.get(name, [])))

    @property
    def core_terms(self) -> list[str]:
        fallback = [self.topic_name] if self.topic_name else []
        return unique_clean(self.slot("phenomenon") + self.slot("synonyms") + fallback)

    @property
    def mechanism_terms(self) -> list[str]:
        return self.slot("mechanisms")

    @property
    def context_terms(self) -> list[str]:
        return self.slot("contexts")

    @property
    def method_terms(self) -> list[str]:
        return self.slot("methods")

    @property
    def theory_terms(self) -> list[str]:
        return self.slot("theories")

    @property
    def branch_terms(self) -> list[str]:
        return unique_clean(self.mechanism_terms + self.context_terms + self.method_terms + self.theory_terms)

    @property
    def exclusion_terms(self) -> list[str]:
        return unique_clean(self.slot("exclusions") + self.exclude_rules)

    @property
    def enabled_databases(self) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        mapping = {
            "openalex": "openalex",
            "semantic scholar": "semantic_scholar",
            "semantic_scholar": "semantic_scholar",
            "semanticscholar": "semantic_scholar",
            "arxiv": "arxiv",
            "arxiv.org": "arxiv",
        }
        values = self.preferred_databases or ["openalex", "semantic_scholar", "arxiv"]
        for value in values:
            key = mapping.get(clean_text(value).lower())
            if key and key not in seen:
                seen.add(key)
                normalized.append(key)
        return normalized or ["openalex", "semantic_scholar", "arxiv"]

    @property
    def unsupported_databases(self) -> list[str]:
        supported = {"openalex", "semantic_scholar", "arxiv", "semantic scholar", "arxiv.org", "semanticscholar"}
        return [value for value in self.preferred_databases if clean_text(value).lower() not in supported]


def parse_seed_work(item: object) -> SeedWork:
    if isinstance(item, str):
        cleaned = clean_text(item)
        return SeedWork(label=cleaned, title=cleaned, doi=normalize_doi(cleaned))
    if not isinstance(item, dict):
        return SeedWork()
    return SeedWork(
        label=clean_text(item.get("label")),
        title=clean_text(item.get("title")),
        doi=normalize_doi(item.get("doi")),
        openalex_id=clean_text(item.get("openalex_id")),
        semantic_scholar_id=clean_text(item.get("semantic_scholar_id")),
        arxiv_id=clean_text(item.get("arxiv_id")),
    )


def find_topic_profile_path(
    workdir: Path,
    explicit_path: Path | None = None,
    reference_dir: Path | None = None,
) -> Path | None:
    if explicit_path:
        candidate = explicit_path.expanduser().resolve()
        if candidate.exists():
            return candidate
        raise RuntimeError(f"Topic profile not found: {candidate}")

    for env_var in PROFILE_ENV_VARS:
        env_value = os.environ.get(env_var)
        if env_value and Path(env_value).exists():
            return Path(env_value).resolve()

    for name in PROFILE_FILE_NAMES:
        candidate = workdir / name
        if candidate.exists():
            return candidate.resolve()

    if reference_dir:
        template_candidate = reference_dir / "topic_profile.template.json"
        if template_candidate.exists():
            return None
    return None


def load_topic_profile(
    workdir: Path,
    reference_dir: Path | None = None,
    explicit_path: Path | None = None,
) -> TopicProfile:
    path = find_topic_profile_path(workdir, explicit_path=explicit_path, reference_dir=reference_dir)
    if path is None:
        template_hint = ""
        if reference_dir:
            template_hint = f" Create `{workdir / 'topic_profile.json'}` from `{reference_dir / 'topic_profile.template.json'}` first."
        raise RuntimeError(f"Missing topic profile in `{workdir}`.{template_hint}")

    data = json.loads(path.read_text(encoding="utf-8"))
    keyword_slots = data.get("keyword_slots") or {}
    date_range = data.get("date_range") or {}
    seed_works = [parse_seed_work(item) for item in data.get("seed_works") or []]
    return TopicProfile(
        topic_name=clean_text(data.get("topic_name")),
        research_goal=clean_text(data.get("research_goal")),
        recent_start=int(date_range.get("recent_start")) if date_range.get("recent_start") is not None else None,
        recent_end=int(date_range.get("recent_end")) if date_range.get("recent_end") is not None else None,
        include_classics_before_recent=bool(date_range.get("include_classics_before_recent", True)),
        keyword_slots={key: unique_clean(list(value or [])) for key, value in keyword_slots.items()},
        seed_authors=unique_clean(list(data.get("seed_authors") or [])),
        seed_works=[seed for seed in seed_works if seed.best_label],
        preferred_databases=unique_clean(list(data.get("preferred_databases") or [])),
        include_rules=unique_clean(list(data.get("include_rules") or [])),
        priority_rules=unique_clean(list(data.get("priority_rules") or [])),
        exclude_rules=unique_clean(list(data.get("exclude_rules") or [])),
        source_path=path,
    )


def build_query_blocks(profile: TopicProfile, limit: int = 24) -> list[QueryBlock]:
    blocks: list[QueryBlock] = []
    seen: set[str] = set()

    def add(kind: str, query: str, label_hint: str) -> None:
        cleaned = clean_text(query)
        key = cleaned.lower()
        if not cleaned or key in seen:
            return
        seen.add(key)
        blocks.append(QueryBlock(label=f"{kind}_{slug_label(label_hint)}", query=cleaned, kind=kind))

    if profile.topic_name:
        add("topic", profile.topic_name, profile.topic_name)

    core_terms = profile.core_terms[:5]
    mechanism_terms = profile.mechanism_terms[:4]
    context_terms = profile.context_terms[:4]
    method_terms = profile.method_terms[:3]
    theory_terms = profile.theory_terms[:3]

    for term in core_terms:
        add("core", term, term)

    if len(core_terms) >= 2:
        add("core", " ".join(core_terms[:2]), "core_blend")

    branch_terms = mechanism_terms + context_terms + method_terms + theory_terms
    for core_term in (core_terms[:3] or [profile.topic_name]):
        for branch_term in branch_terms[:8]:
            add("branch", f"{core_term} {branch_term}", f"{core_term}_{branch_term}")

    for author in profile.seed_authors[:4]:
        basis = core_terms[0] if core_terms else profile.topic_name
        if basis:
            add("author", f"{author} {basis}", f"{author}_{basis}")

    return blocks[:limit]
