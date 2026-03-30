from __future__ import annotations

import os
from pathlib import Path


WORKSPACE_MARKERS = (
    "topic_profile.json",
    "paper_finder_results.json",
    "paper_finder_source_log.json",
    "task_instructions",
)


def resolve_workspace_root(script_file: str, env_var: str = "PAPER_FINDER_WORKDIR") -> Path:
    env_candidates = [os.environ.get(env_var), os.environ.get("RESEARCH_PAPER_FINDER_WORKDIR")]
    candidates: list[Path] = []
    for env_value in env_candidates:
        if env_value:
            candidates.append(Path(env_value).resolve())
    candidates.append(Path.cwd().resolve())

    script_path = Path(script_file).resolve()
    candidates.extend(script_path.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if any((candidate / marker).exists() for marker in WORKSPACE_MARKERS):
            return candidate

    return Path.cwd().resolve()
