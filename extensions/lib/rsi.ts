/**
 * Bridge to the Python core.
 *
 * The extensions never read the state files or the database. They spawn
 * `python3 -I <package>/bin/rsi <command> --json` and parse stdout, so the two halves
 * can be changed and released independently, and a Python failure cannot crash pi.
 *
 * `-I` isolates the interpreter from the user's site-packages and PYTHONPATH: the core
 * is standard library only, and it must stay that way to install from a git URL.
 */

import { execFile, spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);

/** Package root: <root>/extensions/lib/rsi.ts -> <root>. */
export const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const CLI = join(PACKAGE_ROOT, "bin", "rsi");

export interface ContextPayload {
	home: string;
	cwd: string | null;
	project: string | null;
	facts: string;
	fact_count: number;
	skills: { name: string; scope: string }[];
	skill_paths: string[];
	sessions_indexed: number;
}

export interface RecallHit {
	session: string;
	project: string | null;
	turn: number;
	ts: string | null;
	relevance: number;
	section: string;
	text: string;
}

export interface RecallPayload {
	query: string;
	project: string | null;
	hits: RecallHit[];
}

export function python(): string {
	const configured = process.env.RSI_PYTHON?.trim();
	return configured || "python3";
}

export function rsiHome(): string {
	const configured = process.env.RSI_HOME?.trim();
	return configured ? resolve(configured.replace(/^~(?=\/|$)/, homedir())) : join(homedir(), ".pi", "rsi");
}

export function learnLog(): string {
	return join(rsiHome(), "state", "learn.out");
}

export interface RunOptions {
	timeoutMs?: number;
	signal?: AbortSignal;
	cwd?: string;
}

/** Run a CLI command and return trimmed stdout. Throws with stderr on failure. */
export async function rsi(args: string[], options: RunOptions = {}): Promise<string> {
	const { stdout } = await run(python(), ["-I", CLI, ...args], {
		timeout: options.timeoutMs ?? 30_000,
		maxBuffer: 8 * 1024 * 1024,
		signal: options.signal,
		cwd: options.cwd,
	});
	return stdout.trim();
}

/** Run a CLI command with `--json`; returns null instead of throwing. */
export async function rsiJson<T>(args: string[], options: RunOptions = {}): Promise<T | null> {
	try {
		return JSON.parse(await rsi([...args, "--json"], options)) as T;
	} catch {
		return null;
	}
}

/** Start a command detached so it outlives pi; output is appended to a log file. */
export function rsiDetached(args: string[], logFile: string): void {
	const log = logFile || learnLog();
	mkdirSync(dirname(log), { recursive: true });
	const out = openSync(log, "a");
	const child = spawn(python(), ["-I", CLI, ...args], {
		detached: true,
		stdio: ["ignore", out, out],
		env: process.env,
	});
	child.unref();
	// The child holds its own descriptor; keeping the parent's open would leak one per
	// session for the lifetime of the pi process.
	closeSync(out);
}

export function errorText(error: unknown): string {
	const detail = (error as { stderr?: string }).stderr?.trim();
	if (detail) {
		return detail.slice(0, 800);
	}
	return (error instanceof Error ? error.message : String(error)).slice(0, 800);
}
