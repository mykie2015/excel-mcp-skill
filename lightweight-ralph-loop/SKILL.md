---
name: lightweight-ralph-loop
description: Run a multi-worker autonomous coding loop from PRD.md to validated PR. Orchestrates task breakdown, parallel workers (Codex Docker or OpenCode agent API) in isolated git worktrees, Python-driven TDD/lint/type-check gates, PR creation via gh, automated PR review, and produces a RUN_RESULT.md summary. Each run is organized under an initiative folder for tracking across related efforts. Use when asked to "run a ralph loop", "run ralph against this repo", "set up autonomous coding workers", "ralph wiggum loop", "PRD to PR with parallel workers", or "autonomous multi-worker build from PRD".
---

# Lightweight Ralph Loop

Autonomous multi-worker coding loop: PRD in, validated PR out. The orchestrator can be invoked from any agent (Cursor, Claude Code, Codex, OpenCode) — it uses Codex Docker or OpenCode agent API as the *worker* backends.

## When to use this skill

- You have a `PRD.md` describing work to do against a GitHub repo
- You want multiple workers building in parallel (Codex Docker or OpenCode agent API)
- You need TDD, lint, and type-check gates enforced between rounds
- You want a validated PR with automated review, not just raw commits
- You want a `RUN_RESULT.md` summarizing the entire run
- You want runs organized by initiative for tracking across related efforts

## Prerequisites

- Docker installed and running (for Codex workers), or OpenCode CLI installed (for OpenCode workers)
- `gh` CLI authenticated (`gh auth status`)
- `git` with worktree support
- Python 3.10+
- Target repo cloned locally

The orchestrator checks prerequisites on startup and reports what's missing.

## Instructions

### Step 1: Initialize the run

Collect these inputs (ask if missing):

| Input | Required | Default |
|-------|----------|---------|
| Target repo path | yes | — |
| `PRD.md` path | yes | `./PRD.md` |
| Initiative name | yes | — |
| Agent backend | no | `codex` |
| Base branch | no | repo default branch |
| Number of workers | no | 3 |
| Max rounds | no | 10 |
| Max retries per task | no | 3 |
| Repo profile | no | auto-detect or `repo_profile.json` |

Run the orchestrator init:

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py init \
  --repo /path/to/repo \
  --prd /path/to/PRD.md \
  --initiative "auth-overhaul" \
  --agent codex \
  --workers 3 \
  --max-rounds 10
```

This creates:
- A run directory under `runs/<initiative>/<run-id>/` with `loop_state.json`, `workers.json`, `events.jsonl`
- Integration branch `ralph/<initiative>/<run-id>`
- One git worktree per worker

### Step 2: Planning phase (PRD → TODO.md)

The orchestrator invokes a single agent session to decompose `PRD.md` into `TODO.md`:

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py plan --run-dir <run-dir>
```

**What it produces:**
- `TODO.md` with `- [ ]` checklist items, each tagged with:
  - target files
  - validation command (test/lint)
  - dependency on other tasks (if any)
- Tasks are leaf-level and worker-assignable

**Human checkpoint:** The orchestrator pauses for approval before dispatching workers. Review `TODO.md`, edit if needed, then confirm.

### Step 3: Worker dispatch

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py run --run-dir <run-dir>
```

The orchestrator:

1. Resets each worker's worktree to a clean state at the start of each round
2. Reads `TODO.md` and picks independent leaf tasks
3. Assigns one task per worker (round-robin across available workers)
4. Each worker runs in its own:
   - Git worktree (branched from integration branch)
   - Fresh agent session (Codex Docker container or OpenCode API call)
   - Scoped prompt with exact file targets and validation expectations
5. After each worker completes, runs **focused gates** (test + lint) against the worker's worktree
6. Workers that pass focused gates have their branches merged into the integration branch
7. After all merges, runs **full gates** (test + lint + typecheck + build) against the integration branch
8. `workers.json` updates with heartbeat, current task, timing, and token usage

### Step 4: Validation gates

Two levels of validation run automatically:

**Per-worker focused gates** (against worktree, before merge):
1. Focused tests — `test_focused_cmd` or `test_cmd` against changed scope
2. Lint — linter configured for the repo

**Post-merge full gates** (against integration branch, after merge):
1. Repo-wide tests
2. Lint
3. Type check (optional, per profile)
4. Build (optional, per profile)

Gate results are captured in `events.jsonl`. Task outcomes:
- **pass** → mark `[x]` in TODO.md, merge worker branch into integration branch
- **retry** → re-dispatch to same or different worker (up to max retries)
- **blocked** → log and skip

### Step 5: Integration

After each round of workers completes:
- Validated worker branches merge into the integration branch
- Conflict detection runs; conflicts surface as blocked events
- `rounds.jsonl` gets a summary row (tasks attempted, passed, failed, duration)

### Step 6: PR + Review loop

When all tasks are done (or max rounds reached):

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py review --run-dir <run-dir>
```

