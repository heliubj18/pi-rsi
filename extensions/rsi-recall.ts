/**
 * rsi-recall: a `recall` tool over earlier sessions.
 *
 * The tool is the deliberate half of memory. Facts are injected automatically because
 * they are small and dated; transcripts are not, because searching them costs a tool
 * call and the model should make that call when the user's words suggest prior work
 * ("last time", "the command we used", "why did we reject X").
 *
 * Excerpts are labelled with session, turn and date. They are records of past sessions,
 * not current truth, and the tool description says so: a recall hit that is trusted
 * blindly is worse than no recall at all.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { errorText, type RecallPayload, rsiJson } from "./lib/rsi.ts";

const MAX_OUTPUT_CHARS = 8000;

function format(payload: RecallPayload): string {
	if (payload.hits.length === 0) {
		return "No matching past turns.";
	}
	const intro =
		`${payload.hits.length} excerpt(s)${payload.project ? ` from project ${payload.project}` : ""}. ` +
		"Dated records of past sessions: verify anything that may have changed.";
	const parts: string[] = [intro];
	let size = intro.length;
	for (const hit of payload.hits) {
		const when = hit.ts ? hit.ts.slice(0, 10) : "unknown date";
		const block = `\n### ${when} — ${hit.section}\n${hit.text}`;
		if (size + block.length > MAX_OUTPUT_CHARS) {
			parts.push("\n(remaining excerpts omitted for length; narrow the query)");
			break;
		}
		parts.push(block);
		size += block.length;
	}
	return parts.join("");
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "recall",
		label: "Recall",
		description:
			"Search turns from earlier pi sessions in this project for how something was done or decided before. Returns dated excerpts. Verify anything that may have changed.",
		promptSnippet: "Search earlier sessions for past results, commands and decisions",
		promptGuidelines: [
			"Use recall when the user refers to earlier work (\"last time\", \"上次\", \"the command we used\") or when a past result, command or decision would save rediscovering it.",
			"Recalled excerpts are dated records of past sessions and can be outdated; verify anything that may have changed before relying on it.",
			"The search matches substrings, so reuse the words the earlier session would have used, including identifiers and error strings.",
		],
		parameters: Type.Object({
			query: Type.String({
				description: "What to look for, in the words the earlier session would have used",
			}),
			all_projects: Type.Optional(
				Type.Boolean({ description: "Search every project, not only the current one" }),
			),
			limit: Type.Optional(
				Type.Integer({ minimum: 1, maximum: 10, description: "Maximum excerpts (default 5)" }),
			),
			sort: Type.Optional(
				Type.Union([Type.Literal("relevance"), Type.Literal("recent")], {
					description:
						"recent for \"last time\"/\"latest\", relevance for a specific identifier or error",
				}),
			),
		}),
		async execute(_id, params, signal, _onUpdate, ctx) {
			const args = [params.query, "--cwd", ctx.cwd, "--limit", String(params.limit ?? 5)];
			if (params.all_projects) {
				args.push("--all-projects");
			}
			if (params.sort) {
				args.push("--sort", params.sort);
			}
			try {
				const payload = await rsiJson<RecallPayload>(["recall", ...args], {
					timeoutMs: 20_000,
					signal,
				});
				if (!payload) {
					return {
						content: [{ type: "text", text: "recall failed: the search command returned no JSON." }],
						details: {},
						isError: true,
					};
				}
				return { content: [{ type: "text", text: format(payload) }], details: {} };
			} catch (error) {
				return {
					content: [{ type: "text", text: `recall failed: ${errorText(error)}` }],
					details: {},
					isError: true,
				};
			}
		},
	});
}
