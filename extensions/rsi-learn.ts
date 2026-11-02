/**
 * rsi-learn: start the learner after a session ends.
 *
 * The learner is a separate process, started detached, so quitting pi is not delayed by
 * a model call and the learner survives the exit. Its output goes to
 * `$RSI_HOME/state/learn.out` and its decisions to `$RSI_HOME/state/learn.jsonl`, which
 * `rsi log` prints.
 *
 * Compaction gets its own hook: the turns that are about to be summarized away are
 * indexed first, so `recall` can still find their exact text later. It is detached and
 * never delays the compaction.
 *
 * `RSI_LEARN=0` disables the automatic run; `RSI_EVAL=1` disables reads and writes
 * entirely, for benchmarking a configuration on the same tasks.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { errorText, learnLog, rsi, rsiDetached } from "./lib/rsi.ts";

const FACT_ID = /^f-[0-9a-f]{8}$/;

export default function (pi: ExtensionAPI) {
	pi.on("session_shutdown", async (event, ctx) => {
		// "reload" keeps the same session going; every other reason ends it.
		if (event.reason === "reload" || process.env.RSI_LEARN === "0" || process.env.RSI_EVAL === "1") {
			return;
		}
		const file = ctx.sessionManager.getSessionFile();
		if (!file) {
			return;
		}
		rsiDetached(["learn", file], learnLog());
	});

	pi.on("session_before_compact", async (_event, ctx) => {
		const file = ctx.sessionManager.getSessionFile();
		if (!file || process.env.RSI_EVAL === "1") {
			return undefined;
		}
		rsiDetached(["index", file, "--force"], learnLog());
		return undefined;
	});

	pi.registerCommand("learn", {
		description: "Learn from this session now, ignoring the duplicate check (rsi learn --force)",
		handler: async (_args, ctx) => {
			const file = ctx.sessionManager.getSessionFile();
			if (!file) {
				ctx.ui.notify("This session is not saved, so there is nothing to learn from.", "warning");
				return;
			}
			rsiDetached(["learn", file, "--force"], learnLog());
			ctx.ui.notify(
				`Learner started. Results: rsi log (or ${learnLog()}). New skills and facts appear after /reload.`,
				"info",
			);
		},
	});

	pi.registerCommand("forget", {
		description: "Retire a remembered fact: /forget f-xxxxxxxx (ids are shown by /facts)",
		handler: async (args, ctx) => {
			const id = args.trim();
			if (!FACT_ID.test(id)) {
				ctx.ui.notify("Usage: /forget f-xxxxxxxx (see /facts)", "warning");
				return;
			}
			try {
				const output = await rsi(["forget", id], { timeoutMs: 20_000 });
				ctx.ui.notify(`${output}. The current session still shows it until /reload.`, "info");
			} catch (error) {
				ctx.ui.notify(`forget failed: ${errorText(error)}`, "error");
			}
		},
	});

	pi.registerCommand("facts", {
		description: "Show recorded facts (ids can be retired with /forget)",
		handler: async (_args, ctx) => {
			try {
				const output = await rsi(["facts"], { timeoutMs: 20_000 });
				const lines = output.split("\n");
				const shown = lines.slice(0, 15).join("\n");
				const more = lines.length > 15 ? `\n… ${lines.length - 15} more (run \`rsi facts\`)` : "";
				ctx.ui.notify(shown + more || "No facts recorded yet.", "info");
			} catch (error) {
				ctx.ui.notify(`facts failed: ${errorText(error)}`, "error");
			}
		},
	});
}
