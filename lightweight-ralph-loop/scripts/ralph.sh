#!/usr/bin/env bash
# ralph.sh — Standalone single-worker loop runner
#
# Can be used independently of ralph_orchestrator.py for simple
# one-worker ralph loops without the full orchestration overhead.
#
# ralph_orchestrator.py does NOT call this script; it manages workers
# directly via exec_agent(). This script exists for lightweight
# standalone usage or manual debugging.
#
# Usage:
#   bash ralph.sh <task_description> [max_retries]
#
# Environment variables:
#   RALPH_PROJECT_DIR   — worktree path for this worker
#   RALPH_LOG_DIR       — log directory (default: .ralph/logs)
#   RALPH_WORKER_ID     — worker identity (e.g., codex-a)
#   RALPH_AGENT         — agent backend: codex (default) or opencode
#   CODEX_FLAGS         — extra flags for codex exec (default: --full-auto)
set -euo pipefail

TASK_DESC="${1:?'Usage: ralph.sh <task_description> [max_retries]'}"
MAX_RETRIES="${2:-3}"

PROJECT_DIR="${RALPH_PROJECT_DIR:-$(pwd)}"
LOG_DIR="${RALPH_LOG_DIR:-${PROJECT_DIR}/.ralph/logs}"
WORKER_ID="${RALPH_WORKER_ID:-worker}"
AGENT="${RALPH_AGENT:-codex}"
CODEX_FLAGS="${CODEX_FLAGS:---full-auto}"

mkdir -p "${LOG_DIR}"

slugify() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '-' | head -c 40 | sed 's/-$//'
}

TASK_SLUG=$(slugify "${TASK_DESC}")

build_prompt() {
    cat <<PROMPT
You are worker ${WORKER_ID} in a Ralph Loop.

YOUR TASK (do exactly this, nothing else):
${TASK_DESC}

RULES:
- Read relevant files before modifying them
- Implement the change
- Run any validation specified in the task
- Stage only files you changed: git add <specific files>
- Do NOT commit
- Do NOT use git add -A or git add .
- Output TASK_COMPLETE when done
PROMPT
}

run_agent() {
    local prompt="$1"
    if [ "$AGENT" = "opencode" ]; then
        opencode run "$prompt"
    else
        codex exec ${CODEX_FLAGS} "$prompt"
    fi
}

run_task() {
    local attempt=0

    while [ $attempt -lt "$MAX_RETRIES" ]; do
        attempt=$((attempt + 1))
        local timestamp
        timestamp=$(date +"%Y%m%d-%H%M%S")
        local log_file="${LOG_DIR}/${WORKER_ID}-${TASK_SLUG}-attempt${attempt}-${timestamp}.log"

        echo "[${WORKER_ID}] Task: ${TASK_DESC}"
        echo "[${WORKER_ID}] Attempt ${attempt}/${MAX_RETRIES} (agent: ${AGENT})"

        local prompt
        prompt=$(build_prompt)

        local start_time
        start_time=$(date +%s)

        set +e
        run_agent "$prompt" 2>&1 | tee "${log_file}"
        local exit_code=${PIPESTATUS[0]}
        set -e

        local end_time
        end_time=$(date +%s)
        local duration=$((end_time - start_time))

        if [ $exit_code -eq 0 ]; then
            echo "" >> "${log_file}"
            echo "=== OUTCOME: PASS | worker=${WORKER_ID} | agent=${AGENT} | task=${TASK_DESC} | attempt=${attempt} | duration=${duration}s ===" >> "${log_file}"

            echo ""
            echo "ITERATION: ${attempt}"
            echo "TASK: ${TASK_DESC}"
            echo "STATUS: completed"
            echo "WORKER: ${WORKER_ID}"
            echo "AGENT: ${AGENT}"
            echo "DURATION: ${duration}s"
            echo "NEXT: TASK_COMPLETE"
            return 0
        else
            echo "" >> "${log_file}"
            echo "=== OUTCOME: FAIL | worker=${WORKER_ID} | agent=${AGENT} | task=${TASK_DESC} | attempt=${attempt} | exit=${exit_code} | duration=${duration}s ===" >> "${log_file}"
            echo "[${WORKER_ID}] FAIL — attempt ${attempt} (${duration}s)"
        fi
    done

    echo ""
    echo "ITERATION: ${attempt}"
    echo "TASK: ${TASK_DESC}"
    echo "STATUS: blocked"
    echo "WORKER: ${WORKER_ID}"
    echo "AGENT: ${AGENT}"
    echo "NEXT: EXHAUSTED"
    return 1
}

run_task
