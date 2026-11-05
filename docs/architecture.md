# Architecture

pi-rsi has two halves that share one contract: TypeScript extensions that live inside pi,
and a Python core that owns every decision and all state.

```
 pi session
 ├── rsi-context.ts   before_agent_start      facts + project into the prompt
 │                    resources_discover     learned skills on disk
 ├── rsi-recall.ts    recall tool             dated excerpts from earlier sessions
 ├── rsi-learn.ts     session_shutdown        spawn `rsi learn <file>` (detached)
 │                    session_before_compact  `rsi index <file> --force`
 └── rsi-trace.ts     session_start           `rsi/config` entry: version, home, configSha
                      /rate                  `rsi/feedback` entry
                                │
                                │ argv + JSON
                                ▼
 $RSI_HOME  ◀── src/pi_rsi/cli.py ──◇ learner.py ── judges/ generators/
                (facts, skills,        index → filter → gate → generate
                 store, guard)                          → review → write
```

The boundary is a process, not a library. The extensions never import Python, never parse
a fact file, never open the database. They call `bin/rsi` by absolute path and read JSON
from stdout. That is why the Python side can be tested without pi and the TypeScript side
can be replaced without touching the learner.

`PACKAGE_ROOT` in `extensions/lib/rsi.ts` is derived from the module URL, and `CLI` is
`PACKAGE_ROOT/bin/rsi`. `pi install` does not add a package's `bin` to `PATH`, so an
absolute path is the only portable option.

## Why the split

* **Testability.** The whole loop runs in `pytest` with a fake generator and a fake judge:
  no network, no model, no session files from a real agent.
* **Durability.** `pi update --extensions` reconciles the checkout. Anything written inside
  the package is fair game; everything mutable is under `RSI_HOME`.
* **Portability of the glue.** Extensions use only `node:*` and `typebox`, both already
  present in pi. The Python side uses only the standard library. `pi install git:...`
  works on a machine with nothing installed but Python and pi.
* **One place for SQL.** `store.py` is the only module that knows the schema. There is no
  storage interface with a single implementation behind it: the contract that matters is
  the CLI's, and it is versioned by `--help` and the tests.

## The learning loop

One run, in order, with what happens when a stage fails:

| stage | what it does | on failure |
| --- | --- | --- |
| parse | pi JSONL → turns, `[USER]` first, `[ANSWER]` before steps; every turn redacted and truncated | malformed lines counted, not fatal; a session with no user turn is `nothing` |
| index | upsert into SQLite, replacing a session's turns | a broken index never blocks learning; the error is logged |
| dedupe | skip a session whose id and fingerprint were already processed | `duplicate`, unless `--force` or `/learn` |
| filter | `tool_calls >= min_tool_calls` or `user_turns > min_user_turns`, or any recorded feedback | `filtered` |
| gate | the judge answers two questions | no judge: gate opens; judge error: gate opens, `judge_error` recorded |
| generate | the generator returns one JSON object: skill, facts to add, retire, supersede | `generator_failed`, logged with the stderr tail |
| plan | validate shape, clip lengths, resolve scope | incomplete entries are `dropped` with a reason |
| review | second judge pass: scope, durability, duplicate, contradiction, replacement | no judge: retirements dropped, additions kept and forced to project scope |
| write | validate, secret-scan, apply quotas, snapshot, write | refused entries are logged; the rest still lands |

Invariants that hold on every path:

1. Nothing is written before it passes the secret scan. Learned text ends up in a system
   prompt, so it is held to the same standard as a transcript.
2. A skill overwrite always leaves a snapshot.
3. Quotas are checked at write time, not at plan time.
4. One run at a time (`flock` on `state/learn.lock`). Requests are queued in
   `state/queue/` first, and the run holding the lock drains the queue, so a second run
   waits instead of losing its session.
5. A run always logs its status and reason, including runs that learned nothing.

## The two kinds of memory

**Facts** are sentences with an id (`f-` + 8 hex of a sha1), a scope, a date, a status and
a reason. They are injected into every session that can see them, sorted oldest first and
bounded by `max_inject_chars`. A fact is retired, never deleted: the line stays with
`status = "retired"`, the date and the reason, so a later run can see that something was
believed and why it stopped being true.

