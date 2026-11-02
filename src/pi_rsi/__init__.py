"""pi-rsi: a self-improvement loop for the pi coding agent.

The package has two halves that only meet through a process boundary:

* ``src/pi_rsi`` (Python, standard library only) owns state: the session index, the
  facts, the learned skills, the learner and the per-command CLI.
* ``extensions`` (TypeScript, no build step) owns interaction: it injects the facts into
  the system prompt, publishes the learned skills to pi, registers the ``recall`` tool,
  and starts the learner after a session ends.

Keeping the halves apart means the learner can be inspected, run and tested without pi,
and a failure in either cannot break the other.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
