# Evidence Packet - 2026-06-08

This is a synthetic public fixture. It shows the kind of bounded evidence the
standup prompt expects without loading a private bucket.

## Narrative Session Summary

Project: OpenTraces examples

The human asked the agent to turn public examples into fixtures with clear tasks and
to keep private run artifacts out of the open repository. The agent reported
that public examples were added for standup, trace capsules, PR intent reports,
Spotlight search packets, skill evaluation, and Trace Intelligence evals. Files
touched included `examples/README.md`, per-example READMEs, JSON fixtures, and a
focused pytest module.

## Trace Capsule

Title: Parser regression after dependency upgrade

The capsule shows one bounded failure episode. A parser fixture failed with
`expected normalized duration string`, and the capsule declares that system
prompts and reasoning are excluded. It is safe to mention as evidence of the
capsule-sharing workflow, not as a real production failure.

## Pull Request Report

Title: tighten duration parsing

The report explains why the branch exists, names the parser normalization change,
lists regression tests, and points reviewers to trace evidence. It is ready for
review but should not be described as merged.
