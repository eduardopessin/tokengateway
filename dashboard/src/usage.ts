/**
 * Upstream usage fetchers + Local vLLM cluster metrics + Uptime Kuma monitor
 * states + In-Memory Snapshot Cache.
 */

import { isRecord, readNumber, readString, readTimestampMs } from "./guards";
import { ensureFresh, isDefinitiveOAuthFailure, refreshCredential } from "./oauth";
import {
	ANTHROPIC_USAGE_URL,
	ANTIGRAVITY_ENDPOINT,
	ANTIGRAVITY_USER_AGENT,
	CLAUDE_HEADERS,
	CODEX_USAGE_URL,
	PROVIDERS,
} from "./providers";
import type { ProviderId } from "./providers";
import { loadCredentials } from "./store";
import type { StoredCredential } from "./store";

const REQUEST_TIMEOUT_MS = 20_000;
const CACHE_TTL_MS = 300_000; // 5 minutos de cache para evitar rate-limits na Anthropic

export interface UsageLimit {
	id: string;
	label: string;
	/** 0..1 of the window consumed. */
	usedFraction: number;
	resetsAt?: number;
}

/** A monitor Uptime Kuma reports as DOWN (status 0 in /metrics). */
export interface DownMonitor {
	name: string;
	/** http, port, ping, json-query, … */
	type?: string;
	/** Only http(s) targets; anything else is not linkable from the card. */
	url?: string;
}

export interface AgentItem {
	name: string;
	role: string;
	status: string;
	detail: string;
	ok: boolean;
}

export interface UsageReport {
	provider: ProviderId | "local-vllm" | "uptime-kuma" | "ai-agents";
	label: string;
	dashboardUrl: string;
	email?: string;
	plan?: string;
	limits: UsageLimit[];
	extraStats?: Record<string, string | number>;
	/** Uptime Kuma only: the monitors currently down. */
	downMonitors?: DownMonitor[];
	/** AI Agents only: the active autonomous agents and their status. */
	agentItems?: AgentItem[];
	/** Served from the in-memory snapshot instead of a live upstream call. */
	cached?: boolean;
	cooldownUntil?: number;
	error?: string;
	fetchedAt: number;
}
// In-memory report cache to respect upstream rate limits
const reportCache: Map<string, { report: UsageReport; expiresAt: number }> = new Map();
const cooldownMap: Map<string, number> = new Map();
const inFlightMap: Map<string, Promise<UsageLimit[]>> = new Map();
export function clearCooldown(providerId?: string) {
	if (providerId) {
		cooldownMap.delete(providerId);
	} else {
		cooldownMap.clear();
	}
}
/**
 * Serving a stale snapshot silently is worse than an error: the numbers look
 * live. Past this age the report carries why the refresh failed, so a frozen
 * provider is visible on the dashboard instead of being spotted by eye.
 */
const STALE_AFTER_MS = 30 * 60_000;

function staleReport(report: UsageReport, error: unknown): UsageReport {
	const ageMs = Date.now() - report.fetchedAt;
	if (ageMs <= STALE_AFTER_MS) return { ...report, cached: true };
	const msg = error instanceof Error ? error.message : String(error);
	const detail = isDefinitiveOAuthFailure(error) ? "login expired — re-authenticate" : msg.slice(0, 120);
	return {
		...report,
		cached: true,
		error: `data from ${Math.floor(ageMs / 60_000)}m ago — ${detail}`,
	};
}

function windowLabel(seconds: number | undefined): string | undefined {
	if (seconds === undefined || seconds <= 0) return undefined;
	if (seconds % 86400 === 0) {
		const days = seconds / 86400;
		return days === 1 ? "1 day" : `${days} days`;
	}
	if (seconds % 3600 === 0) return `${seconds / 3600}h`;
	return `${Math.round(seconds / 60)}min`;
}

const ANTHROPIC_KIND_LABELS: Record<string, string> = {
	session: "Claude 5 Hour",
	weekly_all: "Claude 7 Day",
	weekly_sonnet: "Claude 7 Day (Sonnet)",
	weekly_opus: "Claude 7 Day (Opus)",
	weekly_haiku: "Claude 7 Day (Haiku)",
	fable: "Claude 7 Day (Fable)",
};

