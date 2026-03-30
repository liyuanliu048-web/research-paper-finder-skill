---
name: research-paper-finder
description: Find, expand, and export research-paper candidate sets from a topic description or `topic_profile.json`. Use when Codex needs to search scholarly literature, turn topic keywords into API queries, harvest papers from OpenAlex, Semantic Scholar, and arXiv, follow seed authors or seed works, deduplicate records, and write JSON-first outputs for later screening or reading.
---

# Research Paper Finder

## Overview

Use this skill to turn a natural-language topic or a prepared `topic_profile.json` into a broad, traceable, deduplicated paper pool.
Keep structured outputs in JSON first, then derive Excel, RIS, or Markdown views from the same record set.

## Workflow

1. Build or update `topic_profile.json` from `references/topic_profile.template.json`.
2. Expand the topic into query blocks with core terms, branch terms, seed authors, and seed works.
3. Run `scripts/find_papers.py` to search OpenAlex, Semantic Scholar, and arXiv.
4. Use seed-based expansion and citation chasing to broaden coverage without inventing metadata.
5. Deduplicate by DOI first and normalized title plus year second.
6. Export JSON-first outputs and review the source log before recommending manual supplements.

## Build Topic Profile

- If the user only gives a topic in natural language, create `topic_profile.json` in the working directory before running the search script.
- Follow `references/topic_profile_contract.md` and start from `references/topic_profile.template.json`.
- Fill `phenomenon`, `synonyms`, `mechanisms`, `contexts`, `methods`, `theories`, and `exclusions` with real terms that matter for recall and noise control.
- Fill `seed_authors` and `seed_works` only with verified anchors. Never invent DOI, author names, years, or IDs.
- Prefer `seed_works` entries that already have DOI, OpenAlex ID, Semantic Scholar ID, or arXiv ID because they support reliable expansion.

## Run Search

- Default execution path:
  `python skill/research-paper-finder/scripts/find_papers.py --workdir <target-dir>`
- Use `--topic-profile <path>` when the profile JSON is outside the output directory.
- Use `--prefix <name>` to avoid collisions with other search runs in the same folder.
- Use `--max-query-blocks <n>` for a quick validation pass before a full harvest.
- Use `--skip-citation-expansion` when you need a faster first pass and can defer citation chasing.
- Expect rate limiting on some APIs, especially Semantic Scholar. Treat reruns and smaller validation passes as normal operating practice.

## Output Contract

The script writes these files into the target workdir:

- `<prefix>_results.json`
- `<prefix>_source_log.json`
- `<prefix>_results.xlsx`
- `<prefix>_results.ris`
- `<prefix>_source_log.md`

JSON is the authoritative output. Excel, RIS, and Markdown are convenience views.
Preserve title, authors, year, venue, DOI, abstract, URL, structured IDs, database sources, relevance notes, and source tracking.

## Coverage Rules

- Prefer structured APIs first: OpenAlex, Semantic Scholar Graph API, and arXiv.
- Use broad topic and branch queries first, then add seed-based forward citations, recent backward references, and seed-author tracking.
- Keep the candidate pool broad enough for later screening. Do not treat this stage as final inclusion screening.
- Deduplicate conservatively. Merge provenance instead of dropping source history.
- Record unsupported or manual-only databases separately instead of pretending they were queried automatically.

## Manual Supplement

- If the topic depends on Web of Science, Scopus, Google Scholar, PubMed, IEEE Xplore, ACM DL, or a field-specific index, keep the automated API run as the baseline and add manual search notes separately.
- Do not claim coverage for sources that were not actually queried.
- Do not use weak title matches to fabricate DOI, citation links, or fulltext access.

## References

- `references/topic_profile.template.json`
- `references/topic_profile_contract.md`
- `references/search_workflow.md`
- `scripts/find_papers.py`

## Stop Condition

Stop after you have produced a traceable candidate paper pool, summarized coverage limits, and identified the most useful next step for screening or reading.
