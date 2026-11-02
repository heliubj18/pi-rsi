# Configuration

One file: `$RSI_HOME/config.toml`, default `~/.pi/rsi/config.toml`. It lives outside the
package, so `pi update --extensions` cannot overwrite it. `rsi init` writes an annotated
copy; `rsi config` prints the effective values with secrets masked.

Every key is optional. An empty file is a working configuration: candidates come from a
one-shot `pi` process and nothing judges them.

```sh
rsi init                      # write config.toml and projects.json
rsi config                    # effective values, secrets masked
rsi doctor                    # check each dependency, and which stages are degraded
```

## `[generator]` — who writes the candidates

| key | default | meaning |
| --- | --- | --- |
| `provider` | `pi` | `pi`, `openai` or `anthropic` |
| `model` | `""` | model id; empty means pi's configured default |
| `base_url` | `""` | required for `openai`/`anthropic`, without a trailing slash |
| `api_key` | `""` | secret reference (see below) |
| `timeout_s` | `180` | hard limit for one generation call |
| `max_output_tokens` | `0` | `0` means the provider's default |

`provider = "pi"` spawns `pi --print --no-session --no-tools --no-extensions --no-skills
--no-prompt-templates --no-context-files` with a fixed system prompt. It costs one model
call, uses whatever credentials pi already has, and needs no HTTP client. Set the model
only if you want something cheaper or stronger than your interactive default.

`openai` uses `/chat/completions` with `response_format: json_object`; `anthropic` uses
`/messages`. Both accept any compatible gateway.

## `[judge]` — who gates and reviews

| key | default | meaning |
| --- | --- | --- |
| `provider` | `none` | `none`, `systemone`, `openai` or `anthropic` |
| `base_url` | `""` | required unless `none` |
| `path` | `/v3/decisions` | path under `base_url`, `systemone` only |
| `model` | `""` | model id for the judge |
| `api_key` | `""` | secret reference |
| `timeout_s` | `30` | hard limit for one judgement call |

`none` is not a degraded mode: the deterministic checks (dedupe, quotas, secret scan,
validation) still run, and the gate simply opens. Retirements and supersessions are
ignored without a judge, and additions are kept at project scope, because deleting a fact
— or promoting one into every repository — on one model's word is how a memory turns into
a liability.

`systemone` is a batch JSON protocol: one request carries named questions, each answered
`noul` (probability in `[0, 1]`) or `choice` (one of the given options). A missing,
out-of-range or unlisted answer voids the whole response — a backend that answers
sloppily cannot pass as a working judge. `openai` and `anthropic` ask the identical
question set through their chat APIs.

## `[judge.thresholds]`

Each is a probability in `[0, 1]`. Raise a gate to learn less, lower it to learn more.

| key | default | decides |
| --- | --- | --- |
| `gate_skill` | `0.5` | is this session worth a generator call for a skill |
| `gate_fact` | `0.5` | same, for a fact; if both gates fail, nothing is generated |
| `fact_durable` | `0.5` | will this still be true in a month |
| `fact_contradicted` | `0.8` | a recorded fact is contradicted (retire) |
| `fact_supersedes` | `0.6` | a candidate restates a recorded fact (duplicate) |
| `scope_global` | `0.8` | a lesson holds outside the project it came from |
| `skill_blamed` | `0.5` | reserved: a skill implicated by negative feedback (v0.2) |
| `recall_relevant` | `0.5` | reserved: rerank a recall hit (v0.2) |
| `recall_inject` | `0.8` | reserved: inject a hit without being asked (v0.2) |
| `recall_recent` | `0.6` | reserved: prefer a recent hit over a relevant one (v0.2) |

## `[learning]`

| key | default | meaning |
| --- | --- | --- |
| `enabled` | `true` | `false` disables the post-session learner (`RSI_LEARN=0` also does) |
| `max_skills_total` | `30` | hard cap on learned skills |
| `max_skills_per_day` | `3` | throttle |
| `max_facts_per_session` | `3` | candidates beyond this are dropped, not queued |
| `max_facts_per_day` | `10` | throttle |
| `max_facts_per_scope` | `40` | hard cap per scope; retire before adding |
| `digest_chars` | `24000` | transcript digest size sent to the generator |
| `min_tool_calls` | `3` | a session below both minimums is not worth reading |
| `min_user_turns` | `2` |  |