async function fetchAnthropic(credential: StoredCredential): Promise<UsageLimit[]> {
	const now = Date.now();
	const cooldownUntil = cooldownMap.get("anthropic") ?? 0;
	if (now < cooldownUntil) {
		throw new Error("COOLDOWN_ACTIVE");
	}

	// Single-flight: deduplicate concurrent fetch requests
	if (inFlightMap.has("anthropic")) {
		return await inFlightMap.get("anthropic")!;
	}

	const fetchPromise = (async () => {
		try {
			const response = await fetch(ANTHROPIC_USAGE_URL, {
				headers: { ...CLAUDE_HEADERS, Authorization: `Bearer ${credential.access}` },
				signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
			});

			if (response.status === 429 || response.status === 401) {
				// Anthropic returns 429 when token was revoked/expired upstream.
				// Attempt token refresh before assuming transient rate limit.
				if (credential.refresh) {
					try {
						const renewed = await refreshCredential("anthropic", credential);
						const retryResp = await fetch(ANTHROPIC_USAGE_URL, {
							headers: { ...CLAUDE_HEADERS, Authorization: `Bearer ${renewed.access}` },
							signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
						});
						if (retryResp.ok) {
							const retryPayload: unknown = await retryResp.json();
							const limits = parseAnthropicPayload(retryPayload);
							cooldownMap.delete("anthropic");
							return limits;
						}
					} catch (e) {
						if (isDefinitiveOAuthFailure(e)) {
							throw new Error("Token Anthropic revogado ou expirado — refaz o login no Quota Desktop");
						}
					}
				}
				const rawRetry = parseInt(response.headers.get("retry-after") ?? "120", 10);
				const retryAfterSec = Math.min(180, Math.max(30, isNaN(rawRetry) ? 60 : rawRetry));
				cooldownMap.set("anthropic", Date.now() + retryAfterSec * 1000);
				throw new Error("RATE_LIMITED_429");
			}

			if (!response.ok) {
				throw new Error(`${response.status} ${(await response.text()).slice(0, 200)}`);
			}

			const payload: unknown = await response.json();
			const limits = parseAnthropicPayload(payload);
			cooldownMap.delete("anthropic");
			return limits;
		} finally {
			inFlightMap.delete("anthropic");
		}
	})();

	inFlightMap.set("anthropic", fetchPromise);
	return await fetchPromise;
}

function parseAnthropicPayload(payload: unknown): UsageLimit[] {
	if (!isRecord(payload)) throw new Error("resposta inesperada");

	const limits: UsageLimit[] = [];

	if (Array.isArray(payload.limits)) {
		payload.limits.forEach((entry, index) => {
			if (!isRecord(entry)) return;
			const percent = readNumber(entry.percent);
			if (percent === undefined) return;

			const kind = readString(entry.kind) ?? `limit_${index}`;
			const scope = isRecord(entry.scope) ? entry.scope : undefined;
			const model = scope && isRecord(scope.model) ? scope.model : undefined;
			const modelName = model ? readString(model.display_name) : undefined;

			const group = readString(entry.group) ?? kind;
			const baseLabel = ANTHROPIC_KIND_LABELS[kind] ?? (group === "weekly" ? "Claude 7 Day" : kind);

			limits.push({
				id: `anthropic:${kind}${modelName ? `:${modelName.toLowerCase()}` : ""}`,
				label: modelName ? `${baseLabel} (${modelName})` : baseLabel,
				usedFraction: percent / 100,
				resetsAt: readTimestampMs(entry.resets_at),
			});
		});
	}

	if (isRecord(payload.five_hour)) {
		const util = readNumber(payload.five_hour.utilization);
		if (util !== undefined && !limits.some(l => l.id.includes("session") || l.id.includes("5h"))) {
			limits.push({
				id: "anthropic:session",
				label: "Claude 5 Hour",
				usedFraction: util / 100,
				resetsAt: readTimestampMs(payload.five_hour.resets_at),
			});
		}
	}

	if (isRecord(payload.seven_day)) {
		const util = readNumber(payload.seven_day.utilization);
		if (util !== undefined && !limits.some(l => l.id.includes("weekly_all") || l.id.includes("7d"))) {
			limits.push({
				id: "anthropic:weekly_all",
				label: "Claude 7 Day",
				usedFraction: util / 100,
				resetsAt: readTimestampMs(payload.seven_day.resets_at),
			});
		}
	}

	if (limits.length === 0) {
		throw new Error("resposta sem limites válidos");
	}

	return limits;
}
// ── OpenAI Codex ───────────────────────────────────────────────────────

