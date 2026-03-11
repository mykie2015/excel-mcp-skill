---
name: skill-creator_v2
description: Create or improve agent skills from concrete workflows. Use when the user wants to build a new skill, refine an existing skill, define trigger phrases and frontmatter, choose scripts/references/assets, or iterate on real examples. Supports cross-agent skills for OpenCode, Codex, Claude Code, Cursor, and any agent implementing the Agent Skills spec. Start with 2-3 concrete use cases, write a lean SKILL.md, test with a few realistic prompts, and improve from feedback. Use the bundled eval and benchmark tooling only when the user explicitly wants deeper validation.
---

# Skill Creator V2

This version is aligned to the workflow in "The Complete Guide to Building Skills for Claude" and adapted for the cross-agent skills ecosystem (OpenCode, Codex, Claude Code, Cursor, and any agent supporting the Agent Skills spec).

The default path is intentionally simple:
1. Start from concrete use cases
2. Plan the minimum useful skill contents
3. Write a lean `SKILL.md`
4. Test with a few realistic prompts
5. Iterate based on what actually failed

Do not default to heavy evaluation harnesses, benchmark loops, or description optimization. Those are optional follow-on tools, not the core workflow.

## Core principles

- Start with 2-3 concrete use cases, not abstract capability lists.
- Optimize for **progressive disclosure** — the 3-level system:
  1. **YAML frontmatter** — always loaded in the agent's system prompt; just enough for the agent to know when to use the skill
  2. **SKILL.md body** — loaded when the agent thinks the skill is relevant; full instructions
  3. **Linked files** (references/, scripts/, assets/) — loaded only when the agent navigates to them on-demand
- Keep the skill portable in content when possible. Put environment-specific requirements in `compatibility` or clearly labeled sections.
- Prefer the smallest skill that reliably solves the user's repeated problem.
- Use scripts only when they add deterministic reliability or remove repeated boilerplate. For critical validations, prefer a script over language instructions — code is deterministic; language interpretation isn't.
- Keep `SKILL.md` under 500 lines (or ~5,000 words) and avoid deep reference trees.
- Do not add extra docs inside the skill folder such as `README.md`, `CHANGELOG.md`, or setup guides. A repo-level README for human visitors is fine when distributing via GitHub — just keep it outside the skill folder.

## Skill categories

Identify which category your skill fits — this shapes the patterns and techniques you should use:

| Category | When to use | Key techniques |
|----------|------------|----------------|
| **Document & Asset Creation** | Creating consistent output (docs, presentations, code, designs) | Embedded style guides, template structures, quality checklists |
| **Workflow Automation** | Multi-step processes needing consistent methodology | Step-by-step workflow with validation gates, iterative refinement loops |
| **MCP Enhancement** | Adding workflow guidance on top of MCP tool access | Coordinating multiple MCP calls in sequence, embedding domain expertise |

## Skill anatomy

Each skill should be a simple folder:

```text
skill-name/
├── SKILL.md
├── scripts/       # optional
├── references/    # optional
└── assets/        # optional
```

### `SKILL.md`

`SKILL.md` must contain YAML frontmatter and Markdown instructions.

Minimal frontmatter:

```yaml
---
name: your-skill-name
description: What it does. Use when the user asks to...
---
```

Rules:
- `name` must be kebab-case, match the folder name, no spaces or capitals
- `description` must include both:
  - what the skill does
  - when to use it (include trigger phrases users would actually say)
- keep `description` under 1024 characters
- **no XML angle brackets** (`<` or `>`) anywhere in frontmatter — frontmatter appears in the agent's system prompt, so angle brackets could inject unintended instructions
- **reserved names**: do not use "claude" or "anthropic" in the skill name

Optional frontmatter fields:
- `license` — use when making the skill open source (e.g., `MIT`, `Apache-2.0`)
- `compatibility` — 1-500 characters indicating environment requirements (product, system packages, network access)
- `allowed-tools` — restrict which tools the skill can access (e.g., `"Bash(python:*) WebFetch"`)
- `metadata` — arbitrary key-value pairs (e.g., `author`, `version`, `mcp-server`, `category`, `tags`)

