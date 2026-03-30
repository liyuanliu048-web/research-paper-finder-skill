# research-paper-finder

A Codex skill for building broad, traceable, deduplicated research-paper candidate sets from a topic description or a prepared `topic_profile.json`.

## What It Does

`research-paper-finder` helps Codex:

- turn a topic into structured search inputs
- search OpenAlex, Semantic Scholar, and arXiv
- expand coverage with seed authors and seed works
- follow citation links for broader recall
- deduplicate records by DOI and normalized title
- export JSON-first outputs for later screening or reading

## Repository Layout

```text
research-paper-finder/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── search_workflow.md
│   ├── topic_profile.template.json
│   └── topic_profile_contract.md
└── scripts/
    ├── _workspace.py
    ├── find_papers.py
    ├── paper_finder_common.py
    └── topic_profile.py
```

## Install

For local Codex use, place the skill under:

```text
~/.codex/skills/research-paper-finder
```

In this environment it is already installed at:

```text
C:\Users\c2060931\.codex\skills\research-paper-finder
```

After installation, restart Codex so the new skill is picked up.

## Core Input

The main input is `topic_profile.json`.

Start from [`references/topic_profile.template.json`](references/topic_profile.template.json) and fill:

- `topic_name`
- `research_goal`
- `date_range`
- `keyword_slots`
- `seed_authors`
- `seed_works`
- `preferred_databases`

Use [`references/topic_profile_contract.md`](references/topic_profile_contract.md) for the field rules.

## Run

Basic example:

```bash
python scripts/find_papers.py --workdir <target-dir>
```

Common options:

```bash
python scripts/find_papers.py \
  --workdir <target-dir> \
  --topic-profile <path-to-topic_profile.json> \
  --prefix paper_finder \
  --max-query-blocks 24
```

Useful flags:

- `--skip-citation-expansion` for a faster first pass
- `--json-only` to skip XLSX, RIS, and Markdown views
- `--prefix` to separate multiple runs in one folder

## Outputs

The script writes:

- `<prefix>_results.json`
- `<prefix>_source_log.json`
- `<prefix>_results.xlsx`
- `<prefix>_results.ris`
- `<prefix>_source_log.md`

`JSON` is the authoritative output. The others are derived views.

## Notes

- OpenAlex is the primary structured source.
- Semantic Scholar can hit rate limits during larger runs.
- arXiv is included mainly for preprint coverage.
- Specialist databases may still need manual follow-up.
- This skill builds a candidate pool, not a final inclusion list.

## Related Files

- Skill definition: [`SKILL.md`](SKILL.md)
- Workflow notes: [`references/search_workflow.md`](references/search_workflow.md)
- Main script: [`scripts/find_papers.py`](scripts/find_papers.py)
