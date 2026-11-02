/**
 * rsi-trace: record which configuration and which memory produced a session.
 *
 * Two kinds of entry, both written with `appendEntry`, so they live in the session JSONL
 * and never reach the model:
 *
 * `rsi/config`    written on every session start: the package version, the project, a
 *                 hash of the configuration and the learned-skill tree, and the fact
 *                 count. Two sessions with the same `configSha` saw the same memory.
 * `rsi/feedback`  written by `/rate`: the human verdict that the learner treats as
 *                 evidence a session is worth reading, and that a later version can be
 *                 compared against.
 *
 * The point is measurement. Without a configuration identity per session, "did the
 * learned skills help" cannot be answered even in principle, because there is nothing to
 * group the sessions by.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { PACKAGE_ROOT, rsiHome } from "./lib/rsi.ts";

function version(): string {
	try {
		const manifest = JSON.parse(readFileSync(join(PACKAGE_ROOT, "package.json"), "utf8"));
		return typeof manifest.version === "string" ? manifest.version : "unknown";
	} catch {
		return "unknown";
	}
}

/** Stable hash over the configuration and the learned skills, so sessions can be grouped. */
function configSha(): string {
	const home = rsiHome();
	const hash = createHash("sha256");
	const files: string[] = [];
	const walk = (dir: string): void => {
		if (!existsSync(dir)) {
			return;
		}
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				walk(full);
			} else if (entry.isFile()) {
				files.push(full);
			}
		}
	};
	for (const name of ["config.toml", "projects.json"]) {
		const file = join(home, name);
		if (existsSync(file)) {
			hash.update(name);
			hash.update("\0");
			hash.update(readFileSync(file));
			hash.update("\0");
		}
	}
	walk(join(home, "skills"));
	files.sort();
	for (const file of files) {
		hash.update(relative(home, file));
		hash.update("\0");
		hash.update(readFileSync(file));
		hash.update("\0");
	}
	return hash.digest("hex").slice(0, 16);
}

function factCount(): number {
	const file = join(rsiHome(), "facts.jsonl");
	if (!existsSync(file)) {
		return 0;
	}
	return readFileSync(file, "utf8")
		.split("\n")
		.filter((line) => line.trim().length > 0 && line.includes("\"status\": \"active\"")).length;
}

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (event, ctx) => {
		if (event.reason === "reload") {
			return;
		}
		try {
			pi.appendEntry("rsi/config", {
				ts: new Date().toISOString(),
				version: version(),
				home: rsiHome(),
				cwd: ctx.cwd,
				sha: configSha(),
				facts: factCount(),
			});
		} catch {
			// Tracing must never interfere with starting a session.
		}
	});

	pi.registerCommand("rate", {
		description: "Rate this session for later analysis: /rate up|down [reason]",
		handler: async (args, ctx) => {
			const [verdict, ...rest] = args.trim().split(/\s+/);
			const rating = verdict === "up" || verdict === "down" ? verdict : "";
			if (!rating) {
				ctx.ui.notify("Usage: /rate up|down [reason]", "warning");
				return;
			}
			pi.appendEntry("rsi/feedback", {
				ts: new Date().toISOString(),
				rating,
				reason: rest.join(" ").slice(0, 500),
			});
			ctx.ui.notify(`Recorded ${rating}. A later learner run uses it as evidence.`, "info");
		},
	});

	pi.registerCommand("rsi-version", {
		description: "Show the installed pi-rsi version and the state directory",
		handler: async (_args, ctx) => {
			ctx.ui.notify(`pi-rsi ${version()} — state in ${rsiHome()}`, "info");
		},
	});
}

