# Troubleshooting

Start with `rsi doctor`. It checks Python, SQLite and the trigram tokenizer, the home
directory and its permissions, the config, `projects.json`, the sessions directory, the
generator, the judge, the index, the lock and the quota headroom, and it says which
learning stages are degraded.

```sh
rsi doctor            # human readable, exit 1 if anything failed
rsi doctor --json     # same checks, machine readable
rsi log -n 20         # what the learner did and why
```

## Nothing is being learned

1. `rsi log` — read the last rows. `status` is one of `learned`, `nothing`, `filtered`,
   `gated`, `duplicate`, `disabled`, `eval`, `generator_failed`, `locked`, `error`.
2. `filtered` means the session was below `min_tool_calls` / `min_user_turns`. A question
   that was answered in one turn is a conversation, not a lesson; lower the minimums if
   your sessions are genuinely short.
3. `duplicate` means the same session id with the same fingerprint was already processed.
   Use `/learn` (which forces) or `rsi learn <file> --force`.
4. `gated` means the judge said no. Look at the `gate` scores in the log: just under the
   threshold is a calibration decision, not a bug.
5. `generator_failed`: the message carries the tail of stderr. Usually a model id that
   `pi --model` does not accept, or an expired credential.
6. Nothing at all in the log: the extension did not fire. Check `pi list` shows pi-rsi,
   and run `/rsi` — if that errors, the extension is not loaded or `python3` is missing.

## A fact is wrong, or a skill is bad

```sh
rsi facts --all          # ids, dates, status
rsi forget f-1a2b3c4d    # retire, with a reason
rsi skills               # what is installed
rm -rf ~/.pi/rsi/skills/learned/<name>
```

Retiring keeps the line with `status = "retired"`. Overwrites leave a copy under
`state/snapshots/`. Nothing learned is invisible or unrecoverable.

## The same fact keeps coming back

Retiring is not blocking. If a session genuinely re-establishes the fact, the learner will
record it again — that is usually correct. If it is not, the session digest probably still
contains the old conclusion: retire the fact with a reason that names the correction, and
the reason is shown to the next run in the facts block.

## `locked` or `queued`

One learner runs at a time (`flock` on `state/learn.lock`, released by the kernel when the
process exits, so a stale lock needs no cleanup). `rsi learn <file>` queues its request in
`state/queue/` before it waits for the lock, and the run holding the lock works through
the whole queue, so a session that ends during another run is learned from afterwards,
not lost. `rsi learn` prints `queued` when it gave up waiting (900 s, `--wait`) and left
the request for the next run, and `taken` when another run picked it up.

`locked` now comes only from `--dry-run`, `--no-judge` or `--model` runs, which bypass
the queue. Requests left in `state/queue/` by a crashed run are drained at the next pi
session start, or now with `rsi learn --drain`. A queue that never empties means a run is
hanging: check for a stuck `pi` process from the generator.

## Recall returns nothing

* The index is empty: `rsi index --all` (or `rsi reindex`).
* The query is under three characters: trigram FTS cannot answer it, and the `LIKE`
  fallback only sees the current project's turns.
* The session was recorded under a different `cwd`: sessions are grouped by the exact
  startup directory. `rsi stats` prints the projects the index knows about.
* The turn was truncated at `max_turn_chars`. Raise it and `rsi reindex`.

## Facts are not in the prompt

`rsi context --cwd "$PWD"` prints exactly what a session would receive: the resolved
project, the fact block and the skills pi would discover. If the project is missing, the
working directory is not in `projects.json`, so only `global` facts apply.

## `pi install` succeeded but nothing happens

* `pi install` does not add a package's `bin` to `PATH`. The extensions call
  `<PACKAGE_ROOT>/bin/rsi` themselves; if you see `rsi: command not found` in a shell,
  that is expected.
* Extension loading is per package; check `pi list` and `/reload`.
* `rsi doctor` from a shell is a different environment from the one pi spawns. If doctor
  passes but `/rsi` fails, `RSI_PYTHON` or `PATH` for `pi` is the difference.

## Doctor says the SQLite build lacks trigram

The interpreter pi-rsi runs under was built without the trigram tokenizer (it needs SQLite
≥ 3.34 and FTS5). Point `RSI_PYTHON` at a normal Python 3.11+ build; on macOS the
system `python3` from Homebrew or python.org both work. `rsi doctor` prints the failing
probe so you can reproduce it in one line.

## A commit fails the secret scan

`secret-scan.yml` runs gitleaks over the commits a pull request adds. To reproduce exactly
what CI saw, run the same binary against the same range:

```sh
ver=8.30.1
curl -sSfL -O "https://github.com/gitleaks/gitleaks/releases/download/v$ver/gitleaks_${ver}_linux_x64.tar.gz"
tar -xzf "gitleaks_${ver}_linux_x64.tar.gz" gitleaks

git fetch origin main
./gitleaks git --no-banner --redact --verbose \
  --log-opts="--diff-merges=remerge origin/main..HEAD" .
```

`--diff-merges=remerge` is what makes a merge commit visible: without it the merge itself
is skipped and a credential added while resolving a conflict would pass. To scan a working
tree instead of commits, use `gitleaks dir .`, which also picks up files that are not
committed yet.

If the finding is a real credential, rotate it first and then rewrite the branch; deleting
it in a later commit does not clear the check, because each commit is scanned on its own.

If it is a false positive:

* a sample value that a test needs on purpose belongs in `.gitleaks.toml` as an exact
  literal, scoped to `tests/` (`condition = "AND"` with both `paths` and `regexes`), so the
  same string is still caught anywhere else;
* anything else belongs in `.gitleaksignore` as its fingerprint, or on the line itself as a
  `gitleaks:allow` comment. Both are narrower than a path allowlist and survive less well
  than a rule change, which is why they come last.

### A test fixture needs an obvious fake credential

`.gitleaks.toml` already allows the literals the redaction tests use. A new fixture is a
one-line addition to that list; keep the value recognisably fake (`AKIA…EXAMPLE`,
`sk-test-…`) rather than something that looks like a live key, and scope the entry to
`tests/` so a real credential pasted into a source file still fails the scan.

## Upgrading

`pi update --extensions` refreshes the code; state is untouched. If a release bumps
`SCHEMA_VERSION`, the first run of the new version refuses to read the old database and
prints `rsi reindex`. Session files remain the source of truth for the index; facts and
skills are plain files and have never needed a migration.
