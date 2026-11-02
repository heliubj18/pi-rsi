/**
 * rsi-context: what a new session starts with.
 *
 * Two things are injected, both from the Python core:
 *
 * 1. The active project and its facts, as a named system-prompt section. Named sections
 *    are recorded in the session log (pi writes `systemPromptOptions`), so a transcript
 *    can be checked against what the model was actually told. Mutating `systemPrompt`
 *    instead would work for the model but leave no such record.
 * 2. The learned-skill directories, through `resources_discover`, so pi loads them like
 *    any other skill and the model opens one on demand instead of carrying all of them.
 *
 * Nothing here writes state, and every failure is silent: a broken fact store must not
 * block the prompt. `/rsi` is the way to see whether it is working.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { type ContextPayload, errorText, rsiJson } from "./lib/rsi.ts";

const CACHE_MS = 15_000;
const CAVEAT =
	"Facts recorded in earlier sessions. They may be outdated: verify anything that can have changed before relying on it.";

let cache: { cwd: string; at: number; data: ContextPayload | null } | null = null;

async function load(cwd: string): Promise<ContextPayload | null> {
	const now = Date.now();
	if (cache && cache.cwd === cwd && now - cache.at < CACHE_MS) {
		return cache.data;
	}
	const data = await rsiJson<ContextPayload>(["context", "--cwd", cwd], { timeoutMs: 15_000 });
	cache = { cwd, at: now, data };
	return data;
}

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		cache = null;
		// Warm the cache before the first turn so the prompt hook does not pay for it.
		await load(ctx.cwd);
	});

	pi.on("resources_discover", async (event) => {
		const data = await load(event.cwd);
		const paths = data?.skill_paths ?? [];
		return paths.length > 0 ? { skillPaths: paths } : undefined;
	});

	pi.on("before_agent_start", async (event, ctx) => {
		const data = await load(ctx.cwd);
		if (!data) {
			return undefined;
		}
		const parts: string[] = [];
		if (data.project) {
			parts.push(`Project: ${data.project}`);
		}
		if (data.facts) {
			parts.push(`${CAVEAT}\n\n${data.facts}`);
		}
		if (parts.length > 0) {
			event.systemPromptOptions.sections.rsi_facts = parts.join("\n\n");
		}
		return undefined;
	});

	pi.registerCommand("rsi", {
		description: "Show the pi-rsi state for this directory: project, facts, skills, index",
		handler: async (_args, ctx) => {
			try {
				const data = await load(ctx.cwd);
				if (!data) {
					ctx.ui.notify("rsi: the context command failed. Run `rsi doctor` in a shell.", "error");
					return;
				}
				const facts = data.fact_count === 1 ? "1 fact" : `${data.fact_count} facts`;
				const skills = data.skills.length === 1 ? "1 skill" : `${data.skills.length} skills`;
				ctx.ui.notify(
					`rsi: project ${data.project ?? "(unmapped)"}, ${facts}, ${skills}, ` +
						`${data.sessions_indexed} sessions indexed, home ${data.home}`,
					"info",
				);
			} catch (error) {
				ctx.ui.notify(`rsi: ${errorText(error)}`, "error");
			}
		},
	});
}
