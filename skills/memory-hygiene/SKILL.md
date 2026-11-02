---
name: memory-hygiene
description: Use when a session establishes something durable (a version, endpoint, constraint, decision) that a future session would need, when the user corrects a recorded fact, or when earlier sessions might already contain the answer. Keeps the fact store true and the learned-skill library small.
---

# Memory hygiene

pi-rsi keeps two kinds of memory: **facts** (short statements injected into every
session) and **skills** (procedures loaded on demand). Both are written after a session
ends, by a judge plus a generator. What you do during the session decides what it can see
and how good the result is.

## Before re-deriving something, ask for it

Call `recall` when the user refers to work that is not in the current context:

- "like last time", "the same problem", "上次", "之前那个"
- a bug that smells like one already fixed, in this repository or another
- an error message, an internal identifier, a flag name

Phrase the query as the earlier session would have: `"out_xfers stuck at 1"` finds more
than `"transfer bug"`. Results are dated excerpts. They may be out of date — check the
date, and verify against the code before repeating a conclusion.

Do not call `recall` for general knowledge, for anything in the repository, or to pad a
prompt. It costs a search and returns text you then have to trust.

## Make the durable part explicit

The learner reads a digest of the session. A conclusion buried in four tool calls is hard
to see; a sentence is easy. When something is settled, say it once, plainly:

```
The staging cluster is read-only: writes go through the queue at 10.0.4.11.
Gravity uses the merge commit's image tag, not HEAD; that is why a stale deploy follows a merge.
kv cache offload needs page size >= 16 MiB; below that vLLM refuses to start.
```

Include the number, the version, the identifier, and the reason. "Fixed it" is not a
fact. "Bumped the image tag to match the merge commit" is.

Facts are capped at 400 characters. Two facts that differ only in a number are two
facts; do not merge them.

## What does not belong in a fact

- Anything already written in the repository, in a `README`, or in a config file. The
  learner is told this; do not make it fight you by restating it fresh.
- Secrets. Candidates matching a credential pattern are dropped whole; a fact with a key
  in it is a lost fact and a leaked key.
- One-off paths and timestamps (`/tmp/xyz`, "today's build"). They are true for an hour
  and inject noise for months.
- Opinions about the user, the machine, or a person.

## Correcting what is recorded

Facts are injected with ids. If a fact is wrong:

```sh
rsi facts --all          # id, date, status, text
rsi forget f-1a2b3c4d    # retire it, with a reason
```

Retiring keeps the line and the reason; it is the mechanism for "we believed this and
stopped". Do not retire by editing `facts.jsonl` — the reason is the part that teaches.

If a fact is out of date but the correction is itself durable, state the corrected
version in the session; the next learner run records it, and the old one becomes a
supersede candidate.

## Skills

Skills are written for procedures that took real work to find and that recur: a
triage order, a set of commands, a gotcha in a tool. They are not notes and not
summaries of a session. The library is capped (30 by default); a skill that no task
matches is dead weight.

If a learned skill is wrong, delete the directory under `~/.pi/rsi/skills/learned/`.
Overwrites leave a snapshot under `state/snapshots/`, so this is recoverable.

## Rating

`/rate up` or `/rate down [reason]` on a session that went well or badly. It makes a short
session worth reading, it labels the configuration that produced it, and it is the only
signal that separates "the loop helps" from "the loop runs".
