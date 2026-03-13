#!/usr/bin/env python3
"""
ralph_orchestrator.py — Lightweight Ralph Loop Orchestrator

Multi-worker orchestrator (Codex Docker or OpenCode agent API) that drives
PRD → TODO → parallel workers → validation gates → integration → PR review → RUN_RESULT.md.

Runs are organized under runs/<initiative>/<run-id>/ for tracking across related efforts.

Usage:
  python ralph_orchestrator.py init   --repo PATH --prd PATH --initiative NAME [--agent codex|opencode]
  python ralph_orchestrator.py plan   [--run-dir PATH]
  python ralph_orchestrator.py run    [--resume] [--run-dir PATH]
  python ralph_orchestrator.py review [--run-dir PATH]
  python ralph_orchestrator.py report [--run-dir PATH]
  python ralph_orchestrator.py status [--run-dir PATH]
  python ralph_orchestrator.py runs   [--initiative NAME]
  python ralph_orchestrator.py dashboard [--port PORT]
  python ralph_orchestrator.py full   --repo PATH --prd PATH --initiative NAME [--agent codex|opencode]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
RALPH_SH = SCRIPT_DIR / "ralph.sh"
ASSETS_DIR = SCRIPT_DIR.parent / "assets"


def _load_dotenv(repo_dir: Path | None = None) -> None:
    """Load .env from repo root or cwd into os.environ (won't overwrite)."""
    candidates = []
    if repo_dir:
        candidates.append(repo_dir / ".env")
    candidates.append(Path.cwd() / ".env")
    for env_path in candidates:
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
            break


# ── Prerequisite checks ─────────────────────────────────────────────────────


def check_prerequisites(agent: str = "codex") -> None:
    missing = []
    for cmd in ["git", "gh", "python3"]:
        if not shutil.which(cmd):
            missing.append(cmd)
    if agent == "codex":
        if not shutil.which("codex"):
            missing.append("codex")
        if not shutil.which("docker"):
            missing.append("docker")
    elif agent == "opencode":
        if not shutil.which("opencode"):
            missing.append("opencode")
    if missing:
        print(f"Error: Missing required tools: {', '.join(missing)}")
        print("Install them before running the ralph loop.")
        sys.exit(1)


# ── Central log + utility helpers ────────────────────────────────────────────

_central_log: Path | None = None


def _init_central_log(ralph_dir: Path) -> None:
    global _central_log
    log_dir = ralph_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _central_log = log_dir / "ralph.log"


def log(msg: str) -> None:
    """Print to stdout AND append to the central ralph.log."""
    line = f"[{ts_local()}] {msg}"
    print(line, flush=True)
    if _central_log:
        with open(_central_log, "a") as f:
            f.write(line + "\n")


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit_event(ralph_dir: Path, event: dict[str, Any]) -> None:
    event.setdefault("timestamp", ts())
    with open(ralph_dir / "events.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")
    etype = event.get("type", "?")
    summary = {k: v for k, v in event.items() if k not in ("timestamp", "type")}
    log(f"EVENT {etype}: {json.dumps(summary, default=str)}")


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_profile(repo_dir: Path, profile_path: Path | None = None) -> dict:
    if profile_path and profile_path.exists():
        return json.loads(profile_path.read_text())

    default = repo_dir / "repo_profile.json"
    if default.exists():
        return json.loads(default.read_text())

    return auto_detect_profile(repo_dir)


def auto_detect_profile(repo_dir: Path) -> dict:
    profile: dict[str, Any] = {"language": "unknown"}

    if (repo_dir / "pyproject.toml").exists() or (repo_dir / "requirements.txt").exists():
        profile.update(language="python", test_cmd="pytest", lint_cmd="ruff check .", typecheck_cmd="mypy .")
    elif (repo_dir / "package.json").exists():
        profile.update(language="javascript", test_cmd="npm test", lint_cmd="npx eslint .")
    elif (repo_dir / "Cargo.toml").exists():
        profile.update(language="rust", test_cmd="cargo test", lint_cmd="cargo clippy")
    elif (repo_dir / "go.mod").exists():
        profile.update(language="go", test_cmd="go test ./...", lint_cmd="golangci-lint run")

    profile.setdefault("test_cmd", "echo 'no test command configured'")
    profile.setdefault("lint_cmd", "echo 'no lint command configured'")
    profile.setdefault("typecheck_cmd", None)
    profile.setdefault("build_cmd", None)
    profile.setdefault("docker_image", "codex-worker:latest")
    profile.setdefault("codex_flags", "--full-auto")
    return profile


# ── TODO.md parsing ──────────────────────────────────────────────────────────


def parse_todo(todo_path: Path) -> list[dict]:
    if not todo_path.exists():
        return []
    tasks = []
    for i, line in enumerate(todo_path.read_text().splitlines()):
        m = re.match(r"^- \[([ x])\] (.+)$", line)
        if m:
            tasks.append({
                "index": i,
                "done": m.group(1) == "x",
                "description": m.group(2).strip(),
                "line": line,
            })
    return tasks


def remaining_tasks(todo_path: Path) -> list[dict]:
    return [t for t in parse_todo(todo_path) if not t["done"]]


def mark_task_done(todo_path: Path, task_index: int) -> None:
    lines = todo_path.read_text().splitlines()
    lines[task_index] = re.sub(r"^- \[ \]", "- [x]", lines[task_index])
    todo_path.write_text("\n".join(lines) + "\n")


# ── Git helpers ──────────────────────────────────────────────────────────────


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, check=check)


def gh(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh"] + args, cwd=cwd, capture_output=True, text=True, check=check)


def default_branch(repo_dir: Path) -> str:
    r = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    return r.stdout.strip()


def create_worktree(repo_dir: Path, worktree_path: Path, branch: str, base: str) -> None:
    if worktree_path.exists():
        shutil.rmtree(worktree_path)
    r = git(["worktree", "add", "-b", branch, str(worktree_path), base], cwd=repo_dir, check=False)
    if r.returncode != 0:
        print(f"  git worktree error: {r.stderr.strip()}")
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)


def reset_worktree(worktree_path: Path, branch: str) -> None:
    """Reset a worktree to the latest state of its branch."""
    git(["checkout", branch], cwd=worktree_path, check=False)
    git(["reset", "--hard", branch], cwd=worktree_path, check=False)
    git(["clean", "-fd"], cwd=worktree_path, check=False)


def _commit_worktree(worktree_path: Path, worker_id: str, task_desc: str) -> None:
    """Commit any staged or untracked files on the worker branch after gates pass."""
    git(["add", "-A"], cwd=worktree_path, check=False)
    slug = task_desc[:60].replace('"', "'")
    r = git(
        ["commit", "-m", f"[{worker_id}] {slug}"],
        cwd=worktree_path, check=False,
    )
    if r.returncode == 0:
        log(f"[{worker_id}] committed to branch")
    else:
        log(f"[{worker_id}] nothing to commit (exit {r.returncode})")


def merge_worktree(repo_dir: Path, integration_branch: str, worker_branch: str) -> bool:
    git(["checkout", integration_branch], cwd=repo_dir)
    r = git(["merge", "--no-ff", "-m", f"merge {worker_branch}", worker_branch], cwd=repo_dir, check=False)
    if r.returncode != 0:
        git(["merge", "--abort"], cwd=repo_dir, check=False)
        return False
    return True


def diff_stats(repo_dir: Path, base: str) -> dict:
    r = git(["diff", "--stat", base + "...HEAD"], cwd=repo_dir, check=False)
    lines_added = 0
    lines_deleted = 0
    files_changed = 0
    for line in r.stdout.splitlines():
        m = re.search(r"(\d+) insertions?\(\+\)", line)
        if m:
            lines_added = int(m.group(1))
        m = re.search(r"(\d+) deletions?\(-\)", line)
        if m:
            lines_deleted = int(m.group(1))
        m = re.search(r"(\d+) files? changed", line)
        if m:
            files_changed = int(m.group(1))
    return {"files_changed": files_changed, "lines_added": lines_added, "lines_deleted": lines_deleted}


# ── Validation gates ─────────────────────────────────────────────────────────


def run_gate(cmd: str | None, label: str, cwd: Path, ralph_dir: Path) -> dict:
    if not cmd:
        return {"gate": label, "status": "skip", "duration_s": 0}

    start = time.time()
    log_path = ralph_dir / "logs" / f"gate-{label}-{int(start)}.log"
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    duration = round(time.time() - start, 2)

    log_path.write_text(f"$ {cmd}\nExit: {r.returncode}\n\n{r.stdout}\n{r.stderr}")

    result = {
        "gate": label,
        "command": cmd,
        "status": "pass" if r.returncode == 0 else "fail",
        "exit_code": r.returncode,
        "duration_s": duration,
        "log": str(log_path),
    }
    emit_event(ralph_dir, {"type": "gate", **result})
    return result


def run_focused_gates(profile: dict, cwd: Path, ralph_dir: Path) -> list[dict]:
    """Run lightweight gates against a single worktree (per-worker validation)."""
    gates = [
        ("focused_test", profile.get("test_focused_cmd", profile.get("test_cmd"))),
        ("lint", profile.get("lint_cmd")),
    ]
    results = []
    for label, cmd in gates:
        result = run_gate(cmd, label, cwd, ralph_dir)
        results.append(result)
        if result["status"] == "fail":
            break
    return results


def run_all_gates(profile: dict, cwd: Path, ralph_dir: Path) -> list[dict]:
    """Run full gate suite against repo (post-merge validation)."""
    gates = [
        ("test", profile.get("test_cmd")),
        ("lint", profile.get("lint_cmd")),
        ("typecheck", profile.get("typecheck_cmd")),
        ("build", profile.get("build_cmd")),
    ]
    results = []
    for label, cmd in gates:
        result = run_gate(cmd, label, cwd, ralph_dir)
        results.append(result)
        log(f"  [{label}] {result['status']} ({result['duration_s']}s)")
        if result["status"] == "fail" and label in ("test", "lint"):
            break
    return results


