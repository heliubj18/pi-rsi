# pi-rsi

[![ci](https://github.com/heliubj18/pi-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/heliubj18/pi-rsi/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![node](https://img.shields.io/badge/node-22.19%2B-blue.svg)](package.json)
[![status](https://img.shields.io/badge/status-0.1%20alpha-orange.svg)](CHANGELOG.md)

A self-improvement loop for the [pi](https://github.com/earendil-works/pi) coding agent.

Every session ends with a few things that would have been worth knowing at the start: a
version that broke the build, the flag that actually works, the order in which to triage a
failure, the reason a design was rejected. pi forgets all of it. pi-rsi reads the session
after it ends, decides whether it contains anything reusable, records it as a **fact** or
a **skill**, and puts it in front of the next session. It also records enough about each
run that the effect can be measured instead of assumed.

```
session ends
   │
   ├─ index ── filter ── judge gate ── generate ── judge review ── write
   │        (skip short,      (worth a      (skill +      (scope,        (validate,
   │         already seen)     model call?)   facts)        durable,       scan, quota,
   │                                                          duplicate)    snapshot)
   ▼
next session: facts as a system-prompt section, skills discovered by pi on demand
```

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Examples](#examples)
- [Configuration](#configuration)
- [Commands](#commands)
- [Notes and caveats](#notes-and-caveats)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Status and roadmap](#status-and-roadmap)
- [License](#license)

## Features

**Two kinds of memory, on purpose.**

- **Facts** are one-line statements with a stable id, a scope and a date: a version, an
  endpoint, a constraint, a decision and its reason. They are injected into every session
  that can see them, as a named system-prompt section, so the block is visible in the
  session log rather than lost in prompt assembly.
- **Skills** are procedures: ordered steps, exact commands, failure modes. They are written
  as ordinary `SKILL.md` files and published to pi through `resources_discover`, so they
  cost nothing per turn and are opened only when a task matches.

**A judge gates the expensive part.** A cheap model answers two questions about the
session. If both say no, no generator call happens. If a candidate comes back, a second
judge pass decides its scope, whether it will still be true next month, whether it
duplicates a recorded fact, and whether a recorded fact is genuinely contradicted.

**Recall over your own transcripts.** An SQLite FTS5 index with the trigram tokenizer
matches substrings in English, in identifiers like `pd_e2e`, and in Chinese, without an
embedding model. A `recall` tool returns dated excerpts from the current project, and the
tool description tells the model to verify them.

**Every stage degrades instead of failing.** No judge, no generator, no network, a broken
index, a corrupt line: each has a documented fallback. The loop still runs, and the log
says what was skipped.

**Deterministic checks before anything is written.** Nine redaction rules, a secret scan
that drops a whole candidate, quotas per scope and per day, near-duplicate detection that
treats different numbers as different facts, and a snapshot before every skill overwrite.

**Measurement is built in.** Each session records an `rsi/config` entry with the version,
the state directory and a `configSha` over the config plus the learned-skill tree.
`/rate up|down` records a human verdict. `state/learn.jsonl` records the status and reason
of every run, including the ones that learned nothing.

**No build step, no runtime dependencies.** The extensions are TypeScript loaded directly
by pi and use only `node:*` and `typebox`. The core is Python 3.11+ standard library only,
with its own SQLite index. `pi install git:...` works on a machine that has nothing but pi
and Python.

## How it works

| stage | what it does | when it cannot |
| --- | --- | --- |
| parse | pi JSONL to turns: `[USER]` first, `[ANSWER]` before the steps, every turn redacted and truncated | malformed lines are counted; a session with no user turn is skipped |
| index | upsert into SQLite, replacing a session's turns | learning continues; the index error is logged |
| dedupe | skip a session whose id and fingerprint were already processed | `--force` or `/learn` reprocesses |
| filter | tool calls, user turns, or any recorded feedback | short session is `filtered`, not learned from |
| gate | two questions to the judge | no judge: gate opens; judge error: gate opens and the error is logged |
| generate | one JSON object: skill, facts to add, retire, supersede | `generator_failed`, with the stderr tail in the log |
| plan | validate shape, clip lengths, resolve scope | incomplete entries are dropped with a reason |
| review | scope, durability, duplicate, contradiction, replacement | no judge: retirements dropped, additions forced to project scope |
| write | validate, secret-scan, quotas, snapshot, write | refused entries are logged; other entries still land |

Facts live in `facts.jsonl` (per scope) and are retired rather than deleted, keeping the
date and the reason. Skills live on disk and are independent of the package, so
`pi update --extensions` can never overwrite them.

## Requirements

| requirement | why |
| --- | --- |
| pi (recent) | hosts the extensions and the generator subprocess |
| Python 3.11+ | runs the core; standard library only |
| SQLite 3.34+ with FTS5 and trigram | the index; verified by `rsi doctor` |
| macOS or Linux | the learner takes an `flock`; Windows is not supported |
| a model for the generator | `pi`'s configured default, or an OpenAI/Anthropic-compatible endpoint |

pi-rsi reads two of pi's own locations instead of hard-coding them: `$RSI_HOME` defaults to
a sibling of pi's agent directory (`~/.pi/rsi`, or next to `PI_CODING_AGENT_DIR` if you
moved it), and the session directory follows `PI_CODING_AGENT_SESSION_DIR`, then the
`sessionDir` setting, then `<agent dir>/sessions`.

`rsi doctor` prints one line per requirement and exits non-zero if any of them failed.

## Installation

### From git, the normal way

```sh
pi install git:github.com/heliubj18/pi-rsi@v0.1.0
```

Restart pi, or run `/reload`. `/rsi` reports the state for the current directory and the
`recall` tool appears. The package is pinned to the tag; `pi update --extensions`
reconciles the checkout without moving the ref. pi clones it to
`~/.pi/agent/git/github.com/heliubj18/pi-rsi`; `pi list` prints the path it actually uses.

Nothing runs yet: facts and skills are empty, and learning needs a generator the Python
core can reach. Point it at one once (the `pi` default generator reuses pi's own model, so
there is nothing to configure if pi can already answer a prompt):

```sh
PKG=~/.pi/agent/git/github.com/heliubj18/pi-rsi   # as printed by `pi list`
python3 -I "$PKG/bin/rsi" init                    # ~/.pi/rsi/config.toml, projects.json
$EDITOR ~/.pi/rsi/projects.json                   # map your repositories to project names
$EDITOR ~/.pi/rsi/config.toml                     # pick a generator, optionally a judge
python3 -I "$PKG/bin/rsi" doctor
python3 -I "$PKG/bin/rsi" index --all
```

`pi install` does not put a package's `bin` on `PATH`, so the CLI is reached by absolute
path, exactly as the extensions call it. `RSI_PYTHON` overrides the interpreter
(`python3` is what they use by default).

### From a checkout

For development, or when you would rather keep a working copy. The result is the same
package; only the source and the update path differ.

```sh
git clone https://github.com/heliubj18/pi-rsi
cd pi-rsi
pi install .            # or: pi -e .  for one invocation only
python3 -I bin/rsi init
```

With a checkout you may also use `uv` (`uv sync`, then `uv run rsi …`); it is a
development convenience, not a requirement. Nothing in the runtime needs it.

### Verify the installation

```sh
python3 -I "$PKG/bin/rsi" doctor     # or: uv run rsi doctor from a checkout
```

A healthy setup looks like this. `warn` lines are degradations, not failures:

```
ok   python       3.12.4
ok   sqlite       3.50.4 with FTS5 trigram
ok   home         ~/.pi/rsi
ok   config       ~/.pi/rsi/config.toml
ok   projects     2 projects
ok   sessions     1841 files in ~/.pi/agent/sessions
ok   generator    pi (/usr/local/bin/pi), model default
warn judge        no judge: every eligible session costs one generator call
                  -> configure [judge] to gate and review what is learned
ok   state        12 active facts, 3 learned skills
ok   recall       1841 sessions, 26073 turns, schema v1
ok   lock         not held
```

### Uninstall

```sh
pi remove git:github.com/heliubj18/pi-rsi    # the source you installed from
pi list                                     # confirm it is gone
```

`pi remove` accepts the same source string, or the path you installed from
(`pi remove ~/src/pi-rsi`), and deletes the extension, skill and command registrations plus
the matching entry in `settings.json`. For a git source it also deletes the clone under
`~/.pi/agent/git/`. Running pi again shows no `/rsi`, no `recall` tool, and no
`rsi_facts` section in the system prompt.

Your memory is not touched, deliberately: `~/.pi/rsi` (facts, learned skills, index,
run log) survives an uninstall, so reinstalling resumes where you left off. Delete it when
you want a clean slate:

```sh
rm -rf ~/.pi/rsi        # every fact and skill this package ever wrote
```

There is no `rsi uninstall` command on purpose; the state directory is the only thing
pi-rsi owns, and `rm` is a better interface for it than a program that might get it wrong.

## Quick start

```sh
# 1. Create the state directory, config and project map.
uv run rsi init

# 2. Tell it which directories are which project. Longest prefix wins.
cat ~/.pi/rsi/projects.json
{
  "payments-api": ["~/src/payments-api"],
  "payments-infra": ["~/src/payments-infra"]
}

# 3. Optionally add a judge, so most sessions cost nothing but a small call.
$EDITOR ~/.pi/rsi/config.toml

# 4. Index the sessions that already exist.
uv run rsi index --all

# 5. Work as usual. After each session the learner runs in the background;
#    the next session starts with what it kept.
uv run rsi log -n 5
```

Inside pi:

```
/rsi                  rsi: project payments-api, 12 facts, 3 skills, 1841 sessions indexed
/facts                the recorded facts, with ids
/forget f-1a2b3c4d    retire one, with a reason
/learn                learn from this session now, ignoring the duplicate check
/rate up              label this session for later comparison
```

## Examples

### What a fact looks like

```
$ rsi facts
payments-api   f-4b1c9a02 (2026-10-28) The staging cluster is read-only; writes go through the queue at 10.0.4.11.
payments-api   f-2aa8a7de (2026-11-02) Deploys use the image tag from the merge commit, not from HEAD, so a stale deploy can follow a merge.
global         f-bb812045 (2026-11-02) SQLite's trigram tokenizer needs 3.34+; below that, FTS5 has no substring search.
```

The block that reaches the model is bounded and labelled:

```
$ rsi context --cwd ~/src/payments-api
Project: payments-api

Facts you recorded earlier:
- f-4b1c9a02 (2026-10-28) The staging cluster is read-only; writes go through the queue at 10.0.4.11.
- f-2aa8a7de (2026-11-02) Deploys use the image tag from the merge commit, not from HEAD, so a stale deploy can follow a merge.
```

In the session log the same text is a section of the system message, so it can be audited
after the fact:

```json
"message": { "role": "system",
             "sections": { "preamble": "...", "tools": "...", "cwd": "...",
                           "rsi_facts": "<rsi_facts>\nProject: payments-api\n..." } }
```

### Learning from one session, without writing

```sh
$ rsi learn ~/.pi/agent/sessions/--Users-me-src-payments-api--/2026-11-02T09-15-01Z_01a19d2e.jsonl --dry-run
```

```json
{
  "status": "dry_run",
  "project": "payments-api",
  "turns": 21,
  "tool_calls": 186,
  "skill": "stale-image-tag-triage",
  "facts_added": ["Deploys use the image tag from the merge commit, not from HEAD."],
  "facts_retired": [],
  "refused": [],
  "reason": "One procedure worth repeating and one durable fact."
}
```

### Reading the log after a week

```sh
$ rsi log -n 3
learned    payments-api      2026-11-02T09:18:44Z  one procedure, one fact
filtered   payments-api      2026-11-02T11:02:10Z  too short to hold a reusable lesson
gated      payments-infra    2026-11-02T11:40:03Z  the judge saw nothing durable
```

### Recall in a session

```
> why did we drop the first attempt at this?

[recall: "out_xfers stuck at 1"]

2 excerpt(s) from project payments-infra. Dated records of past sessions: verify anything
that may have changed.

### 2026-10-31 — answer
The counter never decrements because the abort path returns before the stats update; the
fix was to settle stats in a defer, not at the end of the happy path.

### 2026-10-30 — user
out_xfers is stuck at 1 again in pd_e2e ...
```

### A judge that costs almost nothing

```toml
[judge]
provider = "openai"
base_url = "https://api.example.com/v1"
model = "small-model"
api_key = "env:JUDGE_API_KEY"
```

With this in place, a session that holds nothing reusable costs one small classification
call instead of one full generation call.

## Configuration

All settings live in `$RSI_HOME/config.toml` (default `~/.pi/rsi/config.toml`), outside the
package, so updates cannot overwrite them. Every key is optional; an empty file is a
working configuration.

```toml
[generator]
provider = "pi"        # pi | openai | anthropic
model = ""             # empty: pi's configured default model

[judge]
provider = "none"      # none | systemone | openai | anthropic
```

- The **generator** writes the candidates. `provider = "pi"` spawns a one-shot
  `pi --print` with tools, extensions and skills disabled, so any model id `pi --model`
  accepts works and no SDK is needed. `openai` and `anthropic` speak their HTTP APIs.
- The **judge** answers a batch of probability and multiple-choice questions. With
  `provider = "none"` the gate opens and the generator has the last word; retirements are
  then ignored, and additions stay at project scope.

Full key reference, secret forms (`env:`, `file:`, literal), environment overrides and the
state layout: [docs/configuration.md](docs/configuration.md).

Where it writes, and what overrides what:

| thing | default | override |
| --- | --- | --- |
| state directory | `~/.pi/rsi`, next to pi's agent directory | `--home`, `RSI_HOME` |
| pi sessions | whatever pi uses: `PI_CODING_AGENT_SESSION_DIR`, the `sessionDir` setting, else `<agent dir>/sessions` | `[sessions] dir`, `RSI_SESSIONS_DIR` |
| python | `python3` on `PATH` | `RSI_PYTHON` |
| pi binary | `pi` on `PATH` | `RSI_PI_BIN` |

No XDG variable is consulted. pi keeps everything under `~/.pi`, and state that lives
away from its host is harder to back up; set `RSI_HOME` when that is what you want.

## Commands

Inside pi:

| command | effect |
| --- | --- |
| `/rsi` | project, fact count, skill count, index size for this directory |
| `/facts` | recorded facts with ids |
| `/forget f-xxxxxxxx` | retire a fact, keeping the reason |
| `/learn` | learn from the current session now, ignoring the duplicate check |
| `/rate up\|down [reason]` | record a verdict used by later comparison |
| `/rsi-version` | installed version and state directory |

In a shell:

| command | effect |
| --- | --- |
| `rsi doctor [--json] [--probe]` | check every dependency; `--probe` asks the judge one question |
| `rsi init [--force]` | create `$RSI_HOME`, `config.toml`, `projects.json` |
| `rsi config` | effective configuration, secrets masked |
| `rsi context [--cwd DIR] [--json]` | exactly what a session in `DIR` would receive |
| `rsi project [--cwd DIR]` | resolve a directory to a project name |
| `rsi index FILE... \| --all \| --dir DIR` | index sessions |
| `rsi reindex` | rebuild the index from the session files |
| `rsi recall QUERY [--cwd DIR] [--all-projects] [--limit N] [--sort relevance\|recent]` | search past turns |
| `rsi learn FILE [--dry-run] [--force]` | learn from one session |
| `rsi facts [--all] [--json]` | list facts; `--all` includes retired |
| `rsi forget ID --reason TEXT` | retire a fact |
| `rsi skills [--json]` | list learned skills |
| `rsi log [-n N] [--json]` | learner runs with status and reason |
| `rsi stats [--json]` | index, facts and run counts |

Exit codes: `0` success, `1` a doctor check failed, `2` usage or configuration error,
`3` a state error (for example an unknown fact id). `--home DIR` overrides `$RSI_HOME` for
one command.

## Notes and caveats

**Cost.** One eligible session costs one generator call, which is a full model call. A
judge does not reduce that; it skips the sessions that would have learned nothing. Facts
also grow the system prompt of every later session in scope, so quotas default to small
values (`max_facts_per_scope = 40`).

**Scope is a real decision.** An unmapped working directory writes to `global`, and global
facts follow you into every repository. Keep `projects.json` accurate. Without a judge,
everything learned is kept at project scope on purpose.

**Learned text is untrusted input.** The generator reads a digest of the session, and a
session can contain text that tries to steer it. Mitigations are structural, not
guarantees: quotas, near-duplicate detection, a secret scan, an independent judge pass,
and a human who can read everything with `/facts`. Review `rsi facts` periodically. A
fact is a sentence in a future system prompt, so treat an unexpected one as a real finding.

**Recall is substring search, not comprehension.** It matches three-character trigrams.
Queries under three characters fall back to `LIKE` over the current project only. Nothing
is embedded, nothing is semantically reranked, and an excerpt is a record of a past
session, not current truth.

**Facts are plain text.** `facts.jsonl` and the learned skills are readable by anyone with
access to your home directory, and the index contains redacted excerpts of your sessions.
Secrets are stripped on the way in by nine patterns, which is not the same as a guarantee;
keep `$RSI_HOME` at mode `700` (the default) on a shared machine.

**State is outside the package, by design.** `pi update --extensions` reconciles the
checkout. Back up `facts.jsonl` and `skills/` if you care about them; `state/` is
rebuildable with `rsi reindex`.

**Version 0.1 is alpha.** The CLI and the on-disk formats are stable enough to use, but a
0.x minor release may change the config keys or the fact schema with a note in
[CHANGELOG.md](CHANGELOG.md). The index schema is versioned and refuses to be read by an
older binary rather than misreading it.

**What it deliberately does not do.** It does not inject transcripts into the prompt
automatically, does not import other harnesses' sessions, does not rewrite the agent's own
rules, and does not call any service other than the generator and judge you configure.

## Troubleshooting

`rsi log` first, then `rsi doctor`, then [docs/troubleshooting.md](docs/troubleshooting.md):
it maps each status (`filtered`, `gated`, `duplicate`, `locked`, `generator_failed`) and
each doctor warning to a cause and a fix.

## Development

```sh
uv sync
uv run pytest -q                                  # 126 cases, no network, no model
uv run ruff check . && uv run ruff format --check .
npx tsc --noEmit                                  # extensions, strict
uv run rsi doctor --home /tmp/rsi-dev
```

The suite uses a temporary `$RSI_HOME` with a fake generator and a fake judge, so the whole
loop is covered without a model call. CI runs the Python suite on Ubuntu and macOS for
3.11 and 3.13, type-checks the extensions, and verifies that every path the package
declares to pi exists.

```
extensions/         pi extensions (TypeScript, no build step)
  lib/rsi.ts        the only place that spawns the CLI
  rsi-context.ts    facts and project into the prompt, skills via resources_discover
  rsi-recall.ts     the recall tool
  rsi-learn.ts      post-session learner, /learn, /forget, /facts
  rsi-trace.ts      rsi/config and rsi/feedback entries, /rate
src/pi_rsi/         Python core (standard library only)
  cli.py            the contract the extensions use
  learner.py        index -> filter -> gate -> generate -> review -> write
  judges/           systemone, openai, anthropic adapters
  generators/       pi, openai, anthropic adapters
  store.py          SQLite + FTS5, the only module with SQL
  facts.py          facts: ids, scopes, dedupe, retirement
  skills.py         SKILL.md validation, snapshots, listing
  guard.py          redaction and secret detection
  doctor.py         the checks behind `rsi doctor`
tests/              pytest suite
docs/               configuration, architecture, troubleshooting
```

Design rationale, invariants and extension points: [docs/architecture.md](docs/architecture.md).

## Status and roadmap

0.1 is usable today: the loop runs end to end, and every stage has a test. Known gaps, in
rough order:

- Skill-level feedback: `skill_blamed` and the `recall_*` thresholds are accepted but
  unused until 0.2.
- A `rsi stats` view that compares configurations by `configSha` and `/rate` verdicts.
- Importers for other harnesses' transcripts (pi sessions only today).
- Optional semantic reranking behind the existing FTS5 search.

Contributions are welcome: open an issue with the session shape and the status from
`rsi log`, or a pull request with a test. Please keep the core standard-library only and
the state outside the package.

## License

Apache-2.0. See [LICENSE](LICENSE).

The two-stage judging pattern and the "fact / skill" split were learned from the
`hyper-agent` project's experiments with self-improving agents; the `systemone` judge
protocol is named after the System One decision protocol used there. No code was copied.
