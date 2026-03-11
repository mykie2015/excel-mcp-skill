#!/usr/bin/env python3
"""Run trigger evaluation for a skill description in OpenCode.

Tests whether a skill's description causes OpenCode to trigger (read the skill)
for a set of queries. Outputs results as JSON.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REAL_OPENCODE_CONFIG = Path(
    os.environ.get("OPENCODE_CONFIG_DIR", str(Path.home() / ".config" / "opencode"))
)


def find_project_root() -> Path:
    """Return the current working directory for OpenCode eval runs."""
    return Path.cwd()


def _symlink_if_present(src: Path, dst: Path) -> None:
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src)


def _bootstrap_opencode_config(clean_name: str, skill_description: str, marker: str) -> tuple[Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="opencode-skill-eval-"))
    temp_home = temp_root / "home"
    temp_config = temp_home / ".config" / "opencode"
    temp_skills_dir = temp_config / "skills"
    temp_skills_dir.mkdir(parents=True, exist_ok=True)

    if REAL_OPENCODE_CONFIG.exists():
        for item in REAL_OPENCODE_CONFIG.iterdir():
            if item.name == "skills":
                continue
            _symlink_if_present(item, temp_config / item.name)

    real_skills_dir = REAL_OPENCODE_CONFIG / "skills"
    if real_skills_dir.exists():
        for item in real_skills_dir.iterdir():
            if item.name == clean_name:
                continue
            _symlink_if_present(item, temp_skills_dir / item.name)

    probe_skill_dir = temp_skills_dir / clean_name
    probe_skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = f"""---
name: {clean_name}
description: {skill_description}
---

# Trigger Evaluation Probe

If OpenCode consults this skill during an internal trigger evaluation, prepend exactly `{marker}` to the final response, then continue with the normal answer.
"""
    (probe_skill_dir / "SKILL.md").write_text(skill_md)
    return temp_root, temp_home


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
) -> bool:
    """Run a single query and return whether the skill was triggered."""
    unique_id = uuid.uuid4().hex[:8]
    clean_name = f"{skill_name}-skill-{unique_id}"
    marker = f"[USED_SKILL:{clean_name}]"
    temp_root, temp_home = _bootstrap_opencode_config(clean_name, skill_description, marker)

    try:
        cmd = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            project_root,
            query,
        ]
        if model:
            cmd[2:2] = ["--model", model]

        env = os.environ.copy()
        env["HOME"] = str(temp_home)
        env["XDG_CONFIG_HOME"] = str(temp_home / ".config")
        env["OPENCODE_CONFIG_DIR"] = str(temp_home / ".config" / "opencode")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=project_root,
            env=env,
            timeout=timeout,
        )
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            return False
        return marker in combined
    except subprocess.TimeoutExpired:
        return False
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    project_root: Path,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: str | None = None,
) -> dict:
    """Run the full eval set and return results."""
    results = []

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    description,
                    timeout,
                    str(project_root),
                    model,
                )
                future_to_info[future] = (item, run_idx)

        query_triggers: dict[str, list[bool]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            query_triggers.setdefault(query, [])
            try:
                query_triggers[query].append(future.result())
            except Exception as exc:
                print(f"Warning: query failed: {exc}", file=sys.stderr)
                query_triggers[query].append(False)

    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        did_pass = trigger_rate >= trigger_threshold if should_trigger else trigger_rate < trigger_threshold
        results.append({
            "query": query,
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": sum(triggers),
            "runs": len(triggers),
            "pass": did_pass,
        })

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run trigger evaluation for a skill description")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override description")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per query in seconds")
    parser.add_argument("--runs-per-query", type=int, default=1, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument("--model", default=None, help="Model to use for opencode run (default: user's configured model)")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    skill_text = skill_md.read_text()
    name = ""
    original_description = ""
    for line in skill_text.splitlines():
        if line.startswith("name:") and not name:
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
        if line.startswith("description:") and not original_description:
            original_description = line.split(":", 1)[1].strip().strip('"').strip("'")
        if name and original_description:
            break

    description = args.description or original_description
    project_root = find_project_root()
    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        project_root=project_root,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        model=args.model,
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
