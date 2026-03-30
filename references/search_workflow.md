# Research Paper Search Workflow

## Inputs

- `topic_profile.json` in the target workdir, or a profile passed with `--topic-profile`
- Optional seed authors and seed works with verified identifiers
- Optional preferred database list

## Query Design

1. Treat `keyword_slots.phenomenon` and `keyword_slots.synonyms` as the recall core.
2. Treat `mechanisms`, `contexts`, `methods`, and `theories` as branch-expansion terms.
3. Order terms by expected precision because capped runs give the earliest terms the most exposure.
4. Build mixed query blocks by combining a small number of core and branch terms.
5. For assay-heavy topics, put reporter, substrate, or measurement terms before generic biology labels.
6. Add author blocks from `seed_authors`.
7. Use `seed_works` for exact anchor recovery and citation expansion.

## Search Sources

- OpenAlex for broad metadata, citation links, and DOI-centric normalization
- Semantic Scholar Graph API for complementary paper discovery and open-access links
- arXiv API for preprint coverage

## Expansion Strategy

1. Run broad topic and branch queries first.
2. If the first pass is noisy, build a second focused profile and rerun with tighter method or reporter terms.
3. Recover each verified seed work through OpenAlex and, when possible, Semantic Scholar.
4. Add forward citations from seed works.
5. Pull backward references only from verified seed works by default.
6. Opt in to auto-selected backward-reference seeds only when you explicitly want more recall and accept more drift.
7. Keep source tracking for every addition.

## Deduplication Rules

- Use DOI as the primary deduplication key.
- Fall back to normalized title plus year when DOI is missing.
- Merge provenance, database sources, and structured identifiers instead of dropping them.
- Prefer the more complete record when multiple sources disagree.

## Outputs

- `<prefix>_results.json` as the authoritative structured dataset
- `<prefix>_source_log.json` as the audit trail
- `<prefix>_results.xlsx`, `<prefix>_results.ris`, and `<prefix>_source_log.md` as derived views

## Limits

- Structured APIs do not cover every database or every document type.
- Abstract coverage can be incomplete.
- Rate limiting can interrupt some calls, especially on Semantic Scholar.
- arXiv can return genuine zero-hit runs for narrow assay queries, so distinguish zero results from transport failures.
- Some domains still need manual supplements from specialist indexes.
- This workflow is for candidate harvesting, not final inclusion decisions.