Dedupe is deterministic and deliberately conservative: two facts are the same only if
they are near-identical *and* contain the same digit runs. `3.12` and `3.14` are one
character apart and completely different instructions.

**Skills** are directories with a `SKILL.md`, written to `skills/learned/<name>/` or
`projects/<name>/skills/<name>/`, validated against pi's own limits (name ≤ 64, description
≤ 1024, body ≤ 6000 chars and 120 lines) and published through `resources_discover`. pi
loads a skill lazily when a task matches its description, so a large library costs almost
nothing per turn.

Why not put everything in the prompt: the fact block is bounded and always present, so it
must stay small and be true. Procedures are only useful when they apply, so they belong in
a file the model opens on demand.

## Recall

The index is a single SQLite file: a `sessions` table, a `turns` table and an FTS5 virtual
table using the **trigram** tokenizer. Trigrams match substrings, which works for English,
for identifiers like `pd_e2e`, and for Chinese without a word segmenter or an embedding
model. Ranking is BM25 plus the most recent turns from the current project, so "what did
we do about this yesterday" and "where is this string" both return something useful.

Queries shorter than three characters cannot use a trigram index; those fall back to
`LIKE`. That is a fallback, not a feature: it is slow on a large index and correct on a
small one.

Schema changes bump `PRAGMA user_version`. A database written by a newer version is
refused rather than silently misread; `rsi reindex` rebuilds from session files.

## Judging

Every judge call is a batch of named questions answered in one response:

```json
{"questions": {"skill": {"type": "noul", "instructions": "..."}},
 "answers": {"skill": 0.72}}
```

A response with a missing answer, an out-of-range probability, a `true`/`false` where a
number belongs, or a value outside the listed options is rejected as a whole. Partial
trust in a judge is worse than no judge: a backend that has silently changed shape would
otherwise keep returning `nothing`, which looks like a working, cautious learner.

Thresholds are probabilities, not booleans, so the same protocol works with a tiny
classifier and with a large model.

## Measurement

Assumed improvement is not evidence. Three mechanisms make a round comparable:

* Every session writes an `rsi/config` custom entry with `version`, `home`, `cwd`, the
  session's `configSha` and the fact count. `configSha` is a hash over `config.toml`,
  `projects.json` and the tree under `skills/`, so any change to what the loop believes is
  visible in the trace, not inferred from a date.
* `/rate up|down [reason]` writes `rsi/feedback`, which makes a session worth reading even
  if it was short, and gives a later comparison a human label.
* `state/learn.log` records one JSON line per run with `status`, `reason`, `dropped`,
  `refused`, the gate scores and the fingerprint. A learner that learns nothing for a week
  is visible in `rsi log`, not a mystery.

## Failure modes, by design

| symptom | behaviour | where it shows |
| --- | --- | --- |
| no judge configured | gate opens; retirements ignored | `rsi doctor`, `rsi config` |
| judge returns garbage | whole answer voided; run continues without it | `judge_error` in the log |
| generator fails | session marked `generator_failed`; retried on the next run | `rsi log` |
| quota reached | candidate refused with a reason; other candidates still land | `refused` in the log |
| secret in a candidate | candidate dropped whole | `refused` in the log |
| index corrupt | `rsi reindex` | `rsi doctor` |
| SQLite too old | doctor fails before any session is indexed | `rsi doctor` |
| concurrent runs | second request queued; the lock holder learns from it before releasing | log, `state/queue/` |

## Extending it

* **Another judge**: implement `ask(questions) -> answers` in `judges/`, register it in
  `build()`. Keep the question/answer protocol; the learner only reads probabilities.
* **Another generator**: implement `generate(system, prompt) -> str` in `generators/`.
  Return one JSON object; the plan parser tolerates extra keys and drops incomplete ones.
* **Another harness**: write a parser that produces `session.ParsedSession` and a
  `store.SessionRecord`. Nothing downstream knows what a pi session file looks like.
* **Another storage engine**: this is the one place the design deliberately says no. A
  single SQLite implementation is a feature; adding an interface for a second one nobody
  has written would cost more than it saves. Replace `store.py` wholesale if that changes.

## Non-goals for 0.1

Automatic prompt-time recall of transcripts, importing other harnesses' sessions,
semantic embeddings, a server, a UI, and any autonomous change to the agent's own rules.
The learner may add knowledge; it may not rewrite the instructions that constrain it.