Quotas exist because a learner without limits fills a prompt with plausible sentences.
When a quota is hit the run is logged with `refused` and the reason, so the limit is
visible rather than silent.

## `[retrieval]`

| key | default | meaning |
| --- | --- | --- |
| `candidates` | `20` | BM25 candidates per recall |
| `recent` | `8` | latest turns always considered |
| `max_turn_chars` | `6000` | a turn is truncated to this before indexing |
| `max_inject_chars` | `4000` | the fact block's share of the system prompt |

## `[sessions]`

| key | default | meaning |
| --- | --- | --- |
| `dir` | `""` | empty means whatever pi itself uses: `PI_CODING_AGENT_SESSION_DIR`, then the `sessionDir` setting, then `<pi agent dir>/sessions` |

A one-off `pi --session-dir` is invisible to pi-rsi, so pass `RSI_SESSIONS_DIR` for that
run. `rsi doctor` prints the directory it resolved.

## Secrets

`api_key` accepts three forms:

| form | example | notes |
| --- | --- | --- |
| `env:NAME` | `env:OPENAI_API_KEY` | read at call time; never written to disk by pi-rsi |
| `file:PATH` | `file:~/.config/judge` | first line, trimmed |
| literal | `sk-...` | works, but the file then holds the secret |

`rsi config` and `rsi doctor` mask every key to `first4...last2`.

## Environment overrides

Useful in scripts and tests; the file stays authoritative for everything else.

| variable | maps to |
| --- | --- |
| `RSI_HOME` | the state directory (also `--home`) |
| `RSI_SESSIONS_DIR` | `[sessions] dir`, above pi's own setting |
| `RSI_GENERATOR_PROVIDER`, `RSI_GENERATOR_MODEL`, `RSI_GENERATOR_BASE_URL` | `[generator]` |
| `RSI_JUDGE_PROVIDER`, `RSI_JUDGE_MODEL`, `RSI_JUDGE_BASE_URL` | `[judge]` |
| `RSI_LEARN=0` | disable learning (the extension also sets this for its own subprocess) |
| `RSI_EVAL=1` | read nothing, write nothing; used to keep evaluation sessions clean |
| `RSI_PYTHON` | interpreter the extensions use to run the CLI |
| `RSI_PI_BIN` | `pi` binary the `pi` generator spawns |
| `PI_CODING_AGENT_DIR` | not ours: read to place `$RSI_HOME` next to pi's agent directory |
| `PI_CODING_AGENT_SESSION_DIR` | not ours: read to find pi's sessions |

## Where the state directory goes

`$RSI_HOME` defaults to `rsi` as a sibling of pi's agent directory: `~/.pi/rsi` for the
standard layout, or e.g. `/opt/pi/rsi` when `PI_CODING_AGENT_DIR=/opt/pi/agent`. Precedence:

1. `--home DIR`
2. `RSI_HOME`
3. pi's agent directory, if `PI_CODING_AGENT_DIR` is set
4. `~/.pi/rsi`

No XDG variable is consulted. pi itself keeps everything under `~/.pi`, and a package
that splits state away from its host is harder to back up and to reason about; set
`RSI_HOME` if your machine wants the state somewhere else.

## `projects.json`

Maps a project name to the directories that belong to it, so facts and skills can be
scoped to a repository instead of leaking into `global`:

```json
{
  "payments-api": ["~/src/payments-api", "~/work/payments-infra"],
  "notes": ["/srv/notes"]
}
```

Resolution is the longest matching prefix of the session's working directory, with a
`git rev-parse --show-toplevel` fallback. An unmapped directory writes to `global`, which
is also what happens when `projects.json` is missing.

## State layout

```
$RSI_HOME/
  config.toml
  projects.json
  facts.jsonl                  active and retired facts, append-mostly
  projects/<name>/
    facts.jsonl
    skills/<skill>/SKILL.md
  skills/learned/<skill>/SKILL.md
  state/
    recall.sqlite3           index: sessions + turns, FTS5 trigram
    learn.jsonl              one JSON line per learner run
    learn.out                the last run, human readable
    learn.lock               held while a run is in progress
    snapshots/<stamp>/<scope>/<skill>/SKILL.md
```

Nothing mutable lives in the package directory. Deleting `state/` costs the index, which
`rsi reindex` rebuilds from the session files; deleting `facts.jsonl` or `skills/` costs
what was learned there.