1. Opens or updates a PR via `gh pr create` / `gh pr edit`
2. Waits for CI checks
3. Reads PR review comments; classifies blocking comments
4. Blocking findings → append to `TODO.md` under `## Review Findings`, can dispatch another round
5. Invokes `gh-address-comments` skill to handle review comments (if available)
6. Loops until no blocking findings remain (bounded by max review rounds)

### Step 7: Run close & reporting

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py report --run-dir <run-dir>
```

Generates `RUN_RESULT.md` from canonical artifacts. See `assets/run_result_template.md` for the full template.

The report includes:
- PRD summary and TODO completion stats
- Per-task breakdown with worker, agent, duration, retries
- Round-by-round summary with validation results
- Worker performance (tasks done, time, tokens)
- Key step durations with percentages (planning, execution, validation, integration, review)
- Code changes (files changed, lines added/deleted)
- Token usage
- Validation summary across all rounds
- PR status and review outcome
- Retrospective notes (longest task, most retried, conflicts, gate failures)

### Step 8: Retrospective & backlog

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py retro --run-dir <run-dir>
```

Generates two artifacts:

**`retro.md`** (per-run, in the run directory):
- **What was done** — completed and incomplete tasks with worker attribution
- **What went well** — auto-detected positives (first-attempt pass rate, fast tasks, clean gates, no conflicts)
- **What needs improvement** — categorized issues table with severity and suggested actions (planning, execution, validation, integration, review categories)
- **Metrics snapshot** — first-attempt pass rate, retries, conflicts, gate failures, tokens, duration
- **Backlog items generated** — list of items promoted to the initiative backlog

**`backlog.md`** (per-initiative, at `runs/<initiative>/backlog.md`):
- Accumulates improvement items across all runs for the initiative
- Each item has: priority (P1/P2/P3), category, description, source run, status
- P1 = critical (failed tasks, merge conflicts, high-severity gate failures)
- P2 = should fix (retried tasks, medium-severity issues)
- P3 = nice to have (slow tasks, minor optimizations)
- Use the backlog to feed improvement items into future ralph loop runs

View backlogs:
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py backlog
python lightweight-ralph-loop/scripts/ralph_orchestrator.py backlog --initiative auth-overhaul
```

## Agent backends

Two worker backends are supported. Pass `--agent` to select:

| Backend | Flag | How workers execute |
|---------|------|-------------------|
| **Codex** (default) | `--agent codex` | `codex exec --full-auto` in Docker containers |
| **OpenCode** | `--agent opencode` | `opencode run` via OpenCode agent API |

The orchestrator abstracts the agent backend. Each worker gets its own worktree and fresh session per task. Agent-specific details are configured in `repo_profile.json`:

```json
{
  "agent": "opencode",
  "opencode_model": "anthropic/claude-sonnet-4-6",
  "opencode_api_url": "http://localhost:3000"
}
```

For Codex Docker workers:
```json
{
  "agent": "codex",
  "docker_image": "codex-worker:latest",
  "codex_flags": "--full-auto"
}
```

## Run management

Runs are organized by **initiative** — a named grouping for related work:

```
runs/
├── auth-overhaul/
│   ├── backlog.md                    # accumulated improvements across runs
│   ├── 20260311-143022-a1b2c3/      # run 1
│   │   ├── loop_state.json
│   │   ├── workers.json
│   │   ├── events.jsonl
│   │   ├── rounds.jsonl
│   │   ├── TODO.md
│   │   ├── RUN_RESULT.md
│   │   ├── retro.md                  # run retrospective
│   │   └── logs/
│   └── 20260312-091500-d4e5f6/      # run 2 (follow-up)
│       └── ...
├── api-rate-limiting/
│   ├── backlog.md
│   └── 20260313-100000-g7h8i9/
│       └── ...
└── index.json                        # initiative registry
```

List runs:
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py runs
python lightweight-ralph-loop/scripts/ralph_orchestrator.py runs --initiative auth-overhaul
```

Resume a specific run:
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py run \
  --resume --run-dir runs/auth-overhaul/20260311-143022-a1b2c3
```

## Configuration

### `repo_profile.json`

Place in the target repo root or pass via `--profile`:

```json
{
  "agent": "codex",
  "test_cmd": "pytest",
  "test_focused_cmd": "pytest {changed_files}",
  "lint_cmd": "ruff check .",
  "typecheck_cmd": "mypy .",
  "build_cmd": null,
  "language": "python",
  "docker_image": "codex-worker:latest",
  "codex_flags": "--full-auto",
  "opencode_model": null,
  "opencode_api_url": null
}
```

Auto-detection falls back to sensible defaults based on repo contents (presence of `pyproject.toml`, `package.json`, `Cargo.toml`, etc.).

### Worker count guidance

| Repo size | Recommended workers |
|-----------|-------------------|
| Small (<20 files) | 2 |
| Medium (20-100 files) | 3 |
| Large (100+ files) | 3-4 |

More workers help with independent tasks but add merge overhead for interdependent changes.

## Observability

During a run, check live status:

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py status --run-dir <run-dir>
```

