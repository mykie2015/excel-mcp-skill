# Run Result — {repo_name}

**Run ID:** {run_id}
**Date:** {start_timestamp} → {end_timestamp}
**Total Duration:** {HH:MM:SS}
**Status:** {COMPLETE | PARTIAL | BLOCKED}

## Source

- **Repo:** {owner/repo}
- **Base Branch:** {main}
- **Integration Branch:** {ralph/{run_id}}
- **PR:** {url} | Status: {merged | open | draft}

## PRD Summary

{2-3 sentence summary extracted from PRD.md}

## TODO Completion

| Status | Count |
|--------|-------|
| Completed | {n} |
| Blocked | {n} |
| Skipped | {n} |
| Total | {n} |

### Task Breakdown

| # | Task | Worker | Duration | Status | Retries |
|---|------|--------|----------|--------|---------|
| 1 | {desc} | codex-a | 2m 14s | done | 0 |
| 2 | {desc} | codex-b | 3m 51s | done | 1 |

## Round Summary

| Round | Tasks Attempted | Passed | Failed | Validation | Duration |
|-------|-----------------|--------|--------|------------|----------|
| 1 | 4 | 3 | 1 | tests:pass lint:pass | 8m 22s |
| 2 | 3 | 3 | 0 | tests:pass lint:pass | 6m 11s |

## Worker Performance

| Worker | Tasks Done | Total Time | Active Time | Idle Time | Tokens Used |
|--------|-----------|------------|-------------|-----------|-------------|
| codex-a | 4 | 18m | 14m | 4m | 42,300 |
| codex-b | 3 | 18m | 12m | 6m | 38,100 |
| codex-c | 3 | 18m | 15m | 3m | 45,800 |

## Key Step Durations

| Phase | Duration | % of Total |
|-------|----------|------------|
| Planning (PRD → TODO) | 1m 30s | 4% |
| Worker Execution | 28m 12s | 72% |
| Validation Gates | 4m 08s | 11% |
| Integration/Merge | 1m 22s | 3% |
| PR Review Loop | 3m 50s | 10% |
| **Total** | **39m 02s** | **100%** |

## Code Changes

| Metric | Value |
|--------|-------|
| Files Changed | {n} |
| Files Created | {n} |
| Files Deleted | {n} |
| Lines Added | {n} |
| Lines Deleted | {n} |
| Commits | {n} |

## Token Usage

| Scope | Input | Output | Total | Est. Cost |
|-------|-------|--------|-------|-----------|
| Planning | {n} | {n} | {n} | ${x} |
| Workers | {n} | {n} | {n} | ${x} |
| Review | {n} | {n} | {n} | ${x} |
| **Total** | **{n}** | **{n}** | **{n}** | **${x}** |

## Validation Summary

- **Tests:** {n} suites, {n} passed, {n} failed
- **Lint:** {clean | {n} warnings, {n} errors}
- **Type Check:** {pass | skip | {n} errors}
- **Build:** {pass | skip | fail}

## PR Review

- **Checks:** {n} passed, {n} failed
- **Review Findings:** {n} blocking, {n} follow-up, {n} informational
- **Review Rounds:** {n}
- **Final Status:** {approved | changes_requested | pending}

## Blocked / Escalated Items

{list or "None"}

## Retrospective Notes

{auto-generated observations: longest task, most retried task, validation bottlenecks, merge conflicts encountered}