### `scripts/`

Use `scripts/` for deterministic or repetitive work.

Good reasons to add a script:
- the same code would otherwise be rewritten repeatedly
- the task is fragile enough that freeform generation is unreliable
- validation or packaging is easier to automate

### `references/`

Use `references/` for material that is helpful but not needed on every invocation:
- API docs
- schemas
- domain rules
- examples

Reference files directly from `SKILL.md`, and keep references one level deep.

### `assets/`

Use `assets/` for files that become part of the output:
- templates
- icons
- fonts
- starter code

## Default workflow

Follow this path unless the user clearly wants something different.

### Step 1: Capture concrete use cases

Identify 2-3 realistic tasks the skill should handle.

**Pro tip:** Iterate on a single challenging task until the agent succeeds, then extract the winning approach into the skill. This leverages in-context learning and gives faster signal than broad testing. Expand to multiple test cases after you have a working foundation.

Ask only the minimum questions needed:
- What does the user want to accomplish?
- What would they literally say that should trigger the skill?
- What output should the skill produce?
- What tools, file types, or systems are involved?
- What would success look like?
- Which skill category does this fit? (Document/Asset, Workflow Automation, MCP Enhancement)

If the current conversation already contains enough examples, extract them instead of asking the user to restate them.

### Step 2: Plan the reusable parts

For each use case, decide what belongs in:
- `SKILL.md`
- `scripts/`
- `references/`
- `assets/`

Good planning questions:
- Which instructions are core and should always be present?
- Which details are conditional and should move to `references/`?
- Which repeated operations should become scripts?
- Which output templates belong in `assets/`?

### Step 3: Write the skill

Write `SKILL.md` with:
- clear frontmatter
- direct instructions
- a simple step-by-step structure
- examples of realistic user requests
- common issues and fixes when useful

Prefer this structure:

```markdown
---
name: your-skill
description: ...
---

# Your Skill Name

## When to use this skill

## Instructions

### Step 1

### Step 2

## Examples

## Common issues
```

Writing guidance:
- be specific and actionable — `Run scripts/validate.py --input {file}` not `validate the data`
- include error handling with cause and fix for common failures
- reference bundled resources explicitly — `consult references/api-patterns.md for rate limiting guidance`
- put critical instructions at the top, not buried in the middle
- prefer imperative instructions
- avoid bloated theory that the agent already knows
- move detailed reference material out of `SKILL.md` into `references/`
- for critical validations, prefer bundling a script over language instructions

### Description quality bar

Good descriptions are concrete and triggerable.

Good:

```text
Analyzes Figma files and generates developer handoff notes. Use when the user uploads a .fig file, asks for design specs, component documentation, or design-to-code handoff.
```

Bad:

```text
Helps with projects.
```

## Cross-agent compatibility

Skills follow a shared Agent Skills spec that works across 16+ agents. The same `SKILL.md` can be discovered by OpenCode, Codex, Claude Code, Cursor, and others.

### Discovery paths by agent

| Agent | Project-level | Global |
|-------|--------------|--------|
| OpenCode | `.opencode/skills/`, `.agents/skills/` | `~/.config/opencode/skills/`, `~/.agents/skills/` |
| Codex | `.codex/skills/` | `~/.codex/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |

### Installation via `add-skill`

Skills can be distributed and installed across agents using the `add-skill` CLI:

```bash
npx add-skill <owner>/<repo> --skill <skill-name>
npx add-skill <owner>/<repo> --skill <skill-name> -g -a opencode -a claude-code
npx add-skill <owner>/<repo> --list
```

When creating a skill intended for distribution, keep it in its own directory within a git repository so `add-skill` can install it by name.

### CLI-specific commands in skills

When a skill includes shell commands that differ across agents, use a CLI variable pattern:

```markdown
Supported CLIs:
- Codex: `codex exec --full-auto "$(cat PROMPT.md)"`
- Claude Code: `claude --dangerously-skip-permissions "$(cat PROMPT.md)"`
- OpenCode: `opencode run "$(cat PROMPT.md)"`

