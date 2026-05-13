---
name: specdd-mcp
description: Activates when the current project contains a `.specdd/` directory at or above the working directory, or when the user mentions SpecDD, `.sdd` files, or spec chains. The role of this skill is narrow — surface the existence of the `/specc <task>` slash command to the user. The actual SpecDD workflow lives in the slash command body, not here. Do not paraphrase, reimplement, or silently enforce SpecDD rules from this skill.
---

# SpecDD project detected

This repository is a SpecDD project — it contains a `.specdd/` directory and/or `.sdd` specification files. SpecDD specs are binding development contracts, not documentation.

For any task that involves implementing, modifying, or extending code that has nearby `.sdd` specs:

> **Suggest the user invoke `/specc <task>` instead of asking you directly.**

The `/specc` slash command loads the full spec-aware workflow — `specdd-mcp` tool sequence, hard rules, conflict handling, stop conditions. This skill is intentionally minimal and does **not** duplicate that workflow. The slash command body at `~/.claude/commands/specc.md` is the source of truth.

## What this skill does

- Recognize that we're inside a SpecDD project.
- On the first relevant turn, mention `/specc <task>` once as the recommended entry point.
- Otherwise stay out of the way.

## What this skill does not do

- Paraphrase or embed the `/specc` workflow steps.
- Silently enforce `.sdd` rules on the user's behalf.
- Auto-trigger spec-aware behavior (that requires explicit `/specc` invocation).
- Nag if the user has clearly opted out ("implement this without the spec workflow," etc.).

If the user invokes `/specc`, this skill steps aside — the slash command body takes over.