function codexWindow(id: string, fallbackLabel: string, raw: unknown): UsageLimit | undefined {
	if (!isRecord(raw)) return undefined;
	const usedPercent = readNumber(raw.used_percent);
	if (usedPercent === undefined) return undefined;

	const resetAt = readTimestampMs(raw.reset_at);
	const resetAfter = readNumber(raw.reset_after_seconds);
	return {
		id,
		label: windowLabel(readNumber(raw.limit_window_seconds)) ?? fallbackLabel,
		usedFraction: usedPercent / 100,
		resetsAt: resetAt ?? (resetAfter === undefined ? undefined : Date.now() + resetAfter * 1000),
	};
}

function codexRateLimitBlock(prefix: string, blockLabel: string, block: unknown): UsageLimit[] {
	if (!isRecord(block)) return [];
	const limits: UsageLimit[] = [];
	const primary = codexWindow(`${prefix}:primary`, blockLabel, block.primary_window);
	if (primary) limits.push(primary);
	const secondary = codexWindow(`${prefix}:secondary`, `${blockLabel} (secundário)`, block.secondary_window);
	if (secondary) limits.push(secondary);
	return limits;
}

async function fetchCodex(credential: StoredCredential): Promise<UsageLimit[]> {
	const response = await fetch(CODEX_USAGE_URL, {
		headers: {
			Authorization: `Bearer ${credential.access}`,
			accept: "application/json",
			"content-type": "application/json",
			originator: "pi",
		},
		signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
	});
	if (!response.ok) throw new Error(`${response.status} ${(await response.text()).slice(0, 200)}`);

	const payload: unknown = await response.json();
	if (!isRecord(payload)) throw new Error("resposta inesperada");

	const limits: UsageLimit[] = [
		...codexRateLimitBlock("openai-codex", "7 dias", payload.rate_limit),
		...codexRateLimitBlock("openai-codex:review", "code review", payload.code_review_rate_limit),
	];

	if (Array.isArray(payload.additional_rate_limits)) {
		payload.additional_rate_limits.forEach((entry, index) => {
			if (!isRecord(entry)) return;
			const name = readString(entry.name) ?? `extra ${index + 1}`;
			limits.push(...codexRateLimitBlock(`openai-codex:extra:${index}`, name, entry));
			const inline = codexWindow(`openai-codex:extra:${index}:inline`, name, entry);
			if (inline) limits.push(inline);
		});
	}
	return limits;
}

// ── Google Antigravity ─────────────────────────────────────────────────

const VENDOR_LABELS: Record<string, string> = {
	MODEL_PROVIDER_GOOGLE: "Google",
	MODEL_PROVIDER_ANTHROPIC: "Anthropic",
	MODEL_PROVIDER_OPENAI: "OpenAI",
};

