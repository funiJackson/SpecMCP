# SpecDD MCP — Design Document (v2)

Design contract for **specdd-mcp**, a system that gives Claude (and other AI coding agents) deterministic tools and an explicit workflow command for working with [SpecDD](https://github.com/specdd/specdd) `.sdd` specification files.

This document is the source of truth before implementation. Treat it as v0 of the project's own `app.sdd`.

> **What changed from v1:** the project is no longer "an MCP server" alone. It ships three coordinated surfaces — an MCP server (tools), Claude Code slash commands (workflow), and a CLI (bootstrap & install). The slash command is now the **explicit user entry point**; MCP tools are the deterministic backend it drives. See §1.7 and §5.

---

## 1. Design principles

1. **Code-better-than-LLM gate.** Every tool must do something the LLM does poorly, slowly, or inconsistently when left to its built-in file tools. Tools the LLM could trivially do with `Read` are not exposed.
2. **One canonical parser.** All tools that touch a `.sdd` file go through the same parser. There is one `ParsedSpec` shape used by every tool that returns spec data.
3. **Structured errors, never string panics.** Every tool returns `{ ok: true, ... } | { ok: false, error: code, message, details? }`. Claude can branch on `error`.
4. **Read tools are cheap, write tools are explicit.** Read tools never mutate. Write tools require explicit caller intent, validate preconditions (mtime / hash), and return a diff-like summary.
5. **Repo-relative POSIX paths everywhere.** Inputs and outputs use forward-slash paths relative to the repo root, on every platform. Caller passes `repo_root`, or it's auto-detected from the nearest `.specdd/` (preferred) or `.git/` (fallback) ancestor.
6. **Forward-compatible with new sections.** Unknown spec sections are preserved in `unknown_sections` with their line numbers rather than dropped or erroring. SpecDD v1.x may grow.
7. **Explicit invocation over ambient detection.** The agent's workflow is triggered by the user typing `/specc <task>`, not by passive heuristics. MCP tools are passive capabilities; the slash command is the active playbook. This is a deliberate trade: we accept "user must remember to type /specc" in exchange for "no silent failure when SpecDD discipline is needed but skipped."

---

## 2. Scope

### v1 in scope

- **9 MCP tools** covering: parse → query → mutate task → validate → index. Below this, the slash command is missing one of its workflow steps and the v2 slash commands have no backing tools to call.
- **2 slash commands**: `/specc` (the main implementation workflow) and `/specc:bootstrap` (initialize a project).
- **CLI** with at least `bootstrap` and `install-commands` subcommands. The CLI is the human-driven and one-shot path; the slash commands are the agent-driven path.

### v2 in scope (after v1 ships and gets real usage)

- **More MCP tools**: `check_dependencies`, `create_spec`, `add_task`, an MCP-side `bootstrap_project`.
- **More slash commands**: `/specc:audit`, `/specc:draft`, `/specc:status`.
- **MCP prompts** for non-Claude-Code MCP clients (clients that don't have a slash-command equivalent — see §7.1).

### Explicitly out of scope (any version)

- Drafting `Must` / `Must not` rules from context — judgment, belongs in the slash command body, not a tool.
- Deciding spec level (service vs feature vs module) — judgment.
- Semantic conflict resolution between specs — judgment. Mechanical detection is in scope; resolution is the user's call.
- Code-vs-spec drift detection — requires understanding code semantics. Belongs in the slash command body if at all.
- Generating scenarios from code or tests — judgment.
- Anything that would call an LLM from inside a tool. If the implementation needs an LLM, it doesn't belong in the MCP server.

---

## 3. Shared data model

Every tool that returns spec data uses these shapes. Schemas given in TypeScript syntax for clarity; the implementation may be Python or TS (see §8).

### 3.1 `ParsedSpec`

```typescript
interface ParsedSpec {
  // Identity
  path: string;                 // repo-relative, POSIX, e.g. "src/billing/invoice.sdd"
  name: string;                 // value of "Spec: ..." line
  level: SpecLevel;             // inferred from filename suffix or location

  // Raw fallback
  raw: string;                  // full original file content
  line_count: number;
  encoding: "utf-8";            // only utf-8 is supported in v1; reserved for future
  parser_version: string;       // semver of the parser that produced this

  // Sections — present only when the section exists in the source.
  // String fields keep their literal text. Positions live in `positions`.
  platform?: string;
  purpose?: string;
  structure?: Array<{ path: string; description: string }>;

  owns?: string[];
  can_modify?: string[];
  can_read?: string[];
  references?: string[];

  must?: string[];
  must_not?: string[];
  depends_on?: string[];
  forbids?: string[];

  exposes?: string[];
  accepts?: string[];
  returns?: string[];
  raises?: string[];
  handles?: string[];

  tasks?: ParsedTask[];
  scenarios?: ParsedScenario[];
  examples?: string[];
  done_when?: string[];

  // For every known section that appears in the file, the line span of its
  // header → next section header. Lets downstream tools point at exact
  // locations without re-parsing.
  positions: Record<KnownSection, { start_line: number; end_line: number }>;

  // Forward-compat: any section name not recognized.
  unknown_sections?: Array<{
    name: string;
    lines: string[];
    start_line: number;
    end_line: number;
  }>;
}

type SpecLevel =
  | "app" | "module" | "feature" | "service" | "model"
  | "adapter" | "api" | "component" | "job" | "event" | "policy"
  | "custom"          // user-defined level inferred from filename
  | "unknown";        // parser could not infer

type KnownSection =
  | "spec" | "platform" | "purpose" | "structure"
  | "owns" | "can_modify" | "can_read" | "references"
  | "must" | "must_not" | "depends_on" | "forbids"
  | "exposes" | "accepts" | "returns" | "raises" | "handles"
  | "tasks" | "scenarios" | "examples" | "done_when";
```

**Design note on `SpecLevel`:** the README explicitly says spec levels are conventions, not rules. `"custom"` lets users name their own level via a filename suffix the parser recognizes structurally but doesn't have semantics for. `"unknown"` means the parser couldn't tell at all — a parser problem, not a user choice.

### 3.2 `ParsedTask`

```typescript
interface ParsedTask {
  state: "open" | "done" | "skipped" | "blocked" | "needs_decision";
  state_symbol: " " | "x" | "-" | "!" | "?";
  text: string;                 // task text, stripped
  id?: string;                  // e.g. "#1" if present
  line: number;                 // 1-indexed line number
  indent: string;               // literal leading whitespace, for safe rewrite
  raw: string;                  // the original line verbatim
}
```

### 3.3 `ParsedScenario`

```typescript
interface ParsedScenario {
  name: string;                 // text after "Scenario:"
  steps: string[];              // raw step lines
  start_line: number;           // 1-indexed
  end_line: number;
}
```

### 3.4 `SpecChain`

```typescript
interface SpecChain {
  target: string;               // repo-relative target path
  repo_root: string;            // absolute, returned for caller's reference
  chain: ParsedSpec[];          // root-to-target order, parents first
  nearest: ParsedSpec | null;   // last element of chain, or null if none
  malformed: Array<{ path: string; error: string }>;  // specs that failed to parse but were in the chain
}
```

### 3.5 `Constraint`

Wraps any inherited rule with full provenance.

```typescript
interface Constraint {
  rule: string;                 // the rule text
  source: string;               // repo-relative path of the spec it came from
  line: number;                 // 1-indexed line in that spec
}
```

**Line numbers are mandatory.** Without them, callers can't quote exact provenance, which defeats half the point of merging.

### 3.6 `EffectiveConstraints`

The merged view that `/specc` calls once at the start of every implementation.

```typescript
interface EffectiveConstraints {
  target: string;
  chain_summary: Array<{ path: string; level: SpecLevel }>;  // one-line orientation

  must: Constraint[];
  must_not: Constraint[];
  forbids: Constraint[];
  depends_on: Constraint[];
  done_when: Constraint[];

  // Read context (recommended, not enforced)
  effective_read_scope: Constraint[];

  // The effective write scope. Both literal patterns and expanded paths.
  effective_write_scope: Array<{
    pattern: string;            // the original "Owns:" or "Can modify:" entry
    matches: string[];          // current filesystem matches (snapshot)
    source: string;             // repo-relative path of the spec that granted this
    source_line: number;
  }>;
  write_authority_source: string | null;  // path of nearest spec with Can modify or Owns

  // Tasks aggregated across the chain, with provenance.
  tasks: Array<ParsedTask & { source: string }>;

  // Mechanically detectable disagreements. Populated only when found.
  conflicts: Array<{
    kind:
      | "depends_on_vs_forbids"      // depends_on in one spec, forbids in ancestor
      | "must_vs_must_not"            // contradictory rules across chain
      | "duplicate_parent_rule"       // child restates parent verbatim (drift risk)
      | "task_violates_must_not";     // a task literally restates a forbidden action
    rule_a: Constraint;
    rule_b: Constraint;
  }>;

  // Horizontal references gathered from the chain. Not auto-resolved; just listed.
  references: Array<{ from: string; to: string; line: number }>;
}
```

**Why `conflicts` is structured, not free-form:** the slash command branches on `conflicts.length > 0 → stop and surface`. A flat `Constraint[]` would force the agent to detect conflicts itself, which is exactly the kind of work the tools should remove.

### 3.7 `Result<T>` envelope

```typescript
type Result<T> =
  | { ok: true; data: T; warnings?: string[] }
  | { ok: false; error: ErrorCode; message: string; details?: Record<string, unknown> };

type ErrorCode =
  | "NOT_FOUND"            // file or spec doesn't exist
  | "PARSE_ERROR"          // .sdd file is malformed
  | "OUT_OF_SCOPE"         // path outside repo_root
  | "TASK_NOT_FOUND"       // identifier matched no task
  | "TASK_AMBIGUOUS"       // identifier matched multiple tasks (details.candidates)
  | "STALE_FILE"           // file changed on disk since last parse (details.expected, .actual)
  | "ALREADY_EXISTS"       // target file exists
  | "INVALID_INPUT"
  | "IO_ERROR"
  | "ENCODING_ERROR"       // file is not UTF-8
  | "TOO_LARGE";           // monorepo guardrail tripped (see §3.8)
```

### 3.8 Operational behavior (cross-cutting)

These apply to every tool, written once here:

- **Path normalization.** All paths in inputs and outputs are POSIX forward-slash, regardless of OS. On Windows, the implementation converts at the boundary.
- **Symlinks.** Chain resolution does **not** follow symlinks in ancestor directories. If a symlink is encountered, surface as a `warnings` entry and skip. (Avoids loops and surprise sibling-tree inclusion.)
- **Encoding.** UTF-8 only in v1. BOM is allowed and stripped. Anything else returns `ENCODING_ERROR`.
- **Monorepo guardrail.** Repo-wide scans (`list_tasks` with no `scope`, future `list_specs`) refuse to run when more than `max_specs` `.sdd` files are found (default 1000). Caller must pass a narrower `scope` or override `max_specs`.
- **Concurrency.** Write tools require an `expected_mtime` or `expected_content_hash` token from the most recent parse. Stale tokens return `STALE_FILE`. A per-file lock is held during the actual write. This is belt-and-suspenders: the lock prevents in-process races, the hash check prevents editor/external races.

---

## 4. Architecture: how the surfaces compose

The MCP tools are not invoked in isolation. They exist to be orchestrated by the slash command. The picture:

```
User types:    /specc implement task #2 in invoice service
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  ~/.claude/commands/specc.md   (slash command body)          │
│                                                              │
│  - Preflight: detect .specdd/                                │
│  - Step 1: identify target                                   │
│  - Step 2: get_effective_constraints ────────┐               │
│  - Step 3: confirm task                      │               │
│  - Step 4: check_modification_scope ─────────┤               │
│  - Step 5: implement (Edit/Write, gated)     │   tool calls  │
│  - Step 6: run tests                         │   into MCP    │
│  - Step 7: update_task_status ───────────────┤   server      │
│  - Step 8: validate_spec ────────────────────┘               │
│  - Step 9: report                                            │
└──────────────────┬───────────────────────────────────────────┘
                   │ MCP stdio
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  specdd-mcp (MCP server, FastMCP / TS SDK)                   │
│  Tools dispatch into the parser + filesystem layer.          │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  Project on disk                                             │
│    .specdd/bootstrap.md          (marker + project rules)    │
│    .specdd/bootstrap.project.md  (optional overrides)        │
│    CLAUDE.md / AGENTS.md         (one-liner pointing at      │
│                                   /specc)                    │
│    **/*.sdd                      (the specs themselves)      │
└─────────────────────────────────────────────────────────────┘
```

**The role each layer plays:**

| Layer | What it owns | Trigger |
|---|---|---|
| Slash command | The workflow, hard rules, stop conditions, error-handling playbook | User types `/specc ...` |
| MCP server | Deterministic operations on `.sdd` files + filesystem | Tool call from any MCP client |
| CLI | One-shot human-driven setup (bootstrap, install) | `specdd-mcp <subcommand>` |
| Project files | Spec content, project-specific overrides, agent entrypoints | Read by all of the above |

This separation means: each surface can iterate independently. The slash command can evolve workflow choices without breaking the tool contract. The MCP server can add tools without breaking the slash command's existing calls. The project files are pure data.

---

## 5. Tool catalog — v1

Nine MCP tools. Each tool's section gives: purpose, why-MCP, input, output, errors, edge cases.

### 5.1 `parse_spec`

**Purpose.** Parse a `.sdd` file (or raw content) into a `ParsedSpec`. The foundation every other tool builds on.

**Why MCP.** Other tools need consistent shapes. Public callers may use it to sanity-check drafts before writing. Not expected to be called directly in the `/specc` hot path.

**Input.**
```typescript
{
  // Exactly one of these:
  path?: string;          // repo-relative path to .sdd file
  content?: string;       // raw .sdd content (drafts, validation)
  virtual_path?: string;  // when using `content`, for error messages and level inference
}
```

**Output.** `Result<ParsedSpec>`

**Errors.** `NOT_FOUND`, `PARSE_ERROR` (with `details.line`), `INVALID_INPUT` (neither `path` nor `content`), `ENCODING_ERROR`.

**Edge cases.**
- Empty file → ok, mostly-empty `ParsedSpec`, warning "spec has no `Spec:` line".
- Missing `Spec:` line → still parse the rest, surface as warning.
- Unknown section name → put into `unknown_sections` with line numbers, no error.
- Binary content at `.sdd` path → `PARSE_ERROR` with `details.kind: "binary"`.

---

### 5.2 `resolve_spec_chain`

**Purpose.** Given a target inside the repo, return every `.sdd` file in the ancestor directories from repo root to the target, parsed and in order.

**Why MCP.** This is what `/specc` does on every turn. Doing it via shell `find`/`ls` is verbose and easy to get wrong (e.g. pulling in sibling-tree specs by mistake).

**Input.**
```typescript
{
  target: string;          // repo-relative file or directory path
  repo_root?: string;      // absolute; auto-detected if omitted (.specdd/ preferred, .git/ fallback)
}
```

**Output.** `Result<SpecChain>`

**Errors.** `NOT_FOUND` (target or repo_root), `OUT_OF_SCOPE` (target outside repo_root). `PARSE_ERROR` on individual chain specs is **not** fatal — the broken spec is reported in `malformed`, the rest of the chain is returned.

**Edge cases.**
- Target is itself a `.sdd` file → include it as the last chain element.
- Target is a directory → walk root down to and including that directory.
- Multiple `.sdd` files in one directory → all included. Ordering: by `SpecLevel` precedence (app > module > feature > service > model > adapter > api > component > job > event > policy > custom > unknown), tiebreaking lexicographically. This means in a directory containing both `module.sdd` and `feature.sdd`, the module spec inherits first. Lexicographic order alone would be semantically wrong (`feature` < `module`).
- No specs found → ok with empty chain, `nearest: null`.
- Symlink in ancestor path → skipped, warning emitted.

---

### 5.3 `get_effective_constraints`

**Purpose.** Return the merged view of all `Must` / `Must not` / `Forbids` / `Depends on` / `Done when` / read scope / write scope / tasks / conflicts across the spec chain, with provenance. **This is the highest-value tool — `/specc` calls it once at the start of every implementation task.**

**Why MCP.** Manually merging the chain in LLM context costs tokens and is error-prone. Conflict detection across rules is mechanical. Structured provenance is hard to fake.

**Input.**
```typescript
{
  target: string;
  repo_root?: string;
}
```

**Output.** `Result<EffectiveConstraints>`

**Errors.** Same as `resolve_spec_chain`.

**Edge cases.**
- Two parent specs disagree → both appear in `must` / `must_not` / etc., **and** a structured entry appears in `conflicts`. The caller (the slash command) stops on `conflicts.length > 0`.
- `effective_write_scope` is empty → `write_authority_source: null`. The slash command treats this as "no permission to write anything" until the user clarifies.
- Glob patterns in `Owns:` (e.g. `src/billing/*`) → expanded against the filesystem at call time. Both the original pattern and current matches are returned. Expansion is a snapshot; callers refresh as needed.
- New file Claude is creating (doesn't exist yet) → not in any glob expansion, but the slash command can check the literal pattern when verifying authority.

**Design note.** The provenance fields (`source` + `line`) are critical. When the slash command surfaces a violation, it must be able to say "this conflicts with `src/billing/module.sdd:14`" rather than vaguely "with some inherited rule."

---

### 5.4 `list_tasks`

**Purpose.** Find tasks across one or many spec files, optionally filtered by state, location, or text.

**Why MCP.** Cross-spec scanning + reliable parsing of task state symbols. `Grep` can't distinguish `[ ]` from `[x]` cleanly when scenario text contains incidental brackets.

**Input.**
```typescript
{
  repo_root?: string;
  scope?: string;                // repo-relative path to limit search
  states?: TaskState[];          // default: ["open"]
  text_contains?: string;        // case-insensitive substring
  task_id?: string;              // exact match for "#N"
  include_blocked?: boolean;     // shortcut: adds "blocked" and "needs_decision" to states
  max_specs?: number;            // override the default monorepo guardrail
}

type TaskState = "open" | "done" | "skipped" | "blocked" | "needs_decision";
```

**Output.** `Result<Array<ParsedTask & { spec_path: string }>>`

**Errors.** `NOT_FOUND` (repo_root or scope), `TOO_LARGE` (more than `max_specs` `.sdd` files in scope).

**Edge cases.**
- No tasks match → ok with empty array, not an error.
- A spec has malformed tasks → skip that file, list the path in `warnings`.

---

### 5.5 `update_task_status`

**Purpose.** Atomically change task states in one spec file. Preserves indentation, comments, IDs, line endings, and unrelated tasks.

**Why MCP.** **The single highest-leverage write tool.** LLM find-and-replace on task lines routinely breaks formatting, changes adjacent tasks, or corrupts task IDs. This is the one place where a tool dramatically beats LLM edits.

**Input.**
```typescript
{
  spec_path: string;             // repo-relative
  expected_content_hash: string; // from the latest parse; STALE_FILE if mismatch

  // One or more updates in a single atomic write.
  updates: Array<{
    new_state: TaskState;

    // Identification — exactly one of:
    task_id?: string;            // e.g. "#1"
    task_line?: number;          // 1-indexed
    task_text_prefix?: string;   // fallback; ambiguity returns TASK_AMBIGUOUS
  }>;
}
```

**Output.**
```typescript
Result<{
  spec_path: string;
  applied: Array<{
    task: ParsedTask;            // the task as it now exists
    previous_state: TaskState;
  }>;
  diff: string;                  // unified diff of the entire change
  new_content_hash: string;      // pass back on next update
}>
```

**Errors.** `TASK_NOT_FOUND`, `TASK_AMBIGUOUS` (with `details.candidates: Array<{ line, text, id?, source }>`), `INVALID_INPUT`, `STALE_FILE` (with `details.expected_hash`, `details.actual_hash`).

**Edge cases.**
- An `updates` entry where `new_state` equals the current state → no-op for that entry, recorded in `applied` with `previous_state` matching for transparency.
- File has Windows line endings → preserved.
- Multi-line task (rare) → only the first line's state symbol changes.
- Any single update in the batch fails identification → the whole batch fails. No partial writes.

**Design note (changed from v1 of this doc).** v1 of this design forbade batch updates. Real workflows close multiple tasks at once when finishing a feature. Forcing N round-trips burns tokens and risks inconsistent intermediate state. Batch is now the default. Atomicity is guaranteed by a single file write; the diff shows all changes.

**Design note on `dry_run`.** Removed. The output's `diff` field gives full visibility into what changed; a separate `dry_run: true` mode would double the tool's contract for no real safety benefit. If a caller wants preview without effect, it can read the file again afterward — but in practice the diff is sufficient.

---

### 5.6 `check_modification_scope`

**Status: implemented (PR 5).**

**Purpose.** Given a target file and a proposed list of files to modify, check which are inside the nearest spec's `Can modify:` / `Owns:` and which would overstep.

**Why MCP.** Pre-edit gate, called by `/specc` step 4. Cheap to call before any write, prevents whole categories of agent overreach.

**Input.**
```typescript
{
  target: string;
  proposed_files: string[];      // repo-relative paths
  repo_root?: string;
}
```

**Output.**
```typescript
Result<{
  authority_source: string | null;    // path of nearest spec; null if none
  effective_scope: Array<{
    pattern: string;
    matches: string[];
  }>;
  allowed: string[];                  // proposed_files inside scope
  out_of_scope: string[];             // proposed_files outside scope
  multiple_authorities?: Array<{      // populated only if more than one spec
    spec: string;                     // in the chain claims authority over the
    line: number;                     // same proposed file
    file: string;
  }>;
  reason?: string;                    // human summary when everything is out of scope
}>
```

**Errors.** `NOT_FOUND` (target).

**Edge cases.**
- No nearest spec at all → `authority_source: null`, `allowed: []`, `out_of_scope` is the full proposed list.
- Proposed file is a glob → expanded against the filesystem before checking.
- Proposed file doesn't exist yet (new file) → still check against literal patterns in `Owns:` / `Can modify:`.
- Multiple specs in the chain claim authority over the same proposed file (the README warns against this but doesn't prevent it) → populate `multiple_authorities` so the slash command can surface the ambiguity.

---

### 5.7 `validate_spec`

**Status: implemented (PR 5 single-file, PR 7 cross-spec).** All nine single-file rules run on every call. The three cross-spec rules (`DUPLICATE_PARENT_RULE`, `CONFLICTING_INHERITANCE`, `TASK_VIOLATES_MUSTNOT`) now activate when `check_inheritance: true` is passed with a `repo_root`: the rule resolves the target's chain, merges it via `get_effective_constraints`' machinery, and surfaces the conflicts where the validated spec is the violator. Without `repo_root` (or for raw `content` whose chain can't resolve) cross-spec analysis degrades silently — the single-file rules still run.

**Purpose.** Static check of a single spec file or raw content. Surfaces syntax issues, malformed tasks, unknown sections (warnings), and optionally cross-spec inheritance issues.

**Why MCP.** Deterministic checks. Faster and more thorough than LLM eyeballing. Useful both inline (`/specc` step 8) and as a CI hook.

**Input.**
```typescript
{
  // Exactly one of:
  path?: string;
  content?: string;
  virtual_path?: string;         // when using `content`

  // Optional cross-spec checks:
  repo_root?: string;
  check_inheritance?: boolean;   // default true when repo_root is given
}
```

**Output.**
```typescript
Result<{
  issues: ValidationIssue[];
  summary: { errors: number; warnings: number };
}>

interface ValidationIssue {
  severity: "error" | "warning";
  code: string;                  // stable code, e.g. "INVALID_TASK_STATE"
  message: string;
  line?: number;
  related_spec?: string;         // for inheritance findings (path:line)
  related_line?: number;
}
```

**Validation rules — v1.**

*Errors:*
- `MISSING_SPEC_HEADER` — no `Spec:` line.
- `INVALID_TASK_STATE` — task uses a symbol other than ` x - ! ?`.
- `DUPLICATE_TASK_ID` — same `#N` appears twice in one spec.
- `MALFORMED_SECTION` — section header followed by something the parser cannot interpret.

*Warnings:*
- `MISSING_PURPOSE` — no `Purpose:` (downgraded from error in v1 of this doc; README treats it as a recommendation, not a rule).
- `UNKNOWN_SECTION` — section name not in the canonical list.
- `EMPTY_SECTION` — section header with no content.
- `LONG_SPEC` — file > 80 lines. Configurable.
- `OWNERSHIP_OUTSIDE_DIRECTORY` — `Owns:` references a path that escapes the spec's own directory (`..` or absolute).
- `DUPLICATE_PARENT_RULE` *(only when `check_inheritance: true`)* — a `Must` or `Must not` byte-identical to one in a parent.
- `CONFLICTING_INHERITANCE` *(only when `check_inheritance: true`)* — `Depends on:` matches a parent's `Forbids:`.
- `TASK_VIOLATES_MUSTNOT` *(only when `check_inheritance: true`)* — task text mechanically matches a parent's `Must not` (string-level; false positives accepted, hence warning).

**Errors (tool-level).** `NOT_FOUND`, `PARSE_ERROR`, `INVALID_INPUT`.

**Edge cases.**
- `check_inheritance: true` but no parent specs → ok, no inheritance findings.
- Many unknown sections → many warnings, no errors. By design — SpecDD is extensible.

---

### 5.8 `list_specs`

**Purpose.** Repo-wide (or scoped) index of all `.sdd` files with optional task summaries. The orientation tool for `/specc:status` and `/specc:audit` (v2 slash commands), and for any caller wanting a dashboard-style overview.

**Why MCP.** Doing this via shell finds plus N parse calls is wasteful and ordering is fragile. One tool returns a sorted, deduplicated index that respects the same parser as every other tool. The guardrail (`max_specs`) is also enforced consistently.

**Input.**
```typescript
{
  repo_root?: string;
  scope?: string;                 // repo-relative path to limit search
  levels?: SpecLevel[];           // filter by level
  include_task_summary?: boolean; // default true
  max_specs?: number;             // override the default guardrail
}
```

**Output.**
```typescript
Result<Array<{
  path: string;                   // repo-relative, POSIX
  name: string;                   // from Spec: header
  level: SpecLevel;
  line_count: number;
  task_summary?: {                // present only if include_task_summary
    open: number;
    done: number;
    skipped: number;
    blocked: number;
    needs_decision: number;
  };
}>>
```

**Errors.** `NOT_FOUND` (repo_root or scope), `TOO_LARGE`.

**Edge cases.**
- Empty repo → ok with empty array.
- Malformed spec → skipped, listed in `warnings` with the path. The index keeps moving.
- Output sorted by `path` ascending for stable ordering.

---

### 5.9 `find_ownership_conflicts`

**Purpose.** Detect cases where more than one spec claims the same item via `Owns:`. The SpecDD README explicitly says "only one spec should own a specific item at any given time" — this tool mechanically enforces that invariant.

**Why MCP.** Cross-spec analysis that needs consistent parsing plus glob expansion against the live filesystem. CI-friendly. Used by `/specc:audit` (v2) and exposed as `specdd-mcp validate --ownership`.

**Input.**
```typescript
{
  repo_root?: string;
  scope?: string;
  max_specs?: number;
}
```

**Output.**
```typescript
Result<Array<{
  item: string;                   // the literal pattern, or an expanded path
  kind: "literal" | "glob_overlap" | "glob_vs_literal";
  owners: Array<{
    spec: string;                 // repo-relative path of the spec
    line: number;                 // line of the Owns: entry in that spec
    pattern: string;              // the literal Owns: pattern as written
  }>;
}>>
```

**Errors.** `NOT_FOUND`, `TOO_LARGE`.

**Edge cases.**
- Two specs literally own the same path → `kind: "literal"`.
- One spec owns `src/billing/*`, another owns `src/billing/invoice.ts` → `kind: "glob_vs_literal"`. The literal entry is more specific; surface the overlap so the user can either narrow the glob or remove the literal.
- Two overlapping globs (`src/billing/*` and `src/billing/**/*.ts`) → `kind: "glob_overlap"`, surfacing the intersection.
- Empty repo or no overlaps → ok with empty array.
- Glob expansion is a snapshot (same semantics as `get_effective_constraints`).

**Design note.** Only mechanical overlaps are detected. Semantic conflicts ("this spec owns invoice logic, that one owns billing logic, and invoice *is* billing") are judgment, not parsing.

---

## 6. Tool catalog — v2 (post-v1)

Briefer specs. All promote to v1-shape (input / output / errors / edges) when implementing.

### 6.1 `check_dependencies`

Given a spec path and a proposed list of dependencies (module names, import paths), return violations of any `Forbids:` or `Must not:` in the chain with `Constraint` provenance.

### 6.2 `create_spec`

Scaffold a new `.sdd` file from inputs (level, name, purpose, optional sections). Writes with consistent formatting. Refuses to overwrite. Validates before writing.

### 6.3 `add_task`

Insert a new task into a spec. Inputs: `spec_path`, `text`, optional `id`, optional `after_task_id` for position. Output shape matches `update_task_status`. Requires `expected_content_hash`.

### 6.4 `bootstrap_project` (MCP version)

Mirror of the v1 CLI `bootstrap` subcommand, exposed as an MCP tool so agents in non-Claude-Code clients can run setup. Lower priority than the CLI version because most setup happens interactively or via copy-paste.

---

## 7. Slash command and CLI surface — v1

### 7.1 Slash commands

**`/specc <task>` — the main workflow.**

The full workflow body lives in `commands/specc.md` in this repo (copy of the canonical one). Summary:

1. Preflight — detect `.specdd/`.
2. `get_effective_constraints(target)` → stop on `conflicts`, on `null` write authority.
3. Confirm task with user.
4. `check_modification_scope(target, proposed_files)` → stop on `out_of_scope`.
5. Implement (built-in `Edit` / `Write`, gated by the `allowed` list).
6. Run tests / checks.
7. `update_task_status(...)` with `expected_content_hash`.
8. `validate_spec(path, check_inheritance=true)`.
9. Report.

**`/specc:bootstrap` — initialize SpecDD in the current repo.**

Calls the CLI under the hood (or, in v2, an MCP `bootstrap_project` tool). Drops `.specdd/bootstrap.md`, optional `bootstrap.project.md` stub, `AGENTS.md`, `CLAUDE.md`, and optionally a starter `app.sdd`. Refuses to clobber.

### 7.2 v2 slash commands

- `/specc:audit` — run `validate_spec` across all `.sdd` files in scope, report summary.
- `/specc:draft <kind> <name>` — draft a new spec body, validate it, ask user before writing.
- `/specc:status` — list open tasks across the repo, grouped by spec.

### 7.3 CLI surface

```
specdd-mcp                          # start the MCP server on stdio
specdd-mcp bootstrap [--with-app]   # write .specdd/, AGENTS.md, CLAUDE.md, optional app.sdd
specdd-mcp install-commands         # copy specc.md (etc.) to ~/.claude/commands/
specdd-mcp validate [PATH]          # CI hook: validate one or all specs
specdd-mcp version
```

The CLI is the human-driven path. Slash commands are the agent-driven path. Both share the same underlying functions where possible.

### 7.4 MCP prompts (deferred)

MCP also supports a `prompts` capability — server-defined templates the client can surface. In Claude Code, slash commands cover the same need but with better UX. We do **not** ship MCP prompts in v1.

In v2, we may add MCP prompts as a mirror of `/specc` for non-Claude-Code clients (Continue, Zed, etc.). This is purely a portability story; semantics stay identical.

---

## 8. Implementation choices

Separable from the design. Decide when coding starts.

| Decision | Options | Status |
|---|---|---|
| Language | Python (`mcp` SDK + FastMCP) vs TypeScript (`@modelcontextprotocol/sdk`) | **Resolved (PR 1): Python 3.10+** — concise, easy parsing, packageable via `pipx`. Pydantic v2 for models. |
| Parser style | Hand-written recursive descent vs regex-per-section vs PEG library | **Resolved (PR 1): Regex-per-section** — sections are line-anchored, ~50 stmts in `sections.py`, 100% test coverage. |
| Test corpus | Synthetic fixtures vs scrape `specdd/benchmark` for real specs | **Resolved (PR 1): Both** — 9 committed synthetic fixtures + live clone of `specdd/benchmark` (with vendored snapshot fallback). |
| MCP SDK | `mcp[cli]` (FastMCP) vs raw `mcp` SDK | **Resolved (PR 2): FastMCP** — free schema generation from type hints + decorator-based registration. Pinned `mcp[cli]>=1.0,<2.0`. |
| Entry point | Console script vs `python -m` only | **Resolved (PR 2): Both** — `pyproject.toml` `[project.scripts] specdd-mcp` plus `__main__.py` so `python -m specdd_mcp` also works. |
| Logging transport | stdout, stderr, or file | **Resolved (PR 2): stderr only** — stdout is reserved for the JSON-RPC protocol; writes there corrupt the client. Two named loggers: `specdd_mcp.server` (lifecycle) and `specdd_mcp.tool` (per-invocation). |
| MCP client test strategy | Subprocess + protocol vs in-process FastMCP TestClient | **Resolved (PR 2): Subprocess** — exercises the real stdio path. Inline `asynccontextmanager` (not `pytest-asyncio` fixture) avoids anyio cancel-scope-across-tasks errors. |
| Distribution | PyPI (`pipx install specdd-mcp`) vs npm vs Docker | **PyPI via pipx** for v1; npm packaging is v2 if demand exists. Confirmed in PR 2 (server runs via console script). |
| Versioning | Match SpecDD spec version vs independent semver | **Independent semver** — server iterates independently. Confirmed in PR 2. |
| Slash command install location | `~/.claude/commands/` (user-global) vs `<project>/.claude/commands/` (project-local) | **Default user-global** via `install-commands`; `bootstrap` also drops a project-local copy so cloning the repo brings them along. Confirmed in PR 6. |

---

## 9. Open design questions

Updated answers in **bold**.

1. **Task identifier resolution for `update_task_status`.** **Resolved: support `task_id`, `task_line`, and `task_text_prefix` in `updates[]`. Ambiguity returns `TASK_AMBIGUOUS` with `details.candidates` populated so the caller can retry with a specific identifier. Implemented in PR 4 (`operations/mutate_tasks.resolve_task_identifier`): exactly one identifier per `UpdateRequest` enforced (zero or two-plus → `INVALID_INPUT`); `task_text_prefix` is case-sensitive `str.startswith`; candidates are emitted in source-line order with `{line, id, text, current_state}` — line is the safest disambiguator and the recommended retry mode. `details.identifier` echoes the caller's input verbatim so error UI can include it without extra tracking.**

2. **Glob expansion semantics for `Owns:` / `Can modify:`.** **Resolved: snapshot expansion at call time. Both the literal pattern and the current matches are returned (see `effective_write_scope` shape). Documented that scope is a snapshot; callers refresh as needed. Implemented in PR 3 (`operations/globs.expand_pattern`): POSIX paths only, files-only filtering, `EXCLUDED_DIR_NAMES` honored (no `.venv` / `.git` / `node_modules` matches), AppleDouble metadata excluded, paths escaping `repo_root` silently skipped. Windows backslashes normalized defensively.**

3. **`validate_spec` strictness on unknown sections.** **Resolved: warning, not error. SpecDD intentionally allows extension.**

4. **Cross-spec validation in `validate_spec`.** **Resolved: in v1, opt-in via `check_inheritance: true`. Single-file validation stays fast; the slash command opts in.**

5. **`bootstrap_project` and `bootstrap.project.md` stub content.** **Resolved: write a commented template explaining its purpose so users have a clear hook for project rules. CLI handles this in v1; MCP tool in v2.**

6. **Auto-detection of `repo_root`.** **Resolved: walk up from the target. Prefer `.specdd/`. Fall back to `.git/`. Error (`NOT_FOUND`) if neither found and caller didn't pass `repo_root`. Implemented in PR 2 (`paths.find_repo_root`): two-pass walk so a SpecDD-managed subtree of an outer git monorepo correctly treats the SpecDD root as `repo_root`. `.git` is accepted whether it's a directory or a file (git submodules).**

7. **`dry_run` mode on write tools.** **Resolved: removed. The diff in the success result already gives full visibility. Avoids doubling the contract of every write tool.**

8. **Concurrency / stale-file safety.** **Resolved: every write requires `expected_content_hash` from the most recent parse. `STALE_FILE` returned on mismatch. Per-file lock during write as belt-and-suspenders. Implemented in PR 4 (`operations/hashing.content_hash` + `operations/locks.file_lock` + `operations/mutate_tasks.update_task_status`): SHA-256 over the raw bytes (BOM included); per-file sidecar `<spec>.sdd.lock` acquired with `fcntl.flock(LOCK_EX)` on POSIX and `msvcrt.locking(LK_LOCK)` on Windows; lock holds across read → hash-check → parse → resolve → write so two processes serialize correctly. Write uses temp-file + `Path.replace` for atomicity. `STALE_FILE` details carry `expected_hash`, `actual_hash`, and `path`; whole-batch atomicity guarantees the file is byte-identical when **any** pre-write check fails (stale hash, unresolvable identifier, empty updates). Validated by a real subprocess race test (N=4 workers, exactly one Ok, three STALE_FILE).**

9. **Forward compatibility when SpecDD evolves in-section semantics (e.g. priority markers on tasks).** **New. Proposed: `ParsedSpec.parser_version` lets callers know what they're getting. Surface `MIGHT_BE_NEWER_SPECDD` as a warning when unrecognized in-section markup is detected (e.g. unexpected non-whitespace after a task state symbol).**

10. **Spec naming convention enforcement.** **New. Proposed: don't. The README explicitly leaves this to projects. The parser infers `SpecLevel` from filename suffix and falls back to `"custom"` or `"unknown"`. Validation does not flag non-canonical names.**

---

## 10. Installation and distribution

### 10.1 Installing the server

```bash
pipx install specdd-mcp
```

This puts `specdd-mcp` on `$PATH` with three roles:
- `specdd-mcp` (no args) → starts the MCP server on stdio.
- `specdd-mcp <subcommand>` → CLI utilities (see §7.3).
- The package also ships canonical copies of slash command files.

### 10.2 Registering the MCP server with Claude Code

```bash
claude mcp add specdd specdd-mcp
```

(Or via Claude Code's config UI. The exact command will be in the README.)

### 10.3 Installing the slash command

```bash
specdd-mcp install-commands
```

Copies `specc.md` (and any v2 commands) into `~/.claude/commands/`. Refuses to overwrite without `--force`. Logs each file written.

### 10.4 Initializing a project

```bash
cd my-project
specdd-mcp bootstrap          # writes .specdd/, AGENTS.md, CLAUDE.md
specdd-mcp bootstrap --with-app  # also drafts a starter app.sdd
```

Alternatively, the user can run `/specc:bootstrap` from inside a Claude Code session.

The project's `CLAUDE.md` is intentionally minimal:

```markdown
This is a SpecDD project. For spec-aware implementation, start your request with `/specc <task>`.
See `.specdd/bootstrap.md` for project rules.
```

### 10.5 Versioning

- `specdd-mcp` follows independent semver.
- The MCP tool surface follows semver: new tools or new optional fields are minor bumps, breaking changes to existing fields are major bumps.
- The slash command files are versioned with the package; `install-commands` overwrites only with `--force`, so users who customized their commands keep their edits.

---

## 11. Summary: the v1 contract

**9 MCP tools** (one parser, one data model, structured errors):

```
parse_spec                  — foundation
resolve_spec_chain          — most-frequent query
get_effective_constraints   — the heart of /specc, returns merged rules + conflicts
list_tasks                  — task discovery
update_task_status          — batch, stale-checked, atomic
check_modification_scope    — pre-edit gate
validate_spec               — health check, optionally cross-spec
list_specs                  — repo-wide index, unblocks /specc:status and /specc:audit
find_ownership_conflicts    — mechanical multi-owner overlap detection
```

**2 slash commands**:

```
/specc <task>               — the implementation workflow
/specc:bootstrap            — initialize SpecDD in this repo
```

**CLI subcommands**:

```
specdd-mcp                  — start MCP server
specdd-mcp bootstrap        — initialize a project
specdd-mcp install-commands — install slash commands to ~/.claude/commands/
specdd-mcp validate         — CI hook
specdd-mcp version
```

If these are right, the v2 surface (more tools, more slash commands, MCP prompts for non-Claude-Code clients) is purely additive — same data model, same error shapes, no redesign needed.