Shows:
- Overall run status and current round
- Worker cards (task, agent, heartbeat, duration, state)
- Completed/remaining task counts
- PR status (if created)

For a browser-based view:

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py dashboard --run-dir <run-dir>
```

Serves a static HTML page polling `workers.json`, `loop_state.json`, and `events.jsonl`.

## Full run (all phases)

For a single command that runs init → plan → (checkpoint) → run → review → report → retro:

```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py full \
  --repo /path/to/repo \
  --prd /path/to/PRD.md \
  --initiative "my-feature" \
  --workers 3
```

## Examples

**"Run a ralph loop against my FastAPI repo with 3 Codex workers"**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py full \
  --repo ~/projects/my-fastapi-app \
  --prd ~/projects/my-fastapi-app/PRD.md \
  --initiative "auth-overhaul" \
  --agent codex \
  --workers 3 \
  --max-rounds 10
```

**"Run with OpenCode agent API instead"**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py full \
  --repo ~/projects/my-fastapi-app \
  --prd ~/projects/my-fastapi-app/PRD.md \
  --initiative "auth-overhaul" \
  --agent opencode \
  --workers 3
```

**"Resume a failed ralph loop"**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py run \
  --resume --run-dir runs/auth-overhaul/20260311-143022-a1b2c3
```

**"List all runs for an initiative"**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py runs --initiative auth-overhaul
```

**"Just generate the plan, I'll review before running"**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py init \
  --repo . --prd PRD.md --initiative "my-feature"
python lightweight-ralph-loop/scripts/ralph_orchestrator.py plan
# review TODO.md, then:
python lightweight-ralph-loop/scripts/ralph_orchestrator.py run
```

## Pause & resume

When you need to stop a run mid-way (session timeout, token limit, etc.):

**Pause the run:**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py pause --run-dir <run-dir>
```

This stops Docker workers, saves a clean paused state, and generates a `RESUME_PROMPT.md` you can copy into a new agent session.

**Generate a resume prompt without pausing:**
```bash
python lightweight-ralph-loop/scripts/ralph_orchestrator.py resume-prompt --run-dir <run-dir>
```

Prints a self-contained prompt with full state, remaining tasks, and exact commands to restart. Also saved to `RESUME_PROMPT.md` in the run directory.

**Resume from the prompt:**
Paste the generated prompt into a new Cursor/Claude/Codex session. The prompt includes all context needed: session paths, current round, completed/remaining tasks, launch command, and post-run steps.

## Common issues

| Problem | Fix |
|---------|-----|
| Worker can't find the task file | Ensure TODO.md tasks reference exact file paths, not vague descriptions |
| Merge conflicts between workers | Reduce worker count or ensure tasks target non-overlapping files |
| Tests pass in worktree but fail on integration | This is expected — focused gates run per-worktree, full gates run post-merge |
| Token usage shows 0 | Check CLI version; older Codex/OpenCode versions don't emit usage data |
| `gh` auth fails inside Docker | Mount `~/.config/gh/` into the container or pass `GH_TOKEN` |
| OpenCode API connection refused | Check `opencode_api_url` in profile or that `opencode` CLI is running |
| Dashboard shows stale data | Dashboard polls files; check that `workers.json` is being written |
| Missing prerequisite error | Install the reported tools; orchestrator checks on startup |

## Safety

- Workers stage files only; commits are made by the orchestrator after gates pass
- No force push — failed rebases surface as blocked events
- Max rounds and max retries prevent infinite loops
- Human checkpoint before worker dispatch prevents wasted cycles on a bad plan
- Worktrees are reset to a clean state at the start of each round
- `Ctrl+C` stops the orchestrator; workers finish their current task then exit
- `git reset --hard` on the integration branch to revert all changes

## Files in this skill

```
lightweight-ralph-loop/
├── SKILL.md                          # this file
├── scripts/
│   ├── ralph_orchestrator.py         # Python orchestrator (init/plan/run/review/report/retro/backlog/status/runs/dashboard/full)
│   └── ralph.sh                      # standalone single-worker loop runner (for manual/debug use; orchestrator manages workers directly)
├── evals/
│   ├── trigger_evals.json            # trigger evaluation queries
│   ├── evals.json                    # functional evaluation prompts
│   └── grading.json                  # grading report from last assessment
├── references/
│   └── prd_reference.md              # link to full PRD for architecture context
└── assets/
    ├── run_result_template.md        # RUN_RESULT.md template
    ├── retro_template.md             # retro.md template
    └── repo_profile_example.json     # example repo_profile.json
```

## Related: skill-creator_v2

This repo also bundles `skill-creator_v2/` — the skill used to create and iterate on this skill itself. Use it to refine `lightweight-ralph-loop` or build new skills following the same methodology.
