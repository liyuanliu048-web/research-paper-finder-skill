# Topic Profile Contract

## Purpose

Use `topic_profile.json` as the shared input that describes what to search and how broadly to expand.
If the user gives only a natural-language topic, derive this JSON before running the search script.

## Construction Rules

- Fill `topic_name` with the shortest precise task label.
- Fill `research_goal` with one sentence describing what the search should surface.
- Fill `date_range` with the active year window and whether classic papers should be backfilled.
- Fill `keyword_slots.phenomenon` with the direct object of study.
- Fill `keyword_slots.synonyms` with lexical variants, abbreviations, and adjacent labels.
- Fill `keyword_slots.mechanisms` with process or explanatory terms.
- Fill `keyword_slots.contexts` with settings, populations, disciplines, media, or domain branches.
- Fill `keyword_slots.methods` with methods that matter for discovery or later screening.
- Fill `keyword_slots.theories` with theory labels, anchor authors, or canonical constructs.
- Fill `keyword_slots.exclusions` with obvious noise terms and false-friend meanings.
- Order entries by expected value. Earlier `phenomenon`, `synonyms`, `mechanisms`, and `methods` terms receive more query exposure under capped runs.
- For assay, probe, or reporter topics, place the assay chemistry or reporter format before generic biology labels.
- Fill `seed_authors` only with real authors who are genuinely central to the topic.
- Fill `seed_works` only with verified works. Prefer DOI, OpenAlex ID, Semantic Scholar ID, or arXiv ID when available.
- Fill `preferred_databases` with structured sources to search automatically. This skill supports `openalex`, `semantic_scholar`, and `arxiv`.

## Use Pattern

1. Create or update `topic_profile.json`.
2. Expand the keyword slots into query blocks.
3. Search structured APIs first.
4. Use seed works and citation chasing to widen coverage.
5. Persist JSON first and derive other views afterward.
6. Record any manual-only databases separately from the automated run.
