---
name: new-plan
description: Create a new plan document in kb/plans/ for the opentraces project. Use this skill whenever the user wants to capture a new feature idea, operational task, bug fix, or refactor as a plan — even if they just say "let's plan X", "add a plan for Y", "write up a plan", "new plan", or describe an idea they want to backlog. The skill interviews the user to reach sufficient depth before writing, then produces a complete plan file following the project's established template and naming convention.
---

# New Plan

This skill creates a well-formed plan document in `kb/plans/` by interviewing the user, fleshing out the idea to the depth we hold as standard across existing plans, then committing the file.

Good plans in this project are not just task lists. They capture *why* something matters, draw a clear architectural picture, define acceptance criteria, and record the decisions made during planning so future maintainers understand the reasoning. Every plan you write should reach that bar.

---

## Step 1: Determine the next plan number

Scan `kb/plans/` for files matching `NNN-*.md`. Find the highest NNN and increment by 1, zero-padded to three digits. Gaps are fine — do not renumber. The next available number as of the last scan was **033**, but always recheck live.

---

## Step 2: Quick intake

Ask the user one opening question to capture the raw idea if they haven't stated it yet. Something like: *"What's the plan about? Give me the rough idea and I'll take it from there."*

If they've already described the idea in their message, skip asking and move to Step 3.

---

## Step 3: Classify and set initial fields

From what the user shared, determine:

- **type** — `feat` / `ops` / `fix` / `refactor` / `ref`
- **status** — start at `proposed` unless the user says it's scoped and ready to go (`backlog`)
- **title** — `"<type>: Descriptive Title"` — title-case, concise, noun-phrase preferred
- **summary** — one sentence describing the deliverable (what ships), not the problem

Tell the user what you've inferred for these fields so they can correct anything before you go deeper.

---

## Step 4: Flesh out the idea with targeted questions

This is where you earn the plan's depth. The goal is to understand the idea well enough to write all mandatory sections without further prompting.

Use the **grill-me** pattern: ask one focused question at a time, wait for the answer, then ask the next. Don't front-load all questions at once. Move through these areas in order, skipping any that are already clear from context:

1. **Problem Frame** — Who is affected and what degrades without this? Is there urgency or is this pure backlog? Any existing evidence (bugs, user feedback, observed failure)?

2. **Scope boundary** — What's explicitly NOT included? This prevents scope creep later and is mandatory in every plan. Push the user to name at least two exclusions.

3. **Architecture** — How would the pieces fit together? New modules/files? Changes to existing ones? Any protocol/interface changes (schema, CLI commands, parsers)?

4. **Requirements** — What does success look like in testable terms? Try to get at least 3 concrete, pass/fail requirements.

5. **Effort signal** — Quick (<1h), Short (<4h), Medium (<1d), or Large (>1d)? If unsure, ask them to break it into milestones and estimate each.

6. **Related plans or research** — Does this depend on or extend any existing plan? Any `kb/br/` research files that informed this?

If the user has already answered some of these in their initial message, don't re-ask — extract what you can and only fill gaps.

---

## Step 5: Write the plan

With enough information gathered, write a complete plan file to:

```
kb/plans/NNN-kebab-slug.md
```

Where `NNN` is the next sequential number and `kebab-slug` is a lowercase, hyphenated summary of the title.

### Mandatory frontmatter

```yaml
---
title: "<type>: Title"
summary: "One sentence describing the deliverable"
type: feat | ops | fix | refactor | ref
status: proposed | backlog
date: <today's date YYYY-MM-DD>
related_research:
  - kb/br/NN-slug.md   # only if applicable
---
```

### Mandatory sections

Every plan must contain all of these sections. Write them in order:

**# Title** (matches frontmatter title minus the type prefix)

**## Overview**
Two to three sentences. The reader should understand scope and outcome without reading further.

**## Problem Frame**
Why this matters, who is affected, what degrades without it, and why now (or why backlog). Ground in evidence, not assumptions.

**## Requirements Trace**
Numbered list: `R1.`, `R2.`, `R3.` ... Each requirement must be pass/fail verifiable. Avoid "should be fast" — prefer "p95 latency < 200ms". Aim for at least 3.

**## Scope Boundaries**
"No X" statements. Minimum two exclusions. This section is mandatory — it draws the line and prevents scope creep.

**## Context & Research**
Links to related plans, `kb/br/` files, external references. One sentence per item explaining what it contributes. If none exist yet, note "No prior research — informed by [source]."

**## Architecture**
How the pieces fit together. Include:
- ASCII diagram if there's a data flow or component relationship worth showing
- Component/file responsibility table:

```
| Component | Responsibility |
|-----------|---------------|
| `path/to/file.py` | ... |
```

**## Implementation Units**

Numbered units with checkboxes. Each unit is a discrete, deliverable chunk. Use `[ ]` for all units (plan is not started):

```
- [ ] **U0 — Name**: What this unit delivers. *Effort: Quick/Short/Medium/Large*
- [ ] **U1 — Name**: ...
```

Aim to have at least 3 units. Large plans should have 6-10.

**## Files to Modify**

```
| File | Changes |
|------|---------|
| `path/to/file.py` | Description |
```

Include new files (mark as `[new]`) and existing files being changed.

**## Verification**

Numbered checklist. Each item is concrete and independently checkable:

```
1. `pytest tests/...` passes
2. `opentraces <command>` produces expected output
3. ...
```

**## Decision Audit Trail**

Table of key decisions made during planning:

```
| # | Phase | Decision | Classification | Principle | Rationale |
|---|-------|----------|---------------|-----------|-----------|
| 1 | Design | ... | Architecture | Simplicity | ... |
| 2 | Scope | Excluded X | Scope | Focus | X is tracked in plan NNN |
```

Aim for at least 2-3 entries. Record what you chose AND what you ruled out.

---

## Step 6: Confirm before writing

Before writing the file, show the user a brief summary of what you're about to create:

> Plan **NNN** — `<type>: Title`
> Status: `proposed` | Type: `feat`
> ~N implementation units, effort: Short/Medium/Large
> File: `kb/plans/NNN-slug.md`
>
> Ready to write?

If they confirm (or say "yes", "go ahead", "looks good"), write the file. If they want changes, adjust first.

---

## Depth calibration

When in doubt about how much detail to include, look at the existing plans in `kb/plans/` for reference. Plans 010, 013, 015, 020, 026, 031 are good examples of thorough, well-structured plans. Plans that are too thin feel like feature requests; plans at the right depth feel like a blueprint someone else could pick up and execute from.

The key signal: after reading the plan, can a different engineer implement it without needing to ask clarifying questions? If yes, it's deep enough.

---

## Conventions reminder

- File names: `NNN-kebab-slug.md` (three-digit zero-padded)
- Title prefix must match type: `feat:`, `ops:`, `fix:`, `refactor:`, `ref:`
- Effort labels: Quick (<1h) / Short (<4h) / Medium (<1d) / Large (>1d)
- Plans reference each other by number: "see plan 015", "depends on plan 006"
- `related_research` paths are relative: `kb/br/NN-slug.md`
- Today's date goes in the `date` field — never leave it blank
