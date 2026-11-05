# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — unreleased

First release: the loop works end to end, with measurement built in.

### Added

- **Secret scan.** `secret-scan.yml` runs gitleaks over the commits a pull request or push
  adds, one commit at a time, with `--diff-merges=remerge` so a merge resolution is scanned
  too. `.gitleaks.toml` extends the default rules and allowlists the fake credentials the
  redaction tests write, scoped to `tests/` and to those exact literals.
- **pi's own locations respected.** `$RSI_HOME` defaults to a sibling of pi's agent
  directory (`~/.pi/rsi`, or next to `PI_CODING_AGENT_DIR` when that is set), and the
  session directory follows `PI_CODING_AGENT_SESSION_DIR`, then the `sessionDir` setting,
  then `<agent dir>/sessions`, so indexing does not silently scan an empty directory after
  pi has been relocated. `[sessions] dir` and `RSI_SESSIONS_DIR` override both.
- **Post-session learning.** `session_shutdown` spawns a detached `rsi learn` that
  indexes the session, filters it, asks a judge whether it is worth reading, generates
  skill and fact candidates, re-judges scope and durability, then writes.
- **Facts.** `facts.jsonl` with stable ids, scope, date, status and reason. Injected into
  later sessions as a named system-prompt section through `before_agent_start`, so the
  block is recorded in the session log rather than lost in prompt assembly.
- **Learned skills.** `SKILL.md` files validated against pi's own limits and published
  through `resources_discover`. Overwrites leave a snapshot.
- **Recall.** A `recall` tool over an SQLite FTS5 index using the trigram tokenizer, with
  BM25 ranking merged with the most recent turns, a `LIKE` fallback for queries under
  three characters, and dated excerpts.
- **Judges.** A batch JSON protocol (`noul`, `choice`) with all-or-nothing validation, plus
  `openai` and `anthropic` adapters that ask the same questions through the chat APIs.
  `provider = "none"` runs the loop unjudged.
- **Generators.** A one-shot `pi --print` subprocess (no SDK, any model id pi accepts),
  plus `openai` and `anthropic` adapters.
- **CLI.** `init`, `doctor`, `config`, `context`, `project`, `recall`, `index`, `reindex`,
  `learn`, `facts`, `forget`, `skills`, `log`, `stats`, with a stable `--json` shape.
- **Commands.** `/rsi`, `/learn`, `/forget`, `/facts`, `/rate`.
- **Trace records.** Each session writes `rsi/config` with the version, the home, and a
  `configSha` over `config.toml`, `projects.json` and the learned-skill tree; `/rate`
  writes `rsi/feedback`. Both are inputs for comparing two configurations later.
- **Safety.** Nine built-in redaction rules applied to every indexed turn and every
  candidate; a candidate matching a secret pattern is dropped whole. Quotas on skills and
  facts. One learner run at a time (`flock`). Strict config parsing: an unknown key is an
  error, not a silently ignored typo.
- **Docs.** `docs/configuration.md`, `docs/architecture.md`, `docs/troubleshooting.md`.
- **Tests.** 126 pytest cases covering the store, parsing, redaction, dedupe, quotas,
  every judge and generator adapter, the full learner loop with fakes, and the CLI.

### Fixed

- The judge now sees what it judges: the gate and contradiction questions carry the
  session digest, and the skill scope question carries the skill. Before, they were asked
  about "this session" with no session attached.
- `[learning] digest_chars` is enforced; the earliest turns are dropped first.
- A session that ends while another one is learning is no longer lost. `rsi learn` queues
  its request in `state/queue/` before it waits (up to 900 s, `--wait`) for the lock, and
  the run holding the lock works through the whole queue before releasing it. Before, the
  second run logged `locked` and the session was never learned from. Requests left by a
  crash or reboot are drained at the next session start (`rsi learn --drain`).
- The durability question asked whether a fact was worth carrying into "unrelated future
  sessions", so the judge dropped facts that only matter in their own repository. It now
  asks about future sessions in the same repository; whether a fact holds everywhere is
  still the separate scope question.

### Known limits

- pi sessions only; no importer for other harnesses.
- Recall is a tool call the model makes, not automatic prompt injection.
- `skill_blamed` and the `recall_*` thresholds are accepted but unused until 0.2.

[0.1.0]: https://github.com/heliubj18/pi-rsi/releases/tag/v0.1.0