Ask which CLI the user is running and substitute accordingly.
```

Avoid hardcoding a single CLI unless the skill is explicitly agent-specific.

## Default testing approach

Testing should match the importance of the skill. Start manually unless the user asks for more rigor.

### Manual first

Create a small test set:
- 3-5 prompts that should trigger
- 3-5 prompts that should not trigger
- 2-3 realistic functional prompts that exercise the workflow

Check:
- whether the skill triggers when it should
- whether it stays quiet on near-misses
- whether the outputs are structurally correct
- whether the user would need to correct the process

This is the default. It matches the guide better than forcing full automation for every skill.

### Success criteria

Define criteria before iterating. Aim for rigor but accept some vibes-based assessment.

**Quantitative targets:**
- Skill triggers on ~90% of relevant queries (run 10-20 test queries)
- Completes workflow in fewer tool calls than without the skill
- 0 failed API/MCP calls per workflow
- Comparable or lower token consumption vs. baseline

**Qualitative targets:**
- Users don't need to prompt the agent about next steps
- Workflows complete without user correction
- Consistent results across separate sessions
- A new user can accomplish the task on first try

**Baseline comparison:** Run the same task with and without the skill. Track tool calls, tokens consumed, error rate, and user interventions. This is the strongest signal your skill adds value.

## Iteration loop

When a test fails, classify the problem:

- **Undertriggering**
  - add better trigger phrases
  - clarify the user intent in the description

- **Overtriggering**
  - narrow the description
  - add clearer scope boundaries

- **Execution issues**
  - improve instructions
  - add missing error handling
  - move repeated fragile steps into scripts

- **Context bloat**
  - cut nonessential prose
  - move details into `references/`

General rule:
- fix the pattern, not just the single example
- avoid overfitting to one test prompt

## Common skill patterns

Choose the pattern that fits your use case. These emerged from early adopters and internal teams.

**Sequential Workflow** — multi-step processes in a specific order. Explicit step ordering, dependencies between steps, validation at each stage, rollback instructions for failures.

**Multi-MCP Coordination** — workflows spanning multiple services. Clear phase separation, data passing between MCPs, validation before moving to next phase, centralized error handling.

**Iterative Refinement** — output quality improves with iteration. Initial draft → quality check (run validation script) → refinement loop → finalization. Explicit quality criteria and know-when-to-stop conditions.

**Context-Aware Tool Selection** — same outcome, different tools depending on context. Decision tree based on file type/size/purpose, fallback options, transparency about choices made.

**Domain-Specific Intelligence** — specialized knowledge beyond tool access. Domain expertise embedded in logic (compliance, security, etc.), action gated behind checks, comprehensive audit trails.

For full examples of each pattern, see `references/The-Complete-Guide-to-Building-Skill-for-Claude.pdf` Chapter 5.

## Troubleshooting common issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Skill won't upload | `SKILL.md` not exactly case-sensitive; YAML missing `---` delimiters; name has spaces/capitals | Rename to exactly `SKILL.md`, fix YAML formatting, use kebab-case name |
| Skill never triggers | Description too vague or missing trigger phrases | Add specific user phrases; ask the agent "when would you use [skill name]?" to debug |
| Skill triggers too often | Description too broad | Add scope boundaries; be more specific; add negative triggers ("Do NOT use for...") |
| Instructions not followed | Instructions too verbose, buried, or ambiguous | Put critical instructions at top; use numbered lists; bundle validation as scripts |
| Slow responses / degraded quality | SKILL.md too large; too many skills loaded | Keep under 5,000 words; move detail to references/; reduce simultaneously enabled skills |
| MCP calls fail | Auth expired, wrong tool names, MCP not connected | Test MCP independently without skill first; verify tool names are exact |

## Updating an existing skill

When improving an existing skill:
- preserve the current skill name unless the user explicitly wants a rename
- snapshot the old version if you need a before/after comparison
- keep compatibility with the skill's current folder structure unless there is a strong reason to change it
- focus first on trigger quality, instruction clarity, and repeated failure modes

## Optional advanced tooling

This skill bundle includes extra scripts for users who explicitly want deeper validation:
- `scripts/run_eval.py`
- `scripts/run_loop.py`
- `scripts/aggregate_benchmark.py`
- `eval-viewer/generate_review.py`
- `scripts/improve_description.py`

Use these only when:
- the user asks for rigorous evaluation
- the skill is high-stakes or widely reused
- the environment supports the required tooling

Do not force this path for ordinary skill creation.

### For agent-specific environments

If you use the advanced tooling in any agent CLI:
- prefer manual testing first
- only escalate to automated trigger evaluation after the skill already works reasonably well
- if the agent CLI cannot reach its configured provider, skip automated evaluation and continue with manual iteration
- when testing cross-agent skills, verify the skill triggers correctly in at least two agents if possible

## Distribution and sharing

When distributing a skill:

1. **Host on GitHub** — public repo, clear repo-level README (outside the skill folder), example usage
2. **Position on outcomes** — "enables teams to set up project workspaces in seconds" not "a folder containing YAML frontmatter"
3. **Installation guide** — show download, install, enable, and test steps
4. **Cross-agent install** — `npx add-skill <owner>/<repo> --skill <name> -g -a opencode -a claude-code`
5. **Versioning** — use `metadata.version` in frontmatter; bump on significant changes

For MCP-enhanced skills, document both the MCP connection and the skill together — users need both to get value.

## Practical checklist

### Before you start
- [ ] Identified 2-3 concrete use cases
- [ ] Tools identified (built-in, MCP, or scripts)
- [ ] Skill category chosen (Document/Asset, Workflow, MCP Enhancement)
- [ ] Planned folder structure

### During development
- [ ] Folder named in kebab-case
- [ ] `SKILL.md` exists (exact case)
- [ ] YAML frontmatter has `---` delimiters, name in kebab-case
- [ ] Description includes WHAT and WHEN with trigger phrases
- [ ] No XML tags (`<` `>`) or reserved names in frontmatter
- [ ] Instructions are specific and actionable
- [ ] Error handling included
- [ ] Examples of realistic user requests provided
- [ ] References linked clearly from SKILL.md
- [ ] Scripts exist only where they materially help
- [ ] No README.md or extra docs inside the skill folder

### Before release
- [ ] Tested triggering on obvious tasks (should trigger)
- [ ] Tested triggering on paraphrased requests (should trigger)
- [ ] Verified doesn't trigger on unrelated topics (should not trigger)
- [ ] Functional tests pass (correct outputs, no errors)
- [ ] Baseline comparison done (fewer tool calls/tokens with skill)
- [ ] If cross-agent: CLI-specific commands use variable patterns
- [ ] If distributable: folder is self-contained, installable via `add-skill`

### After release
- [ ] Tested in real conversations
- [ ] Monitoring for under/over-triggering
- [ ] Iterated based on actual failures, not guesswork
- [ ] Version bumped in metadata

## Reference files

- `references/The-Complete-Guide-to-Building-Skill-for-Claude.pdf` — Anthropic's official 33-page guide covering fundamentals, planning, testing, distribution, patterns, and troubleshooting. Read for full pattern examples and distribution best practices.
- `references/schemas.md` — schemas for eval, grading, and benchmark JSON files
- `agents/grader.md` — expectation grading agent for automated evaluation
- `agents/comparator.md` — blind A/B comparator for skill output quality
- `agents/analyzer.md` — post-hoc analysis and benchmark aggregation

Read the advanced evaluation resources only when you need the automated evaluation path.