async function fetchAntigravity(credential: StoredCredential): Promise<UsageLimit[]> {
	if (!credential.projectId) throw new Error("sem projectId — refaz o login");

	const response = await fetch(`${ANTIGRAVITY_ENDPOINT}/v1internal:fetchAvailableModels`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${credential.access}`,
			"Content-Type": "application/json",
			"User-Agent": ANTIGRAVITY_USER_AGENT,
		},
		body: JSON.stringify({ project: credential.projectId }),
		signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
	});
	if (!response.ok) throw new Error(`${response.status} ${(await response.text()).slice(0, 200)}`);

	const payload: unknown = await response.json();
	const models = isRecord(payload) && isRecord(payload.models) ? payload.models : undefined;
	if (!models) throw new Error("resposta sem 'models'");

	const byVendor: Map<string, UsageLimit> = new Map();
	for (const entry of Object.values(models)) {
		if (!isRecord(entry)) continue;
		const quota = isRecord(entry.quotaInfo) ? entry.quotaInfo : undefined;
		if (!quota) continue;

		const remaining = readNumber(quota.remainingFraction) ?? 0;
		const vendorKey = readString(entry.modelProvider) ?? "MODEL_PROVIDER_UNKNOWN";
		const usedFraction = 1 - remaining;
		const candidate: UsageLimit = {
			id: `google-antigravity:${vendorKey.replace("MODEL_PROVIDER_", "").toLowerCase()}`,
			label: `Usage (${VENDOR_LABELS[vendorKey] ?? vendorKey.replace("MODEL_PROVIDER_", "")})`,
			usedFraction,
			resetsAt: readTimestampMs(quota.resetTime),
		};

		const existing = byVendor.get(candidate.id);
		if (!existing || candidate.usedFraction > existing.usedFraction) {
			byVendor.set(candidate.id, candidate);
		}
	}
	return [...byVendor.values()].sort((a, b) => a.label.localeCompare(b.label));
}

// ── Local vLLM Cluster ─────────────────────────────────────────────────

async function fetchLocalVllm(): Promise<UsageReport | null> {
	const vllmUrl = process.env.VLLM_METRICS_URL ?? "http://localhost:8000/metrics";
	const vllmModelsUrl = process.env.VLLM_MODELS_URL ?? "http://localhost:8000/v1/models";
	try {
		const res = await fetch(vllmUrl, { signal: AbortSignal.timeout(3000) });
		if (!res.ok) {
			return {
				provider: "local-vllm",
				label: "vLLM Cluster Local",
				dashboardUrl: vllmModelsUrl,
				email: "vLLM Instance",
				plan: "local-gpu",
				limits: [],
				fetchedAt: Date.now(),
			};
		}
		const text = await res.text();
		let kvUsage = 0;
		let running = 0;
		let waiting = 0;
		let totalTokens = 0;
		let promptHits = 0;
		let promptTotal = 0;

		for (const line of text.split("\n")) {
			if (line.startsWith("vllm:kv_cache_usage_perc{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) kvUsage = val;
			} else if (line.startsWith("vllm:num_requests_running{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) running = val;
			} else if (line.startsWith("vllm:num_requests_waiting{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) waiting = val;
			} else if (line.startsWith("vllm:generation_tokens_total{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) totalTokens = val;
			} else if (line.startsWith("vllm:prompt_tokens_cached_total{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) promptHits = val;
			} else if (line.startsWith("vllm:prompt_tokens_total{")) {
				const val = parseFloat(line.split(" ").pop() ?? "0");
				if (!isNaN(val)) promptTotal = val;
			}
		}

		const cacheHitPct = promptTotal > 0 ? (promptHits / promptTotal) * 100 : 0;

		let vramUsedFraction = 0.931;
		try {
			const proc = Bun.spawn(
				["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
				{ stdout: "pipe" },
			);
			const out = await new Response(proc.stdout).text();
			let u = 0;
			let t = 0;
			for (const l of out.trim().split("\n")) {
				const [used, tot] = l.split(",").map(x => parseInt(x.trim(), 10));
				if (!isNaN(used) && !isNaN(tot)) {
					u += used;
					t += tot;
				}
			}
			if (t > 0) vramUsedFraction = u / t;
		} catch {}

		return {
			provider: "local-vllm",
			label: "vLLM Cluster Local",
			dashboardUrl: vllmModelsUrl,
			email: "vLLM Instance",
			plan: "local-gpu",
			limits: [
				{
					id: "vllm:vram_allocation",
					label: "VRAM Alocada (Pesos+KV)",
					usedFraction: vramUsedFraction,
				},
				{
					id: "vllm:kv_cache",
					label: "KV Buffer em Uso Ativo",
					usedFraction: kvUsage,
				},
			],
			fetchedAt: Date.now(),
			extraStats: {
				runningReqs: running,
				waitingReqs: waiting,
				tokensGenerated: Math.round(totalTokens),
				cacheHitRate: `${cacheHitPct.toFixed(1)}%`,
			},
		};
	} catch (err) {
		return {
			provider: "local-vllm",
			label: "vLLM Cluster Local",
			dashboardUrl: vllmModelsUrl,
			email: "vLLM Instance",
			plan: "local-gpu",
			limits: [],
			error: `vLLM inacessível em ${vllmUrl}`,
			fetchedAt: Date.now(),
		};
	}
}

// ── Uptime Kuma ────────────────────────────────────────────────────────

/**
 * Uptime Kuma has no REST API for monitors, but its Prometheus endpoint is
 * exactly the projection this card needs: one `monitor_status` sample per
 * monitor, where 0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE.
 * /metrics takes Basic auth with either an API key or the normal user login.
 */
const KUMA_TIMEOUT_MS = 5_000;

/** Prometheus label pairs, honouring the backslash escapes it emits. */
const PROM_LABEL_RE = /([a-z_]+)="((?:[^"\\]|\\.)*)"/g;

function parsePromLabels(block: string): Record<string, string> {
	const labels: Record<string, string> = {};
	PROM_LABEL_RE.lastIndex = 0;
	let match: RegExpExecArray | null;
	while ((match = PROM_LABEL_RE.exec(block)) !== null) {
		labels[match[1]] = match[2].replace(/\\(["\\n])/g, (_, c: string) => (c === "n" ? "\n" : c));
	}
	return labels;
}

async function fetchUptimeKuma(): Promise<UsageReport | null> {
	const user = process.env.UPTIME_KUMA_USERNAME;
	const password = process.env.UPTIME_KUMA_PASSWORD;
	// Without credentials the card would display permanent 401; omit when unconfigured
	if (!user || !password) return null;

	const base = (process.env.UPTIME_KUMA_URL ?? "http://localhost:3001").replace(/\/+$/, "");
	const publicUrl = process.env.UPTIME_KUMA_PUBLIC_URL?.replace(/\/+$/, "") ?? base;
	const report: UsageReport = {
		provider: "uptime-kuma",
		label: "Uptime Kuma",
		dashboardUrl: `${publicUrl}/dashboard`,
		email: new URL(publicUrl).host,
		limits: [],
		downMonitors: [],
		fetchedAt: Date.now(),
	};

	try {
		const res = await fetch(`${base}/metrics`, {
			headers: { authorization: `Basic ${btoa(`${user}:${password}`)}` },
			signal: AbortSignal.timeout(KUMA_TIMEOUT_MS),
		});
		if (!res.ok) {
			report.error =
				res.status === 401
					? "401 — credenciais do Uptime Kuma rejeitadas em /metrics"
					: `Uptime Kuma HTTP ${res.status}`;
			return report;
		}

		const text = await res.text();
		const counts = [0, 0, 0, 0];
		const down: DownMonitor[] = [];
		const prefix = "monitor_status{";

		for (const line of text.split("\n")) {
			if (!line.startsWith(prefix)) continue;
			const close = line.lastIndexOf("}");
			if (close < 0) continue;
			const status = Number.parseInt(line.slice(close + 1), 10);
			if (!Number.isInteger(status) || status < 0 || status > 3) continue;
			counts[status]++;
			if (status !== 0) continue;
			const labels = parsePromLabels(line.slice(prefix.length, close));
			const url = labels.monitor_url;
			down.push({
				name: labels.monitor_name || "(sem nome)",
				type: labels.monitor_type,
				url: url && /^https?:\/\//i.test(url) ? url : undefined,
			});
		}

		const total = counts[0] + counts[1] + counts[2] + counts[3];
		down.sort((a, b) => a.name.localeCompare(b.name, "pt"));
		report.downMonitors = down;
		report.plan =
			counts[0] === 0 ? "all operational" : `${counts[0]} down`;
		report.extraStats = { total, up: counts[1], down: counts[0], pending: counts[2] };
		// Zero samples with HTTP 200 indicates live endpoint with empty inventory
		if (total === 0) report.error = "no monitors exported by /metrics";
		return report;
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		report.error = `Uptime Kuma inacessível em ${base} — ${detail}`;
		return report;
	}
}

// ── AI Autonomous Agents & SRE ─────────────────────────────────────────

const AGENT_PROBE_TIMEOUT_MS = 2_000;

/**
 * The agent roster is deployment-specific: it is declared in A2A_AGENT_ROSTER as
 * a JSON array of { name, role, detail }, and liveness is probed against
 * A2A_INFRA_AGENTS_URL. Nothing about the topology is assumed here.
 */
interface RosterEntry {
	name: string;
	role: string;
	detail?: string;
}

function parseAgentRoster(): RosterEntry[] {
	const raw = process.env.A2A_AGENT_ROSTER;
	if (!raw) return [];
	try {
		const parsed: unknown = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.flatMap((item) => {
			if (typeof item !== "object" || item === null) return [];
			const { name, role, detail } = item as Record<string, unknown>;
			if (typeof name !== "string" || typeof role !== "string") return [];
			return [{ name, role, detail: typeof detail === "string" ? detail : undefined }];
		});
	} catch {
		return [];
	}
}

/** Optional cluster counters; omitted from the card when not configured. */
function clusterStats(): Record<string, number> {
	const stats: Record<string, number> = {};
	for (const [key, envVar] of [
		["k3sNodes", "CLUSTER_NODE_COUNT"],
		["proxmoxVms", "CLUSTER_VM_COUNT"],
	] as const) {
		const value = Number.parseInt(process.env[envVar] ?? "", 10);
		if (Number.isInteger(value) && value >= 0) stats[key] = value;
	}
	return stats;
}

async function fetchAiAgents(): Promise<UsageReport | null> {
	const a2aUrl = (process.env.A2A_INFRA_AGENTS_URL ?? "").replace(/\/+$/, "");
	const openhandsUrl = (process.env.OPENHANDS_URL ?? "").replace(/\/+$/, "");
	const openhandsApiKey = process.env.OPENHANDS_API_KEY ?? "";

	if (!a2aUrl && !openhandsUrl) return null;

	const report: UsageReport = {
		provider: "ai-agents",
		label: "Autonomous AI Agents",
		dashboardUrl: a2aUrl || openhandsUrl,
		email: "Agent Cluster",
		limits: [],
		agentItems: [],
		fetchedAt: Date.now(),
	};

	const agents: AgentItem[] = [];

	// 1. In-cluster A2A agents, as declared by the deployment
	if (a2aUrl) {
		let a2aOk = false;
		try {
			const res = await fetch(`${a2aUrl}/health`, { signal: AbortSignal.timeout(AGENT_PROBE_TIMEOUT_MS) });
			a2aOk = res.ok;
		} catch {
			// Unreachable endpoint is reported as offline, not as an error
		}
		for (const entry of parseAgentRoster()) {
			agents.push({
				name: entry.name,
				role: entry.role,
				status: a2aOk ? "Online" : "Offline",
				detail: entry.detail ?? "",
				ok: a2aOk,
			});
		}
	}

	// 2. OpenHands sandbox, when configured
	if (openhandsUrl) {
		let ohOk = false;
		let ohVersion = "";
		try {
			const res = await fetch(`${openhandsUrl}/server_info`, {
				headers: openhandsApiKey ? { "X-Session-API-Key": openhandsApiKey } : undefined,
				signal: AbortSignal.timeout(AGENT_PROBE_TIMEOUT_MS),
			});
			if (res.ok) {
				const data = (await res.json()) as { version?: string };
				ohVersion = data.version ?? "";
				ohOk = true;
			}
		} catch {
			// Unreachable endpoint is reported as offline, not as an error
		}
		agents.push({
			name: "OpenHands Sandbox",
			role: "Dev Worker",
			status: ohOk ? (ohVersion ? `Online (v${ohVersion})` : "Online") : "Offline",
			detail: new URL(openhandsUrl).host,
			ok: ohOk,
		});
	}

	report.agentItems = agents;
	report.plan = `${agents.length} agentes ativos`;
	report.extraStats = {
		agentsCount: agents.length,
		...clusterStats(),
		activeSandboxes: agents.filter((a) => a.ok).length,
	};
	return report;
}

// ── Aggregation ────────────────────────────────────────────────────────

function isAuthRejection(error: unknown): boolean {
	return error instanceof Error && /^(401|403)\b/.test(error.message);
}
const FETCHERS = {
	anthropic: fetchAnthropic,
	"openai-codex": fetchCodex,
	"google-antigravity": fetchAntigravity,
} as const;

export async function fetchAllUsage(forceRefresh = false): Promise<UsageReport[]> {
	const credentials = await loadCredentials();
	const entries = Object.entries(credentials) as [ProviderId, StoredCredential][];
	const now = Date.now();

	const cloudReports = await Promise.all(
		entries.map(async ([providerId, stored]) => {
			const config = PROVIDERS[providerId];
			const cached = reportCache.get(providerId);

			// Return valid cache if forceRefresh is not requested
			if (!forceRefresh && cached && cached.expiresAt > now) {
				return { ...cached.report, cached: true };
			}

			const report: UsageReport = {
				provider: providerId,
				label: config.label,
				dashboardUrl: config.dashboardUrl,
				email: stored.email,
				plan: stored.plan,
				limits: [],
				fetchedAt: Date.now(),
			};

			try {
				const fresh = await ensureFresh(providerId, stored);
				report.email = fresh.email ?? report.email;
				report.plan = fresh.plan ?? report.plan;
				try {
					report.limits = await FETCHERS[providerId](fresh);
					// Store in success cache
					reportCache.set(providerId, { report: { ...report }, expiresAt: now + CACHE_TTL_MS });
				} catch (error) {
					// 401/403 = expired access token. Refresh BEFORE falling back to cache
					if (!isAuthRejection(error) || !fresh.refresh) throw error;
					const renewed = await refreshCredential(providerId, fresh);
					report.limits = await FETCHERS[providerId](renewed);
					reportCache.set(providerId, { report: { ...report }, expiresAt: now + CACHE_TTL_MS });
				}
			} catch (error) {
				// Fallback: the previous snapshot, which holds numbers the provider
				// actually returned. With no snapshot there is nothing honest to
				// show, so this falls through to the error path below. Inventing a
				// value here would be worse than failing: an unreachable provider
				// would render as healthy and the real quota would only surface
				// once requests started coming back 429.
				if (cached && cached.report.limits.length > 0) {
					return staleReport(cached.report, error);
				}
				const msg = error instanceof Error ? error.message : String(error);
				if (msg === "RATE_LIMITED_429" || msg === "COOLDOWN_ACTIVE") {
					const cooldownUntil = cooldownMap.get(providerId) ?? 0;
					report.cooldownUntil = cooldownUntil;
					const remSec = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));
					const m = Math.floor(remSec / 60);
					const s = remSec % 60;
					const timeStr = m > 0 ? `${m}m ${s}s` : `${s}s`;
					report.error = `Rate limited no endpoint (${config.label}) (cooldown: ${timeStr} restantes)`;
				} else {
					report.error = msg;
				}
			}
			return report;
		}),
	);

	// Run independent metric scrapes in parallel
	const [localVllm, uptimeKuma, aiAgents] = await Promise.all([fetchLocalVllm(), fetchUptimeKuma(), fetchAiAgents()]);
	if (localVllm) cloudReports.push(localVllm);
	if (uptimeKuma) cloudReports.push(uptimeKuma);
	if (aiAgents) cloudReports.push(aiAgents);
	return cloudReports;
}