# ── Agent execution backends ─────────────────────────────────────────────────


def _exec_codex(prompt: str, worktree_path: Path, profile: dict, ralph_dir: Path | None = None) -> subprocess.CompletedProcess:
    docker_image = profile.get("docker_image")
    if docker_image:
        return _exec_codex_docker(prompt, worktree_path, profile, docker_image, ralph_dir=ralph_dir)
    env = {**os.environ, "RALPH_PROJECT_DIR": str(worktree_path)}
    codex_flags = profile.get("codex_flags", "--full-auto")
    return subprocess.run(
        ["codex", "exec", codex_flags, prompt],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def _exec_codex_docker(
    prompt: str, worktree_path: Path, profile: dict, docker_image: str,
    ralph_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    env_flags: list[str] = []
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "GITHUB_TOKEN"):
        val = os.environ.get(key, "")
        if val:
            env_flags.extend(["-e", f"{key}={val}"])

    prompt_escaped = prompt.replace("\\", "\\\\").replace("'", "'\\''")

    pip_deps = profile.get("pip_deps", [])
    pip_cmd = f"pip install -q {' '.join(pip_deps)} 2>/dev/null;" if pip_deps else ""

    login_cmd = (
        'if [ -n "$OPENAI_API_KEY" ]; then '
        'echo "$OPENAI_API_KEY" | codex login --with-api-key 2>/dev/null || true; '
        "fi"
    )
    exec_cmd = f"codex exec --sandbox danger-full-access '{prompt_escaped}'"

    # Resolve the main repo .git dir from the worktree's .git file
    volume_flags = ["-v", f"{worktree_path}:/workspace"]
    git_ref = worktree_path / ".git"
    repo_git_dir = None
    if git_ref.is_file():
        ref_line = git_ref.read_text().strip()
        if ref_line.startswith("gitdir:"):
            wt_gitdir = Path(ref_line.split(":", 1)[1].strip())
            repo_git_dir = wt_gitdir.parent.parent  # .git/worktrees/<name> -> .git
            volume_flags.extend(["-v", f"{repo_git_dir}:{repo_git_dir}"])

    if ralph_dir and ralph_dir.is_dir():
        volume_flags.extend(["-v", f"{ralph_dir}:/session:ro"])

    # Re-create the .git worktree reference inside the container
    fix_git_cmd = ""
    if repo_git_dir and git_ref.is_file():
        ref_content = git_ref.read_text().strip()
        fix_git_cmd = f"echo '{ref_content}' > /workspace/.git;"

    cmd = [
        "docker", "run", "--rm",
        *volume_flags,
        "-w", "/workspace",
        *env_flags,
        "--entrypoint", "/bin/bash",
        docker_image,
        "-c", f"{pip_cmd} {login_cmd}; {fix_git_cmd} {exec_cmd}",
    ]

    log(f"  [docker] image={docker_image} worktree={worktree_path.name}")
    return _run_streaming(cmd, worktree_path.name, timeout=900)


def _run_streaming(
    cmd: list[str], label: str, timeout: int = 900,
) -> subprocess.CompletedProcess:
    """Run a command, streaming its stdout/stderr to the central log in real-time."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            stdout_lines.append(raw_line)
            if any(kw in line for kw in (
                "thinking", "exec", "file ", "apply_patch", "TASK_COMPLETE",
                "Error", "error", "SUCCESS", "failed", "mkdir", "Created",
            )):
                log(f"  [{label}] {line[:120]}")
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return subprocess.CompletedProcess(
        cmd, proc.returncode or 0,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def _exec_opencode(prompt: str, worktree_path: Path, profile: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "RALPH_PROJECT_DIR": str(worktree_path)}
    model = profile.get("opencode_model")
    if model:
        env["OPENCODE_MODEL"] = model
    return subprocess.run(
        ["opencode", "run", prompt],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def exec_agent(prompt: str, worktree_path: Path, profile: dict, ralph_dir: Path | None = None) -> subprocess.CompletedProcess:
    agent = profile.get("agent", "codex")
    if agent == "opencode":
        return _exec_opencode(prompt, worktree_path, profile)
    return _exec_codex(prompt, worktree_path, profile, ralph_dir=ralph_dir)


# ── Reference context for worker prompts ─────────────────────────────────


def _build_reference_context(ralph_dir: Path, profile: dict) -> str:
    """Build extra context to inject into worker prompts from reference_dirs in loop_state."""
    state = load_json(ralph_dir / "loop_state.json")
    ref_dirs = state.get("reference_dirs", [])
    if not ref_dirs:
        return ""

    lines = ["\n            REFERENCE CONTEXT (follow these patterns):"]
    for ref_dir in ref_dirs:
        ref_path = Path(ref_dir)
        skill_md = ref_path / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text()
            lines.append(f"            --- From {ref_path.name}/SKILL.md ---")
            for section_line in content.splitlines()[:80]:
                lines.append(f"            {section_line}")
            lines.append(f"            --- End {ref_path.name}/SKILL.md ---")
    return "\n".join(lines) + "\n"


# ── Worker execution ─────────────────────────────────────────────────────────


def run_worker(
    worker_id: str,
    task: dict,
    worktree_path: Path,
    ralph_dir: Path,
    profile: dict,
    max_retries: int = 3,
) -> dict:
    task_desc = task["description"]
    slug = re.sub(r"[^a-z0-9]+", "-", task_desc.lower())[:40].rstrip("-")
    log_dir = ralph_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    workers_path = ralph_dir / "workers.json"
    workers = load_json(workers_path)
    agent_name = profile.get("agent", "codex")
    workers.setdefault(worker_id, {})
    workers[worker_id].update({
        "task": task_desc,
        "agent": agent_name,
        "status": "running",
        "heartbeat": ts(),
        "started": ts(),
        "tokens_used": 0,
    })
    save_json(workers_path, workers)

    start = time.time()
    success = False
    r = None

    for attempt in range(1, max_retries + 1):
        log_file = log_dir / f"{worker_id}-{slug}-attempt{attempt}.log"
        log(f"[{worker_id}] ({agent_name}) Attempt {attempt}/{max_retries}: {task_desc[:60]}")

        ref_context = _build_reference_context(ralph_dir, profile)
        prompt = textwrap.dedent(f"""\
            You are worker {worker_id} in a Ralph Loop.

            YOUR TASK (do exactly this, nothing else):
            {task_desc}
{ref_context}
            RULES:
            - Read relevant files before modifying them
            - Implement the change
            - Run validation if specified in the task
            - Stage only the files you changed (git add <specific files>)
            - Do NOT commit
            - Do NOT use git add -A or git add .
            - Output TASK_COMPLETE when done
        """)

        r = exec_agent(prompt, worktree_path, profile, ralph_dir=ralph_dir)

        log_file.write_text(f"$ {agent_name} | worker={worker_id}\nExit: {r.returncode}\n\n{r.stdout}\n{r.stderr}")

        workers[worker_id]["heartbeat"] = ts()
        save_json(workers_path, workers)

        if r.returncode == 0:
            success = True
            break

    duration = round(time.time() - start, 2)

    tokens = 0
    if r:
        for line in (r.stdout + r.stderr).splitlines():
            m = re.search(r"tokens?[_\s]*used[:\s]*(\d+)", line, re.IGNORECASE)
            if m:
                tokens = int(m.group(1))

    workers[worker_id].update(
        status="done" if success else "failed",
        heartbeat=ts(),
        duration_s=duration,
        tokens_used=tokens,
        attempts=attempt,
    )
    save_json(workers_path, workers)

    emit_event(ralph_dir, {
        "type": "worker_done",
        "worker": worker_id,
        "agent": agent_name,
        "task": task_desc,
        "success": success,
        "duration_s": duration,
        "attempts": attempt,
        "tokens": tokens,
    })

    return {
        "worker": worker_id,
        "task": task,
        "success": success,
        "duration_s": duration,
        "attempts": attempt,
        "tokens": tokens,
    }


# ── Subcommands ──────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    repo_dir = Path(args.repo).resolve()
    prd_path = Path(args.prd).resolve()
    initiative = args.initiative
    agent = getattr(args, "agent", "codex") or "codex"

    check_prerequisites(agent)

    if not repo_dir.is_dir():
        print(f"Error: {repo_dir} is not a directory")
        sys.exit(1)
    if not prd_path.exists():
        print(f"Error: PRD not found at {prd_path}")
        sys.exit(1)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    runs_root = repo_dir / "runs"
    ralph_dir = runs_root / initiative / run_id
    ralph_dir.mkdir(parents=True, exist_ok=True)
    (ralph_dir / "logs").mkdir(exist_ok=True)

    _update_initiative_index(runs_root, initiative, run_id)

    base = args.base or default_branch(repo_dir)
    integration_branch = f"ralph/{initiative}/{run_id}"
    git(["checkout", "-b", integration_branch], cwd=repo_dir)

    shutil.copy2(prd_path, ralph_dir / "PRD.md")

    profile = load_profile(repo_dir, Path(args.profile) if args.profile else None)
    agent = getattr(args, "agent", None) or profile.get("agent", "codex")
    profile["agent"] = agent

    worker_count = args.workers
    import tempfile
    worktrees_base = Path(tempfile.mkdtemp(prefix=f"ralph-wt-{run_id}-"))
    worktrees_dir = worktrees_base
    (ralph_dir / "worktrees_path.txt").write_text(str(worktrees_dir))

    worker_prefix = "oc" if agent == "opencode" else "codex"
    worker_ids = [f"{worker_prefix}-{chr(ord('a') + i)}" for i in range(worker_count)]
    workers: dict[str, Any] = {}
    for wid in worker_ids:
        wt_path = worktrees_dir / wid
        wb = f"{integration_branch}-{wid}"
        create_worktree(repo_dir, wt_path, wb, integration_branch)
        workers[wid] = {
            "worktree": str(wt_path),
            "branch": wb,
            "agent": agent,
            "status": "idle",
            "task": None,
            "heartbeat": ts(),
        }

    state = {
        "run_id": run_id,
        "initiative": initiative,
        "repo": str(repo_dir),
        "prd": str(prd_path),
        "base_branch": base,
        "integration_branch": integration_branch,
        "agent": agent,
        "worker_count": worker_count,
        "max_rounds": args.max_rounds,
        "max_retries": args.max_retries,
        "status": "initialized",
        "round": 0,
        "created": ts(),
        "profile": profile,
    }

    save_json(ralph_dir / "loop_state.json", state)
    save_json(ralph_dir / "workers.json", workers)
    (ralph_dir / "events.jsonl").touch()
    (ralph_dir / "rounds.jsonl").touch()

    emit_event(ralph_dir, {"type": "init", "run_id": run_id, "initiative": initiative, "agent": agent, "workers": worker_ids})

    print(f"\n{'=' * 60}")
    print(f" Ralph Loop Initialized")
    print(f"{'=' * 60}")
    print(f"  Initiative:   {initiative}")
    print(f"  Run ID:       {run_id}")
    print(f"  Agent:        {agent}")
    print(f"  Repo:         {repo_dir}")
    print(f"  Branch:       {integration_branch}")
    print(f"  Workers:      {', '.join(worker_ids)}")
    print(f"  Max rounds:   {args.max_rounds}")
    print(f"  Profile:      {profile.get('language', 'auto')}")
    print(f"  Run dir:      {ralph_dir}")
    print(f"{'=' * 60}")
    print(f"\nNext: python {__file__} plan --run-dir {ralph_dir}")


def _update_initiative_index(runs_root: Path, initiative: str, run_id: str) -> None:
    index_path = runs_root / "index.json"
    index = load_json(index_path) if index_path.exists() else {"initiatives": {}}
    if initiative not in index["initiatives"]:
        index["initiatives"][initiative] = {"runs": [], "created": ts()}
    index["initiatives"][initiative]["runs"].append({"run_id": run_id, "created": ts()})
    save_json(index_path, index)


def cmd_runs(args: argparse.Namespace) -> None:
    run_dir = Path(getattr(args, "run_dir", None) or ".").resolve()
    runs_root = run_dir / "runs"
    if not runs_root.exists():
        runs_root = run_dir
        if not (runs_root / "index.json").exists():
            print("No runs found. Run 'init' first.")
            return

    index_path = runs_root / "index.json"
    if not index_path.exists():
        print("No runs index found.")
        return

    index = load_json(index_path)
    target = getattr(args, "initiative", None)

    print(f"\n{'=' * 60}")
    print(f" Ralph Loop Runs")
    print(f"{'=' * 60}")

    for name, data in index.get("initiatives", {}).items():
        if target and name != target:
            continue
        print(f"\n  [{name}] ({len(data['runs'])} runs)")
        for run in data["runs"]:
            state_path = runs_root / name / run["run_id"] / "loop_state.json"
            status = "?"
            if state_path.exists():
                status = load_json(state_path).get("status", "?")
            print(f"    {run['run_id']}  [{status}]  {run.get('created', '')}")

    print(f"\n{'=' * 60}")


def cmd_plan(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    prd_path = ralph_dir / "PRD.md"
    if not prd_path.exists():
        prd_path = repo_dir / "PRD.md"

    print(f"\n── Planning: PRD → TODO.md ──")

    todo_path = ralph_dir / "TODO.md"

    prompt = textwrap.dedent(f"""\
        Read the PRD at {prd_path} and generate a TODO.md file.

        Requirements:
        - Use markdown checklist format: - [ ] Task description
        - Each task must be a single, testable unit of work
        - Include target file paths in each task description
        - Order tasks by dependency (independent tasks first)
        - Group related tasks under ## headings
        - Keep tasks specific enough that a worker knows exactly what to do
        - Do NOT include vague tasks like "review code" or "clean up"

        Write the output to {todo_path}
    """)

    profile = state.get("profile", {})
    exec_agent(prompt, repo_dir, profile)

    if not todo_path.exists():
        todo_path_repo = repo_dir / "TODO.md"
        if todo_path_repo.exists():
            shutil.copy2(todo_path_repo, todo_path)
        else:
            print("Error: Agent did not produce TODO.md")
            sys.exit(1)

    tasks = parse_todo(todo_path)
    total = len(tasks)
    done = sum(1 for t in tasks if t["done"])

    state["status"] = "planned"
    save_json(ralph_dir / "loop_state.json", state)
    emit_event(ralph_dir, {"type": "plan_complete", "total_tasks": total})

    print(f"  TODO.md: {total} tasks ({done} done, {total - done} remaining)")
    print(f"\n  Review TODO.md before proceeding:")
    print(f"    cat {todo_path}")
    print(f"\n  Then run: python {__file__} run --run-dir {ralph_dir}")


def cmd_run(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    _load_dotenv(repo_dir)
    todo_path = ralph_dir / "TODO.md"
    if not todo_path.exists():
        todo_path = repo_dir / "TODO.md"
    profile = state.get("profile", {})
    max_rounds = state["max_rounds"]
    max_retries = state.get("max_retries", 3)
    workers_data = load_json(ralph_dir / "workers.json")
    worker_ids = list(workers_data.keys())

    _init_central_log(ralph_dir)
    run_start = time.time()

    log(f"{'=' * 60}")
    log(f" Ralph Loop — Running")
    log(f"{'=' * 60}")

    for round_num in range(state.get("round", 0) + 1, max_rounds + 1):
        tasks = remaining_tasks(todo_path)
        if not tasks:
            log(f"All tasks complete.")
            break

        state["round"] = round_num
        state["status"] = "running"
        save_json(ralph_dir / "loop_state.json", state)

        log(f"── Round {round_num}/{max_rounds} — {len(tasks)} tasks remaining ──")

        for wid in worker_ids:
            wt_path = Path(workers_data[wid]["worktree"])
            wb = workers_data[wid].get("branch", "")
            if wt_path.exists() and wb:
                reset_worktree(wt_path, wb)

        batch = tasks[: len(worker_ids)]
        round_start = time.time()
        round_results = []

        with ThreadPoolExecutor(max_workers=len(worker_ids)) as pool:
            futures = {}
            for i, task in enumerate(batch):
                wid = worker_ids[i % len(worker_ids)]
                wt_path = Path(workers_data[wid]["worktree"])
                future = pool.submit(run_worker, wid, task, wt_path, ralph_dir, profile, max_retries)
                futures[future] = (wid, task)

            for future in as_completed(futures):
                wid, task = futures[future]
                try:
                    result = future.result()
                    round_results.append(result)
                    if result["success"]:
                        wt_path = Path(workers_data[wid]["worktree"])
                        focused = run_focused_gates(profile, wt_path, ralph_dir)
                        gates_ok = all(g["status"] != "fail" for g in focused)
                        if gates_ok:
                            _commit_worktree(wt_path, wid, task["description"])
                            mark_task_done(todo_path, task["index"])
                            log(f"[{wid}] PASS — {task['description'][:50]}")
                        else:
                            result["success"] = False
                            failed_gates = [g["gate"] for g in focused if g["status"] == "fail"]
                            log(f"[{wid}] GATE FAIL ({', '.join(failed_gates)}) — {task['description'][:50]}")
                    else:
                        log(f"[{wid}] FAIL — {task['description'][:50]}")
                except Exception as e:
                    log(f"[{wid}] ERROR — {e}")

        passed = sum(1 for r in round_results if r["success"])
        failed = len(round_results) - passed

        for result in round_results:
            if result["success"]:
                wb = workers_data[result["worker"]].get("branch", "")
                if wb:
                    ok = merge_worktree(repo_dir, state["integration_branch"], wb)
                    if not ok:
                        emit_event(ralph_dir, {
                            "type": "merge_conflict",
                            "worker": result["worker"],
                            "branch": wb,
                        })
                        log(f"CONFLICT merging {wb}")

        log(f"Post-merge validation gates:")
        gate_results = run_all_gates(profile, repo_dir, ralph_dir)

        round_duration = round(time.time() - round_start, 2)
        round_summary = {
            "round": round_num,
            "tasks_attempted": len(batch),
            "passed": passed,
            "failed": failed,
            "gates": {g["gate"]: g["status"] for g in gate_results},
            "duration_s": round_duration,
            "timestamp": ts(),
        }
        with open(ralph_dir / "rounds.jsonl", "a") as f:
            f.write(json.dumps(round_summary) + "\n")

        log(f"Round {round_num}: {passed} passed, {failed} failed ({round_duration}s)")

    total_duration = round(time.time() - run_start, 2)
    all_tasks = parse_todo(todo_path)
    done_count = sum(1 for t in all_tasks if t["done"])
    total_count = len(all_tasks)

    state["status"] = "completed" if done_count == total_count else "partial"
    state["total_duration_s"] = total_duration
    save_json(ralph_dir / "loop_state.json", state)

    log(f"{'=' * 60}")
    log(f" Ralph Loop — Done")
    log(f"{'=' * 60}")
    log(f"  Tasks: {done_count}/{total_count} completed")
    log(f"  Duration: {total_duration}s ({total_duration // 60:.0f}m {total_duration % 60:.0f}s)")
    log(f"  Next: python {__file__} review")


def cmd_review(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    integration_branch = state["integration_branch"]
    base = state["base_branch"]

    print(f"\n── PR + Review ──")

    git(["checkout", integration_branch], cwd=repo_dir)
    git(["push", "-u", "origin", integration_branch], cwd=repo_dir)

    existing_pr = gh(["pr", "view", "--json", "url"], cwd=repo_dir, check=False)
    if existing_pr.returncode == 0:
        pr_url = json.loads(existing_pr.stdout).get("url", "")
        print(f"  PR exists: {pr_url}")
    else:
        prd_summary = ""
        prd_path = repo_dir / "PRD.md"
        if prd_path.exists():
            lines = prd_path.read_text().splitlines()
            prd_summary = "\n".join(lines[:20])

        result = gh([
            "pr", "create",
            "--title", f"Ralph Loop: {state['run_id']}",
            "--body", f"## Ralph Loop Run\n\n{prd_summary}\n\n---\nGenerated by lightweight-ralph-loop",
            "--base", base,
        ], cwd=repo_dir)
        pr_url = result.stdout.strip()
        print(f"  PR created: {pr_url}")

    state["pr_url"] = pr_url
    save_json(ralph_dir / "loop_state.json", state)

    pr_info = gh(["pr", "view", "--json", "number,url"], cwd=repo_dir, check=False)
    pr_number = None
    if pr_info.returncode == 0:
        pr_json = json.loads(pr_info.stdout)
        pr_number = pr_json.get("number")

    repo_nwo_result = gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=repo_dir, check=False)
    repo_nwo = repo_nwo_result.stdout.strip() if repo_nwo_result.returncode == 0 else ""

    max_review_rounds = 3
    for review_round in range(1, max_review_rounds + 1):
        print(f"\n  Review round {review_round}/{max_review_rounds}")

        checks = gh(["pr", "checks", "--json", "name,state"], cwd=repo_dir, check=False)
        if checks.returncode == 0:
            check_data = json.loads(checks.stdout)
            failed_checks = [c for c in check_data if c.get("state") != "SUCCESS"]
            if failed_checks:
                print(f"  {len(failed_checks)} checks failing")
            else:
                print(f"  All checks passing")

        if pr_number and repo_nwo:
            comments_result = gh(
                ["api", f"repos/{repo_nwo}/pulls/{pr_number}/comments"],
                cwd=repo_dir, check=False,
            )
            if comments_result.returncode == 0:
                comment_data = json.loads(comments_result.stdout)
                blocking = [c for c in comment_data if "blocking" in c.get("body", "").lower()]
                if blocking:
                    print(f"  {len(blocking)} blocking comments found")
                    todo_path = ralph_dir / "TODO.md"
                    with open(todo_path, "a") as f:
                        f.write("\n## Review Findings\n\n")
                        for c in blocking:
                            f.write(f"- [ ] {c.get('body', '')[:200]}\n")
                elif comment_data:
                    print(f"  {len(comment_data)} comments (none blocking)")

        print(f"  Review round {review_round} complete")

        emit_event(ralph_dir, {"type": "review_round", "round": review_round, "pr_number": pr_number})

    state["status"] = "reviewed"
    save_json(ralph_dir / "loop_state.json", state)
    print(f"\n  PR ready for human review: {pr_url}")
    print(f"\n  Next: python {__file__} report")


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def cmd_report(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    todo_path = ralph_dir / "TODO.md"
    if not todo_path.exists():
        todo_path = repo_dir / "TODO.md"

    print(f"\n── Generating RUN_RESULT.md ──")

    tasks = parse_todo(todo_path)
    done = sum(1 for t in tasks if t["done"])
    total = len(tasks)
    blocked = total - done

    workers_data = load_json(ralph_dir / "workers.json")
    base = state.get("base_branch", "main")
    stats = diff_stats(repo_dir, base)

    rounds: list[dict] = []
    rounds_file = ralph_dir / "rounds.jsonl"
    if rounds_file.exists():
        for line in rounds_file.read_text().splitlines():
            if line.strip():
                rounds.append(json.loads(line))

    events: list[dict] = []
    events_file = ralph_dir / "events.jsonl"
    if events_file.exists():
        for line in events_file.read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))

    total_tokens = sum(w.get("tokens_used", 0) for w in workers_data.values())
    total_duration = state.get("total_duration_s", 0)

    worker_events = [e for e in events if e.get("type") == "worker_done"]
    gate_events = [e for e in events if e.get("type") == "gate"]
    review_events = [e for e in events if e.get("type") == "review_round"]

    plan_events = [e for e in events if e.get("type") == "plan_complete"]
    plan_dur = 0.0
    exec_dur = sum(r.get("duration_s", 0) for r in rounds)
    gate_dur = sum(e.get("duration_s", 0) for e in gate_events)
    review_dur = len(review_events) * 30.0  # estimate
    merge_dur = max(0, total_duration - plan_dur - exec_dur - gate_dur - review_dur) if total_duration else 0

    report_lines = [
        f"# Run Result — {Path(state['repo']).name}",
        "",
        f"**Run ID:** {state['run_id']}",
        f"**Initiative:** {state.get('initiative', 'unknown')}",
        f"**Agent:** {state.get('agent', 'codex')}",
        f"**Date:** {state.get('created', 'unknown')}",
        f"**Total Duration:** {_fmt_duration(total_duration)}",
        f"**Status:** {state.get('status', 'unknown').upper()}",
        "",
        "## Source",
        "",
        f"- **Repo:** {state['repo']}",
        f"- **Base Branch:** {base}",
        f"- **Integration Branch:** {state.get('integration_branch', 'unknown')}",
        f"- **PR:** {state.get('pr_url', 'not created')}",
        "",
        "## TODO Completion",
        "",
        "| Status | Count |",
        "|--------|-------|",
        f"| Completed | {done} |",
        f"| Blocked | {blocked} |",
        f"| Total | {total} |",
        "",
        "### Task Breakdown",
        "",
        "| # | Task | Worker | Agent | Duration | Status | Retries |",
        "|---|------|--------|-------|----------|--------|---------|",
    ]

    for i, we in enumerate(worker_events, 1):
        desc = we.get("task", "")[:60]
        wid = we.get("worker", "?")
        agent = we.get("agent", "codex")
        dur = _fmt_duration(we.get("duration_s", 0))
        status = "done" if we.get("success") else "failed"
        retries = max(0, we.get("attempts", 1) - 1)
        report_lines.append(f"| {i} | {desc} | {wid} | {agent} | {dur} | {status} | {retries} |")

    report_lines += [
        "",
        "## Round Summary",
        "",
        "| Round | Tasks Attempted | Passed | Failed | Validation | Duration |",
        "|-------|-----------------|--------|--------|------------|----------|",
    ]

    for r in rounds:
        gates_str = " ".join(f"{k}:{v}" for k, v in r.get("gates", {}).items())
        report_lines.append(
            f"| {r['round']} | {r['tasks_attempted']} | {r['passed']} | {r['failed']} | {gates_str} | {_fmt_duration(r['duration_s'])} |"
        )

    report_lines += [
        "",
        "## Worker Performance",
        "",
        "| Worker | Agent | Tasks Done | Total Time | Tokens Used |",
        "|--------|-------|-----------|------------|-------------|",
    ]

    for wid, wdata in workers_data.items():
        agent = wdata.get("agent", "codex")
        tasks_done = sum(1 for we in worker_events if we.get("worker") == wid and we.get("success"))
        dur = _fmt_duration(wdata.get("duration_s", 0))
        tok = wdata.get("tokens_used", 0)
        report_lines.append(f"| {wid} | {agent} | {tasks_done} | {dur} | {tok:,} |")

    if total_duration > 0:
        def _pct(v: float) -> str:
            return f"{v / total_duration * 100:.0f}%" if total_duration else "—"

        report_lines += [
            "",
            "## Key Step Durations",
            "",
            "| Phase | Duration | % of Total |",
            "|-------|----------|------------|",
            f"| Planning (PRD → TODO) | {_fmt_duration(plan_dur)} | {_pct(plan_dur)} |",
            f"| Worker Execution | {_fmt_duration(exec_dur)} | {_pct(exec_dur)} |",
            f"| Validation Gates | {_fmt_duration(gate_dur)} | {_pct(gate_dur)} |",
            f"| Integration/Merge | {_fmt_duration(merge_dur)} | {_pct(merge_dur)} |",
            f"| PR Review Loop | {_fmt_duration(review_dur)} | {_pct(review_dur)} |",
            f"| **Total** | **{_fmt_duration(total_duration)}** | **100%** |",
        ]

    report_lines += [
        "",
        "## Code Changes",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Files Changed | {stats['files_changed']} |",
        f"| Lines Added | {stats['lines_added']} |",
        f"| Lines Deleted | {stats['lines_deleted']} |",
        "",
        "## Token Usage",
        "",
        f"**Total tokens:** {total_tokens:,}",
        "",
    ]

    gate_summary: dict[str, list[str]] = {}
    for ge in gate_events:
        gate_name = ge.get("gate", "unknown")
        gate_summary.setdefault(gate_name, []).append(ge.get("status", "?"))

    if gate_summary:
        report_lines += [
            "## Validation Summary",
            "",
        ]
        for gname, statuses in gate_summary.items():
            passed_count = sum(1 for s in statuses if s == "pass")
            failed_count = sum(1 for s in statuses if s == "fail")
            report_lines.append(f"- **{gname.title()}:** {passed_count} passed, {failed_count} failed")
        report_lines.append("")

    report_lines += [
        "## PR Review",
        "",
        f"- **PR:** {state.get('pr_url', 'not created')}",
        f"- **Review Rounds:** {len(review_events)}",
        f"- **Final Status:** {state.get('status', 'unknown')}",
        "",
    ]

    merge_conflicts = [e for e in events if e.get("type") == "merge_conflict"]
    if merge_conflicts or blocked > 0:
        report_lines += ["## Blocked / Escalated Items", ""]
        for mc in merge_conflicts:
            report_lines.append(f"- Merge conflict: {mc.get('branch', '?')}")
        if blocked > 0:
            for t in tasks:
                if not t["done"]:
                    report_lines.append(f"- Incomplete: {t['description'][:80]}")
        report_lines.append("")

    retro_notes = []
    if worker_events:
        longest = max(worker_events, key=lambda e: e.get("duration_s", 0))
        retro_notes.append(f"Longest task: \"{longest.get('task', '')[:60]}\" ({_fmt_duration(longest.get('duration_s', 0))})")
        most_retried = max(worker_events, key=lambda e: e.get("attempts", 1))
        if most_retried.get("attempts", 1) > 1:
            retro_notes.append(f"Most retried: \"{most_retried.get('task', '')[:60]}\" ({most_retried.get('attempts', 1)} attempts)")
    if merge_conflicts:
        retro_notes.append(f"{len(merge_conflicts)} merge conflict(s) encountered")
    failed_gates = [e for e in gate_events if e.get("status") == "fail"]
    if failed_gates:
        retro_notes.append(f"{len(failed_gates)} gate failure(s) across all rounds")

    if retro_notes:
        report_lines += ["## Retrospective Notes", ""]
        for note in retro_notes:
            report_lines.append(f"- {note}")
        report_lines.append("")

    result_path = ralph_dir / "RUN_RESULT.md"
    result_path.write_text("\n".join(report_lines) + "\n")

    print(f"  Written to: {result_path}")
    print(f"  Tasks: {done}/{total}")
    print(f"  Duration: {_fmt_duration(total_duration)}")
    print(f"  Code: +{stats['lines_added']} -{stats['lines_deleted']} in {stats['files_changed']} files")
    print(f"  Tokens: {total_tokens:,}")


# ── Retro + Backlog ──────────────────────────────────────────────────────────


def cmd_retro(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    initiative = state.get("initiative", "unknown")
    run_id = state.get("run_id", "unknown")

    todo_path = ralph_dir / "TODO.md"
    if not todo_path.exists():
        todo_path = repo_dir / "TODO.md"

    print(f"\n── Generating retro.md + backlog.md ──")

    tasks = parse_todo(todo_path)
    done_tasks = [t for t in tasks if t["done"]]
    incomplete_tasks = [t for t in tasks if not t["done"]]
    total = len(tasks)
    done = len(done_tasks)

    events: list[dict] = []
    events_file = ralph_dir / "events.jsonl"
    if events_file.exists():
        for line in events_file.read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))

    worker_events = [e for e in events if e.get("type") == "worker_done"]
    gate_events = [e for e in events if e.get("type") == "gate"]
    merge_conflicts = [e for e in events if e.get("type") == "merge_conflict"]

    total_duration = state.get("total_duration_s", 0)
    total_tokens = 0
    workers_data = load_json(ralph_dir / "workers.json")
    for w in workers_data.values():
        total_tokens += w.get("tokens_used", 0)

    first_attempt_passes = sum(
        1 for we in worker_events
        if we.get("success") and we.get("attempts", 1) == 1
    )
    total_retries = sum(max(0, we.get("attempts", 1) - 1) for we in worker_events)
    failed_gates = [e for e in gate_events if e.get("status") == "fail"]
    successful_workers = [we for we in worker_events if we.get("success")]
    failed_workers = [we for we in worker_events if not we.get("success")]

    first_attempt_rate = (
        round(first_attempt_passes / len(worker_events) * 100)
        if worker_events else 0
    )

    # ── Build retro.md ──

    lines = [
        f"# Retrospective — {initiative} / {run_id}",
        "",
        f"**Date:** {state.get('created', 'unknown')}",
        f"**Duration:** {_fmt_duration(total_duration)}",
        f"**Status:** {state.get('status', 'unknown').upper()}",
        f"**Agent:** {state.get('agent', 'codex')}",
        "",
        "## What Was Done",
        "",
        f"{done}/{total} tasks completed.",
        "",
    ]

    if done_tasks:
        lines += ["### Completed Tasks", ""]
        for t in done_tasks:
            we_match = next(
                (we for we in worker_events if we.get("task", "").strip() == t["description"].strip() and we.get("success")),
                None,
            )
            worker = we_match.get("worker", "?") if we_match else "?"
            dur = _fmt_duration(we_match.get("duration_s", 0)) if we_match else "?"
            lines.append(f"- [x] {t['description']} — *{worker}* ({dur})")
        lines.append("")

    if incomplete_tasks:
        lines += ["### Incomplete Tasks", ""]
        for t in incomplete_tasks:
            we_match = next(
                (we for we in failed_workers if we.get("task", "").strip() == t["description"].strip()),
                None,
            )
            reason = ""
            if we_match:
                reason = f" — failed after {we_match.get('attempts', '?')} attempts"
            lines.append(f"- [ ] {t['description']}{reason}")
        lines.append("")

    # ── What Went Well ──

    went_well: list[str] = []
    if first_attempt_rate >= 80:
        went_well.append(f"High first-attempt pass rate: {first_attempt_rate}%")
    elif first_attempt_rate >= 50:
        went_well.append(f"Reasonable first-attempt pass rate: {first_attempt_rate}%")

    fast_tasks = [
        we for we in successful_workers
        if we.get("duration_s", 999) < 120
    ]
    if fast_tasks:
        went_well.append(f"{len(fast_tasks)} task(s) completed in under 2 minutes")

    clean_gate_rounds = sum(
        1 for e in gate_events
        if e.get("gate") == "test" and e.get("status") == "pass"
    )
    if clean_gate_rounds > 0:
        went_well.append(f"{clean_gate_rounds} clean test gate pass(es)")

    if not merge_conflicts:
        went_well.append("No merge conflicts")

    if done == total:
        went_well.append("All tasks completed successfully")

    lines += ["## What Went Well", ""]
    if went_well:
        for item in went_well:
            lines.append(f"- {item}")
    else:
        lines.append("- (no notable positives detected)")
    lines.append("")

    # ── What Needs Improvement ──

    issues: list[dict] = []

    for we in failed_workers:
        issues.append({
            "category": "execution",
            "issue": f"Task failed: \"{we.get('task', '')[:60]}\" ({we.get('attempts', 1)} attempts)",
            "severity": "high",
            "action": "Break task into smaller units or improve task description specificity",
        })

    high_retry_tasks = [
        we for we in worker_events
        if we.get("attempts", 1) > 1 and we.get("success")
    ]
    for we in high_retry_tasks:
        issues.append({
            "category": "execution",
            "issue": f"Task needed {we.get('attempts', 1)} attempts: \"{we.get('task', '')[:60]}\"",
            "severity": "medium",
            "action": "Clarify task description or add more context to the prompt",
        })

    for fg in failed_gates:
        issues.append({
            "category": "validation",
            "issue": f"Gate failure: {fg.get('gate', '?')} ({fg.get('command', 'unknown')})",
            "severity": "medium" if fg.get("gate") != "test" else "high",
            "action": "Check if test is flaky or if gate command needs adjustment in repo_profile.json",
        })

    for mc in merge_conflicts:
        issues.append({
            "category": "integration",
            "issue": f"Merge conflict on branch: {mc.get('branch', '?')}",
            "severity": "high",
            "action": "Assign overlapping file tasks to the same worker or reduce parallelism",
        })

    if incomplete_tasks and not failed_workers:
        issues.append({
            "category": "planning",
            "issue": f"{len(incomplete_tasks)} tasks not attempted (ran out of rounds)",
            "severity": "medium",
            "action": "Increase --max-rounds or break PRD into smaller initiatives",
        })

    slow_tasks = [
        we for we in worker_events
        if we.get("duration_s", 0) > 300
    ]
    for st in slow_tasks:
        issues.append({
            "category": "execution",
            "issue": f"Slow task ({_fmt_duration(st.get('duration_s', 0))}): \"{st.get('task', '')[:60]}\"",
            "severity": "low",
            "action": "Consider decomposing into smaller subtasks",
        })

    lines += ["## What Needs Improvement", ""]
    if issues:
        lines += [
            "| Category | Issue | Severity | Suggested Action |",
            "|----------|-------|----------|------------------|",
        ]
        for iss in issues:
            lines.append(
                f"| {iss['category']} | {iss['issue']} | {iss['severity']} | {iss['action']} |"
            )
    else:
        lines.append("No issues detected — clean run.")
    lines.append("")

    # ── Metrics Snapshot ──

    lines += [
        "## Metrics Snapshot",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Tasks completed | {done}/{total} |",
        f"| First-attempt pass rate | {first_attempt_rate}% |",
        f"| Total retries | {total_retries} |",
        f"| Merge conflicts | {len(merge_conflicts)} |",
        f"| Gate failures | {len(failed_gates)} |",
        f"| Total tokens | {total_tokens:,} |",
        f"| Total duration | {_fmt_duration(total_duration)} |",
        "",
    ]

    # ── Generate backlog items from issues ──

    backlog_items = _build_backlog_items(issues, run_id)

    if backlog_items:
        lines += ["## Backlog Items Generated", ""]
        for bi in backlog_items:
            lines.append(f"- **[{bi['priority']}]** {bi['title']} (from: {bi['source']})")
        lines.append("")

    retro_path = ralph_dir / "retro.md"
    retro_path.write_text("\n".join(lines) + "\n")
    print(f"  retro.md written to: {retro_path}")

    # ── Update initiative-level backlog.md ──

    runs_root = ralph_dir.parent.parent  # runs/<initiative>/<run-id> → runs/
    initiative_dir = ralph_dir.parent     # runs/<initiative>/
    _update_backlog(initiative_dir, backlog_items, run_id, initiative)

    state["retro_generated"] = True
    save_json(ralph_dir / "loop_state.json", state)

    emit_event(ralph_dir, {
        "type": "retro",
        "backlog_items": len(backlog_items),
        "went_well": len(went_well),
        "issues": len(issues),
    })

    print(f"  Backlog items: {len(backlog_items)} added to {initiative_dir / 'backlog.md'}")
    print(f"  Went well: {len(went_well)} | Issues: {len(issues)}")


def _build_backlog_items(issues: list[dict], run_id: str) -> list[dict]:
    """Convert retro issues into prioritized backlog items."""
    severity_to_priority = {"high": "P1", "medium": "P2", "low": "P3"}
    items = []
    seen_titles: set[str] = set()

    for iss in issues:
        title = iss["action"]
        if title in seen_titles:
            continue
        seen_titles.add(title)
        items.append({
            "priority": severity_to_priority.get(iss["severity"], "P3"),
            "title": title,
            "category": iss["category"],
            "source": f"{run_id} — {iss['issue'][:80]}",
            "status": "open",
        })

    items.sort(key=lambda x: x["priority"])
    return items


def _update_backlog(
    initiative_dir: Path,
    new_items: list[dict],
    run_id: str,
    initiative: str,
) -> None:
    """Append new backlog items to the initiative-level backlog.md."""
    backlog_path = initiative_dir / "backlog.md"

    if not backlog_path.exists():
        header = [
            f"# Backlog — {initiative}",
            "",
            "Accumulated improvement items across ralph loop runs.",
            "Priority: P1 (critical) > P2 (should fix) > P3 (nice to have).",
            "",
            "| # | Priority | Category | Item | Source Run | Status |",
            "|---|----------|----------|------|-----------|--------|",
        ]
        backlog_path.write_text("\n".join(header) + "\n")

    existing = backlog_path.read_text()
    existing_lines = existing.splitlines()

    table_lines = [l for l in existing_lines if l.startswith("|") and not l.startswith("| #") and not l.startswith("|---")]
    next_num = len(table_lines) + 1

    new_rows = []
    for item in new_items:
        row = f"| {next_num} | {item['priority']} | {item['category']} | {item['title']} | {run_id} | {item['status']} |"
        if row not in existing:
            new_rows.append(row)
            next_num += 1

    if new_rows:
        with open(backlog_path, "a") as f:
            for row in new_rows:
                f.write(row + "\n")


def cmd_backlog(args: argparse.Namespace) -> None:
    """Show or manage the initiative-level backlog."""
    run_dir = Path(getattr(args, "run_dir", None) or ".").resolve()

    initiative = getattr(args, "initiative", None)

    runs_root = run_dir / "runs" if (run_dir / "runs").exists() else run_dir

    if initiative:
        backlog_path = runs_root / initiative / "backlog.md"
        if not backlog_path.exists():
            print(f"No backlog found for initiative '{initiative}'.")
            return
        print(backlog_path.read_text())
        return

    print(f"\n{'=' * 60}")
    print(f" Backlogs by Initiative")
    print(f"{'=' * 60}")

    found = False
    for entry in sorted(runs_root.iterdir()):
        if entry.is_dir():
            bp = entry / "backlog.md"
            if bp.exists():
                found = True
                content = bp.read_text()
                table_lines = [
                    l for l in content.splitlines()
                    if l.startswith("|") and not l.startswith("| #") and not l.startswith("|---")
                ]
                open_count = sum(1 for l in table_lines if "| open |" in l)
                total_count = len(table_lines)
                print(f"\n  [{entry.name}] {open_count} open / {total_count} total items")
                p1 = sum(1 for l in table_lines if "| P1 |" in l and "| open |" in l)
                p2 = sum(1 for l in table_lines if "| P2 |" in l and "| open |" in l)
                p3 = sum(1 for l in table_lines if "| P3 |" in l and "| open |" in l)
                if p1 or p2 or p3:
                    print(f"    P1: {p1}  P2: {p2}  P3: {p3}")

    if not found:
        print("\n  No backlogs found. Run 'retro' after a completed run to generate.")

    print(f"\n{'=' * 60}")


def cmd_status(args: argparse.Namespace) -> None:
    ralph_dir, state = _load_state(args)
    workers_data = load_json(ralph_dir / "workers.json")
    todo_path = ralph_dir / "TODO.md"
    if not todo_path.exists():
        todo_path = Path(state["repo"]) / "TODO.md"
    tasks = parse_todo(todo_path) if todo_path.exists() else []
    done = sum(1 for t in tasks if t["done"])

    print(f"\n{'=' * 60}")
    print(f" Ralph Loop Status")
    print(f"{'=' * 60}")
    print(f"  Initiative: {state.get('initiative', 'unknown')}")
    print(f"  Run ID:   {state.get('run_id', 'unknown')}")
    print(f"  Agent:    {state.get('agent', 'codex')}")
    print(f"  Status:   {state.get('status', 'unknown')}")
    print(f"  Round:    {state.get('round', 0)}/{state.get('max_rounds', '?')}")
    print(f"  Tasks:    {done}/{len(tasks)} done")
    print(f"  Branch:   {state.get('integration_branch', 'unknown')}")
    print(f"  PR:       {state.get('pr_url', 'not created')}")
    print(f"\n  Workers:")
    for wid, wdata in workers_data.items():
        task = wdata.get("task", "idle") or "idle"
        status = wdata.get("status", "unknown")
        hb = wdata.get("heartbeat", "?")
        print(f"    {wid}: [{status}] {task[:50]} (heartbeat: {hb})")
    print(f"{'=' * 60}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    import http.server

    ralph_dir, state = _load_state(args)
    port = args.port

    html = textwrap.dedent("""\
    <!DOCTYPE html>
    <html><head><title>Ralph Loop Dashboard</title>
    <meta charset="utf-8">
    <style>
      body { font-family: system-ui; max-width: 900px; margin: 2rem auto; background: #0d1117; color: #c9d1d9; }
      h1 { color: #58a6ff; } h2 { color: #8b949e; border-bottom: 1px solid #21262d; }
      .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 1rem; margin: 0.5rem 0; }
      .pass { color: #3fb950; } .fail { color: #f85149; } .running { color: #d29922; }
      table { width: 100%; border-collapse: collapse; }
      td, th { padding: 0.4rem 0.8rem; text-align: left; border-bottom: 1px solid #21262d; }
    </style>
    <script>
    async function refresh() {
      try {
        const [state, workers] = await Promise.all([
          fetch('/loop_state.json').then(r => r.json()),
          fetch('/workers.json').then(r => r.json()),
        ]);
        document.getElementById('status').textContent = state.status || 'unknown';
        document.getElementById('round').textContent = `${state.round || 0}/${state.max_rounds || '?'}`;
        document.getElementById('run-id').textContent = state.run_id || '';
        let whtml = '';
        for (const [id, w] of Object.entries(workers)) {
          const cls = w.status === 'done' ? 'pass' : w.status === 'failed' ? 'fail' : 'running';
          whtml += `<div class="card"><b class="${cls}">${id}</b> [${w.status}] ${w.task || 'idle'}<br>
            <small>heartbeat: ${w.heartbeat || '?'} | tokens: ${w.tokens_used || 0}</small></div>`;
        }
        document.getElementById('workers').innerHTML = whtml;
      } catch(e) { console.error(e); }
    }
    setInterval(refresh, 2000);
    refresh();
    </script>
    </head><body>
    <h1>Ralph Loop Dashboard</h1>
    <p>Run: <span id="run-id"></span> | Status: <b id="status"></b> | Round: <span id="round"></span></p>
    <h2>Workers</h2><div id="workers">Loading...</div>
    </body></html>
    """)

    dashboard_path = ralph_dir / "dashboard.html"
    dashboard_path.write_text(html)

    os.chdir(ralph_dir)
    handler = http.server.SimpleHTTPRequestHandler
    print(f"  Dashboard: http://localhost:{port}")
    http.server.HTTPServer(("", port), handler).serve_forever()


def cmd_pause(args: argparse.Namespace) -> None:
    """Pause a running loop: kill workers, save clean state, generate resume prompt."""
    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])

    # Kill running Docker containers for this run
    docker_image = state.get("profile", {}).get("docker_image")
    if docker_image:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={docker_image}", "-q"],
            capture_output=True, text=True,
        )
        container_ids = r.stdout.strip().splitlines()
        if container_ids:
            subprocess.run(["docker", "stop", *container_ids], capture_output=True, text=True)
            print(f"  Stopped {len(container_ids)} Docker container(s)")

    # Save paused state
    state["status"] = "paused"
    save_json(ralph_dir / "loop_state.json", state)

    # Reset workers to idle (preserve worktree/branch)
    workers_path = ralph_dir / "workers.json"
    workers = load_json(workers_path)
    for wid in workers:
        workers[wid]["status"] = "idle"
        workers[wid]["task"] = None
    save_json(workers_path, workers)

    # Clean worktrees
    for wid, wdata in workers.items():
        wt = Path(wdata.get("worktree", ""))
        if wt.exists():
            reset_worktree(wt, wdata.get("branch", ""))

    emit_event(ralph_dir, {"type": "paused", "round": state.get("round", 0)})

    print(f"\n{'=' * 60}")
    print(f" Ralph Loop — Paused")
    print(f"{'=' * 60}")

    # Generate and print the resume prompt
    _print_resume_prompt(ralph_dir, state, workers)


def cmd_resume_prompt(args: argparse.Namespace) -> None:
    """Generate a resume prompt for the current run state without changing anything."""
    ralph_dir, state = _load_state(args)
    workers = load_json(ralph_dir / "workers.json")
    _print_resume_prompt(ralph_dir, state, workers)


def _print_resume_prompt(ralph_dir: Path, state: dict, workers: dict) -> None:
    """Build and print a copy-paste resume prompt."""
    todo_path = ralph_dir / "TODO.md"
    tasks = parse_todo(todo_path) if todo_path.exists() else []
    done = sum(1 for t in tasks if t["done"])
    total = len(tasks)
    remaining = total - done

    repo_dir = state.get("repo", ".")
    run_dir = str(ralph_dir)
    profile = state.get("profile", {})
    docker_image = profile.get("docker_image", "")
    agent = state.get("agent", "codex")
    rnd = state.get("round", 0)
    max_rounds = state.get("max_rounds", 10)
    integration_branch = state.get("integration_branch", "")
    initiative = state.get("initiative", "")
    run_id = state.get("run_id", "")

    worktree_base = ""
    for wdata in workers.values():
        wt = wdata.get("worktree", "")
        if wt:
            worktree_base = str(Path(wt).parent)
            break

    remaining_tasks_list = "\n".join(
        f"  - {t['description'][:80]}" for t in tasks if not t["done"]
    )
    done_tasks_list = "\n".join(
        f"  - {t['description'][:80]}" for t in tasks if t["done"]
    )

    prompt = textwrap.dedent(f"""\
    Resume the ralph loop run for the {initiative} initiative.

    SESSION: {run_dir}
    ORCHESTRATOR: {Path(repo_dir) / 'lightweight-ralph-loop' / 'scripts' / 'ralph_orchestrator.py'}
    REPO: {repo_dir}

    CURRENT STATE:
    - Run ID: {run_id}
    - Round: {rnd}/{max_rounds} (status: {state.get('status', '?')})
    - TODO: {done}/{total} done, {remaining} remaining
    - Agent: {agent} | Docker image: {docker_image}
    - Integration branch: {integration_branch}
    - Worktrees: {worktree_base}

    COMPLETED TASKS:
    {done_tasks_list or '  (none)'}

    REMAINING TASKS:
    {remaining_tasks_list or '  (none — all done, run report + retro)'}

    STEPS TO RESUME:
    1. Reset loop_state.json: set round={rnd}, status="planned"
       (keep round at {rnd} so the loop starts at round {rnd + 1})
    2. Ensure workers.json has both workers idle with worktree/branch paths
    3. Clean worktrees: git clean -fd in each worktree under {worktree_base}
    4. Truncate logs/ralph.log (optional — or keep for history)
    5. Launch:
       cd {repo_dir} && nohup python3 -u {Path(repo_dir) / 'lightweight-ralph-loop' / 'scripts' / 'ralph_orchestrator.py'} run --run-dir {run_dir} > /dev/null 2>&1 &
    6. Monitor: tail -f {run_dir}/logs/ralph.log
    7. When all {remaining} remaining tasks complete, run:
       python3 {Path(repo_dir) / 'lightweight-ralph-loop' / 'scripts' / 'ralph_orchestrator.py'} report --run-dir {run_dir}
       python3 {Path(repo_dir) / 'lightweight-ralph-loop' / 'scripts' / 'ralph_orchestrator.py'} retro --run-dir {run_dir}
    8. Verify code on integration branch:
       cd {repo_dir} && git log --oneline {integration_branch} | head -20

    ENVIRONMENT:
    - .env at {repo_dir}/.env has OPENAI_API_KEY and OPENAI_BASE_URL
    - pip deps installed in Docker: {', '.join(profile.get('pip_deps', []))}
    - reference_dirs: {', '.join(state.get('reference_dirs', []))}
    """)

    prompt_path = ralph_dir / "RESUME_PROMPT.md"
    prompt_path.write_text(prompt)

    print(prompt)
    print(f"  Saved to: {prompt_path}")
    print(f"  Copy the above into a new Cursor/agent session to continue.")


def cmd_publish(args: argparse.Namespace) -> None:
    """Publish run results to GitHub: create repo, push worker branches, raise PRs."""
    import tempfile

    ralph_dir, state = _load_state(args)
    repo_dir = Path(state["repo"])
    _load_dotenv(repo_dir)

    initiative = state.get("initiative", "unknown")
    run_id = state["run_id"]
    integration_branch = state["integration_branch"]
    workers_data = load_json(ralph_dir / "workers.json")

    gh_owner = getattr(args, "owner", None)
    if not gh_owner:
        result = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True, text=True, check=False,
        )
        gh_owner = result.stdout.strip() if result.returncode == 0 else None
        if not gh_owner:
            print("Error: Could not determine GitHub owner. Pass --owner or authenticate gh.")
            sys.exit(1)

    repo_name = getattr(args, "repo_name", None) or initiative
    visibility = getattr(args, "visibility", "public")
    full_repo = f"{gh_owner}/{repo_name}"

    print(f"\n── Publishing to GitHub ──")
    print(f"  Repo: {full_repo}")
    print(f"  Initiative: {initiative}")
    print(f"  Run: {run_id}")

    repo_exists = subprocess.run(
        ["gh", "repo", "view", full_repo, "--json", "url"],
        capture_output=True, text=True, check=False,
    )
    if repo_exists.returncode != 0:
        print(f"  Creating repo: {full_repo}")
        subprocess.run(
            ["gh", "repo", "create", full_repo,
             f"--{visibility}",
             "--description", f"{initiative} — generated by Ralph Loop (run {run_id})"],
            capture_output=True, text=True, check=True,
        )
    else:
        url = json.loads(repo_exists.stdout).get("url", full_repo)
        print(f"  Repo exists: {url}")

    files_on_branch = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", integration_branch, "--", f"{initiative}/"],
        cwd=repo_dir, capture_output=True, text=True, check=False,
    )
    if files_on_branch.returncode != 0 or not files_on_branch.stdout.strip():
        print(f"  Error: No files found on integration branch {integration_branch} under {initiative}/")
        sys.exit(1)

    all_files = files_on_branch.stdout.strip().splitlines()
    print(f"  Files on integration branch: {len(all_files)}")

    worker_commits: dict[str, list[str]] = {wid: [] for wid in workers_data}
    log_result = subprocess.run(
        ["git", "log", "--oneline", integration_branch],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    )
    for line in log_result.stdout.splitlines():
        for wid in workers_data:
            if f"[{wid}]" in line:
                sha = line.split()[0]
                worker_commits[wid].append(sha)

    worker_files: dict[str, set[str]] = {wid: set() for wid in workers_data}
    for wid, shas in worker_commits.items():
        for sha in shas:
            diff_result = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
                cwd=repo_dir, capture_output=True, text=True, check=False,
            )
            if diff_result.returncode == 0:
                for f in diff_result.stdout.strip().splitlines():
                    if f.startswith(f"{initiative}/"):
                        worker_files[wid].add(f)

    print("  Worker file assignments:")
    for wid, files in worker_files.items():
        print(f"    {wid}: {len(files)} files")

    publish_dir = Path(tempfile.mkdtemp(prefix=f"ralph-publish-{run_id}-"))
    subprocess.run(["git", "init"], cwd=publish_dir, capture_output=True, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=publish_dir, capture_output=True, check=True)

    gitignore_content = "__pycache__/\n*.pyc\n*.pyo\n.env\n.DS_Store\n*.egg-info/\n.venv/\nvenv/\n*.log\n.cursor/\n"
    (publish_dir / ".gitignore").write_text(gitignore_content)

    prd_path = ralph_dir / "PRD.md"
    prd_summary = ""
    if prd_path.exists():
        prd_lines = prd_path.read_text().splitlines()
        prd_summary = "\n".join(prd_lines[:30])

    total_workers = len(workers_data)
    worker_list = ", ".join(workers_data.keys())
    rounds_file = ralph_dir / "rounds.jsonl"
    total_rounds = 0
    if rounds_file.exists():
        total_rounds = len([l for l in rounds_file.read_text().splitlines() if l.strip()])
    duration = state.get("total_duration_s", 0)
    dur_str = f"{int(duration // 60)}m {int(duration % 60):02d}s" if duration else "unknown"

    readme = f"""# {initiative}

Generated by [Ralph Loop](https://github.com/mykie2015/ralph_loop) — autonomous multi-worker coding loop.

## Run Info

- **Run ID:** `{run_id}`
- **Workers:** {worker_list} ({total_workers} parallel)
- **Rounds:** {total_rounds}
- **Duration:** {dur_str}
- **Status:** {state.get('status', 'unknown')}

## Files

```
{chr(10).join(f.removeprefix(initiative + '/') for f in sorted(all_files))}
```

{f'## PRD Summary{chr(10)}{chr(10)}{prd_summary}' if prd_summary else ''}
"""
    (publish_dir / "README.md").write_text(readme)

    subprocess.run(["git", "add", "-A"], cwd=publish_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"initial: {initiative} project skeleton"],
        cwd=publish_dir, capture_output=True, text=True, check=True,
    )

    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{full_repo}.git"],
        cwd=publish_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main", "--force"],
        cwd=publish_dir, capture_output=True, text=True, check=True,
    )
    print("  Pushed main branch (skeleton)")

    pr_urls = []
    for wid, files in worker_files.items():
        if not files:
            print(f"  [{wid}] No files — skipping")
            continue

        subprocess.run(["git", "checkout", "main"], cwd=publish_dir, capture_output=True, check=True)
        branch_name = f"worker/{wid}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=publish_dir, capture_output=True, check=True,
        )

        for filepath in sorted(files):
            relative = filepath.removeprefix(f"{initiative}/")
            dest = publish_dir / relative
            dest.parent.mkdir(parents=True, exist_ok=True)

            extract = subprocess.run(
                ["git", "show", f"{integration_branch}:{filepath}"],
                cwd=repo_dir, capture_output=True, check=False,
            )
            if extract.returncode == 0:
                dest.write_bytes(extract.stdout)

        subprocess.run(["git", "add", "-A"], cwd=publish_dir, capture_output=True, check=True)

        file_list = "\n".join(f"- {f.removeprefix(initiative + '/')}" for f in sorted(files))
        commit_msg = f"feat({wid}): {len(files)} files from Ralph Loop run {run_id}\n\n{file_list}"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=publish_dir, capture_output=True, text=True, check=True,
        )

        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=publish_dir, capture_output=True, text=True, check=True,
        )

        pr_body = f"## Worker `{wid}` — Ralph Loop Run `{run_id}`\n\n"
        pr_body += f"### Files ({len(files)})\n\n{file_list}\n\n"
        pr_body += f"### Run Info\n\n"
        pr_body += f"- **Initiative:** {initiative}\n"
        pr_body += f"- **Rounds:** {total_rounds}\n"
        pr_body += f"- **Duration:** {dur_str}\n"
        pr_body += f"- **Generated by:** [Ralph Loop](https://github.com/mykie2015/ralph_loop)\n"

        pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--repo", full_repo,
             "--base", "main",
             "--head", branch_name,
             "--title", f"feat({wid}): {len(files)} files from run {run_id}",
             "--body", pr_body],
            cwd=publish_dir, capture_output=True, text=True, check=False,
        )

        if pr_result.returncode == 0:
            pr_url = pr_result.stdout.strip()
            pr_urls.append(pr_url)
            print(f"  [{wid}] PR created: {pr_url}")
        else:
            print(f"  [{wid}] PR failed: {pr_result.stderr.strip()}")

        for filepath in sorted(files):
            relative = filepath.removeprefix(f"{initiative}/")
            dest = publish_dir / relative
            if dest.exists():
                dest.unlink()
            parent = dest.parent
            while parent != publish_dir and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    # ── Tooling branches: reference_dirs + orchestrator ──
    tooling_dirs: list[tuple[str, Path]] = []

    reference_dirs = state.get("reference_dirs", [])
    for rd in reference_dirs:
        rd_path = Path(rd)
        if rd_path.is_dir():
            tooling_dirs.append((rd_path.name, rd_path))

    orchestrator_dir = Path(__file__).resolve().parent.parent
    if orchestrator_dir.is_dir() and orchestrator_dir.name not in [d[0] for d in tooling_dirs]:
        tooling_dirs.append((orchestrator_dir.name, orchestrator_dir))

    for dir_name, dir_path in tooling_dirs:
        subprocess.run(["git", "checkout", "main"], cwd=publish_dir, capture_output=True, check=True)
        branch_name = f"tooling/{dir_name}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=publish_dir, capture_output=True, check=True,
        )

        tooling_dest = publish_dir / dir_name
        shutil.copytree(
            dir_path, tooling_dest,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".env",
                "node_modules", ".git", ".venv", "venv",
            ),
        )

        subprocess.run(["git", "add", "-A"], cwd=publish_dir, capture_output=True, check=True)
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"tooling({dir_name}): add {dir_name} used in run {run_id}"],
            cwd=publish_dir, capture_output=True, text=True, check=False,
        )
        if commit_result.returncode != 0:
            print(f"  [tooling/{dir_name}] nothing to commit — skipping")
            shutil.rmtree(tooling_dest, ignore_errors=True)
            continue

        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=publish_dir, capture_output=True, text=True, check=True,
        )

        file_count = sum(1 for _ in dir_path.rglob("*") if _.is_file() and "__pycache__" not in str(_))
        pr_body = f"## Tooling: `{dir_name}`\n\n"
        pr_body += f"Reference/tooling directory used during Ralph Loop run `{run_id}`.\n\n"
        pr_body += f"- **Files:** {file_count}\n"
        pr_body += f"- **Initiative:** {initiative}\n"
        pr_body += f"- **Source:** `{dir_path}`\n"

        pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--repo", full_repo,
             "--base", "main",
             "--head", branch_name,
             "--title", f"tooling({dir_name}): add {dir_name}",
             "--body", pr_body],
            cwd=publish_dir, capture_output=True, text=True, check=False,
        )
        if pr_result.returncode == 0:
            pr_url = pr_result.stdout.strip()
            pr_urls.append(pr_url)
            print(f"  [tooling/{dir_name}] PR created: {pr_url}")
        else:
            print(f"  [tooling/{dir_name}] PR failed: {pr_result.stderr.strip()}")

        shutil.rmtree(tooling_dest, ignore_errors=True)

    state["github_repo"] = full_repo
    state["github_prs"] = pr_urls
    save_json(ralph_dir / "loop_state.json", state)

    print(f"\n  Published to: https://github.com/{full_repo}")
    print(f"  PRs created: {len(pr_urls)}")
    for url in pr_urls:
        print(f"    {url}")

    shutil.rmtree(publish_dir, ignore_errors=True)


def cmd_full(args: argparse.Namespace) -> None:
    cmd_init(args)

    ralph_dir, _ = _load_state(args)
    args.run_dir = str(ralph_dir)

    cmd_plan(args)

    print(f"\n{'─' * 60}")
    print("[HUMAN CHECKPOINT]")
    print("  Review TODO.md before worker dispatch.")
    approval = input("  Press ENTER to continue, or type 'abort': ").strip()
    if approval.lower() == "abort":
        print("  Aborted.")
        sys.exit(0)

    cmd_run(args)
    cmd_report(args)
    cmd_retro(args)
    cmd_publish(args)


# ── State loader ─────────────────────────────────────────────────────────────


def _load_state(args: argparse.Namespace) -> tuple[Path, dict]:
    run_dir = Path(getattr(args, "run_dir", None) or ".").resolve()

    candidates = [run_dir, run_dir / ".ralph"]

    for candidate in candidates:
        state_file = candidate / "loop_state.json"
        if state_file.exists():
            return candidate, load_json(state_file)

    runs_dir = run_dir / "runs"
    if runs_dir.exists():
        for initiative_dir in sorted(runs_dir.iterdir(), reverse=True):
            if initiative_dir.is_dir() and initiative_dir.name != "index.json":
                for run_subdir in sorted(initiative_dir.iterdir(), reverse=True):
                    state_file = run_subdir / "loop_state.json"
                    if state_file.exists():
                        return run_subdir, load_json(state_file)

    print(f"Error: No loop_state.json found. Run 'init' first.")
    sys.exit(1)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight Ralph Loop Orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize a ralph loop run")
    p_init.add_argument("--repo", required=True, help="Target repo path")
    p_init.add_argument("--prd", required=True, help="PRD.md path")
    p_init.add_argument("--initiative", required=True, help="Initiative name (e.g. auth-overhaul)")
    p_init.add_argument("--agent", default="codex", choices=["codex", "opencode"], help="Agent backend")
    p_init.add_argument("--base", default=None, help="Base branch (default: current)")
    p_init.add_argument("--workers", type=int, default=3, help="Number of workers")
    p_init.add_argument("--max-rounds", type=int, default=10)
    p_init.add_argument("--max-retries", type=int, default=3)
    p_init.add_argument("--profile", default=None, help="repo_profile.json path")

    p_plan = sub.add_parser("plan", help="Generate TODO.md from PRD")
    p_plan.add_argument("--run-dir", default=".")

    p_run = sub.add_parser("run", help="Dispatch workers and run rounds")
    p_run.add_argument("--run-dir", default=".")
    p_run.add_argument("--resume", action="store_true")

    p_review = sub.add_parser("review", help="Open PR and run review loop")
    p_review.add_argument("--run-dir", default=".")

    p_report = sub.add_parser("report", help="Generate RUN_RESULT.md")
    p_report.add_argument("--run-dir", default=".")

    p_status = sub.add_parser("status", help="Show live run status")
    p_status.add_argument("--run-dir", default=".")

    p_runs = sub.add_parser("runs", help="List runs by initiative")
    p_runs.add_argument("--run-dir", default=".")
    p_runs.add_argument("--initiative", default=None, help="Filter by initiative name")

    p_dash = sub.add_parser("dashboard", help="Launch HTML dashboard")
    p_dash.add_argument("--run-dir", default=".")
    p_dash.add_argument("--port", type=int, default=8420)

    p_retro = sub.add_parser("retro", help="Generate retro.md and update backlog.md")
    p_retro.add_argument("--run-dir", default=".")

    p_backlog = sub.add_parser("backlog", help="Show initiative backlog")
    p_backlog.add_argument("--run-dir", default=".")
    p_backlog.add_argument("--initiative", default=None, help="Show backlog for specific initiative")

    p_pause = sub.add_parser("pause", help="Pause run: stop workers, save state, generate resume prompt")
    p_pause.add_argument("--run-dir", default=".")

    p_resume_prompt = sub.add_parser("resume-prompt", help="Generate a resume prompt for the current run")
    p_resume_prompt.add_argument("--run-dir", default=".")

    p_publish = sub.add_parser("publish", help="Publish run to GitHub: create repo, push worker branches, raise PRs")
    p_publish.add_argument("--run-dir", default=".")
    p_publish.add_argument("--owner", default=None, help="GitHub owner (default: authenticated user)")
    p_publish.add_argument("--repo-name", default=None, help="GitHub repo name (default: initiative name)")
    p_publish.add_argument("--visibility", default="public", choices=["public", "private"])

    p_full = sub.add_parser("full", help="Full run: init → plan → run → report → retro → publish")
    p_full.add_argument("--repo", required=True)
    p_full.add_argument("--prd", required=True)
    p_full.add_argument("--initiative", required=True)
    p_full.add_argument("--agent", default="codex", choices=["codex", "opencode"])
    p_full.add_argument("--base", default=None)
    p_full.add_argument("--workers", type=int, default=3)
    p_full.add_argument("--max-rounds", type=int, default=10)
    p_full.add_argument("--max-retries", type=int, default=3)
    p_full.add_argument("--profile", default=None)

    args = parser.parse_args()
    cmd = {
        "init": cmd_init,
        "plan": cmd_plan,
        "run": cmd_run,
        "review": cmd_review,
        "report": cmd_report,
        "retro": cmd_retro,
        "backlog": cmd_backlog,
        "status": cmd_status,
        "runs": cmd_runs,
        "dashboard": cmd_dashboard,
        "publish": cmd_publish,
        "pause": cmd_pause,
        "resume-prompt": cmd_resume_prompt,
        "full": cmd_full,
    }
    cmd[args.command](args)


if __name__ == "__main__":
    main()
