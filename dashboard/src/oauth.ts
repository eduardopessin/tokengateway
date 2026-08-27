/**
 * OAuth 2.0 authorization-code flow with PKCE against the three providers.
 *
 * Each provider whitelists one fixed loopback port for its desktop client, so
 * the callback listener must bind that exact port — a random port is rejected
 * at the authorize step.
 */

import type { Server } from "bun";
import { isRecord, readNumber, readString } from "./guards";
import {
	ANTIGRAVITY_ENDPOINT,
	ANTIGRAVITY_USER_AGENT,
	PROVIDERS,
} from "./providers";
import type { ProviderConfig, ProviderId } from "./providers";
import { saveCredential } from "./store";
import type { StoredCredential } from "./store";

const CALLBACK_TIMEOUT_MS = 15 * 60 * 1000;
const TOKEN_TIMEOUT_MS = 30_000;
/**
 * Store every expiry 5 minutes early. Providers hand out `expires_in: 3599`,
 * and treating that instant as the deadline means an in-flight request can ride
 * a token that dies mid-call. omp bakes the same skew into every provider.
 */
const EXPIRY_SKEW_MS = 5 * 60_000;

/**
 * A dead grant needs a new interactive login; a network blip must be retried.
 * Patterns mirror omp's `auth-classify` so the two agree on what is fatal.
 */
const DEFINITIVE_FAILURE =
	/invalid_grant|invalid_token|unauthorized_client|\brevoked\b|refresh[\s_]?token.*expired/i;

export function isDefinitiveOAuthFailure(error: unknown): boolean {
	const msg = error instanceof Error ? error.message : String(error);
	return DEFINITIVE_FAILURE.test(msg);
}

interface PendingLogin {
	provider: ProviderId;
	verifier: string;
	state: string;
	redirectUri: string;
	/** Resolves once the browser hits the loopback callback. */
	settle: (result: { code: string; state: string } | { error: string }) => void;
	/**
	 * One listener per loopback family. `localhost` resolves to `::1` before
	 * `127.0.0.1` on most Linux setups, so an IPv4-only bind makes the
	 * provider's redirect land on a refused connection.
	 */
	servers: Server<undefined>[];
	timer: Timer;
}

const pending: Map<ProviderId, PendingLogin> = new Map();

async function generatePkce(): Promise<{ verifier: string; challenge: string }> {
	const bytes = new Uint8Array(96);
	crypto.getRandomValues(bytes);
	const verifier = Buffer.from(bytes).toString("base64url");
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
	return { verifier, challenge: Buffer.from(digest).toString("base64url") };
}

const CALLBACK_PAGE = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Authenticated</title>
<style>body{font-family:-apple-system,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{text-align:center}h1{font-size:1.3em;margin-bottom:8px}p{color:#8b949e;font-size:.9em}</style>
</head><body><div><h1>Authenticated</h1><p>You can close this window.</p></div>
<script>setTimeout(()=>window.close(),1200)</script></body></html>`;

/**
 * Start a login: bind the provider's loopback port and return the URL the user
 * must open. Resolution of the returned promise means tokens are stored.
 */
export async function beginLogin(providerId: ProviderId): Promise<{ url: string; completion: Promise<void> }> {
	cancelLogin(providerId);

	const config = PROVIDERS[providerId];
	const { verifier, challenge } = await generatePkce();
	const state = crypto.randomUUID();
	const redirectUri = `http://localhost:${config.callbackPort}${config.callbackPath}`;
	let settle: PendingLogin["settle"] = () => {};
	const callback = new Promise<{ code: string; state: string } | { error: string }>(resolve => {
		settle = resolve;
	});

	const handleCallback = (req: Request): Response => {
		const url = new URL(req.url);
		if (url.pathname !== config.callbackPath) return new Response("Not found", { status: 404 });
		const error = url.searchParams.get("error");
		const code = url.searchParams.get("code");
		// Settle after this response is handed back: resolving inline lets the
		// completion chain tear the listener down before the page is flushed,
		// leaving the browser on a dead connection.
		setTimeout(() => {
			if (error) settle({ error });
			else if (code) settle({ code, state: url.searchParams.get("state") ?? "" });
			else settle({ error: "callback missing code" });
		}, 100);
		return new Response(CALLBACK_PAGE, { headers: { "content-type": "text/html; charset=utf-8" } });
	};

	// Bind both loopback families: the provider redirects to the hostname
	// `localhost`, whose resolution order is out of our control.
	const servers: Server<undefined>[] = [];
	const bindErrors: string[] = [];
	for (const hostname of ["0.0.0.0", "127.0.0.1", "::1"]) {
		try {
			servers.push(Bun.serve({ port: config.callbackPort, hostname, fetch: handleCallback }));
		} catch (error) {
			bindErrors.push(`${hostname}: ${error instanceof Error ? error.message : String(error)}`);
		}
	}
	if (servers.length === 0) {
		throw new Error(
			`Could not bind port ${config.callbackPort} (required by ${config.label}) — ${bindErrors.join("; ")}`,
		);
	}

	const timer = setTimeout(() => settle({ error: "login timed out" }), CALLBACK_TIMEOUT_MS);
	pending.set(providerId, { provider: providerId, verifier, state, redirectUri, settle, servers, timer });

	const params = new URLSearchParams({
		response_type: "code",
		client_id: config.clientId,
		redirect_uri: redirectUri,
		scope: config.scopes,
		state,
		...(config.usePkce ? { code_challenge: challenge, code_challenge_method: "S256" } : {}),
		...config.extraAuthParams,
	});

	const completion = (async () => {
		try {
			const result = await callback;
			if ("error" in result) throw new Error(result.error);
			if (result.state && result.state !== state) throw new Error("state mismatch (possible CSRF)");
			await exchangeCode(config, result.code, verifier, redirectUri, state);
		} finally {
			cancelLogin(providerId);
		}
	})();

	return { url: `${config.authorizeUrl}?${params.toString()}`, completion };
}

export function cancelLogin(providerId: ProviderId): void {
	const entry = pending.get(providerId);
	if (!entry) return;
	clearTimeout(entry.timer);
	// Graceful: let the browser's success page finish writing.
	for (const server of entry.servers) server.stop();
	pending.delete(providerId);
}

/**
 * Complete a login from a pasted code, for when the browser runs on another
 * machine and cannot reach this host's loopback listener.
 */
export async function completeLoginWithCode(providerId: ProviderId, pastedCode: string): Promise<void> {
	const entry = pending.get(providerId);
	if (!entry) throw new Error("No pending login for this provider");
	// claude.ai renders the code as `<code>#<state>`.
	const [code, fragmentState] = pastedCode.trim().split("#");
	if (!code) throw new Error("Empty code");
	try {
		await exchangeCode(
			PROVIDERS[providerId],
			code,
			entry.verifier,
			entry.redirectUri,
			fragmentState || entry.state,
		);
	} finally {
		cancelLogin(providerId);
	}
}

interface TokenPayload {
	access: string;
	refresh?: string;
	expires?: number;
	email?: string;
	plan?: string;
}

function parseTokenResponse(raw: unknown): TokenPayload {
	if (!isRecord(raw)) throw new Error("invalid token response");

	const access = readString(raw.access_token);
	if (!access) {
		const detail = readString(raw.error_description) ?? readString(raw.error) ?? "no access_token";
		throw new Error(detail);
	}

	const expiresIn = readNumber(raw.expires_in);
	// Anthropic nests identity under `account`; OpenAI/Google put it in the id_token.
	const account = isRecord(raw.account) ? raw.account : undefined;

	return {
		access,
		refresh: readString(raw.refresh_token),
		expires: expiresIn === undefined ? undefined : Date.now() + expiresIn * 1000 - EXPIRY_SKEW_MS,
		email: account ? readString(account.email_address) : undefined,
		plan: readString(raw.plan_type),
	};
}

/** Pull the email/plan claims out of an OIDC id_token without verifying it. */
function readIdTokenClaims(idToken: string | undefined): { email?: string; plan?: string } {
	if (!idToken) return {};
	const segments = idToken.split(".");
	if (segments.length < 2) return {};
	try {
		const payload: unknown = JSON.parse(Buffer.from(segments[1], "base64url").toString("utf8"));
		if (!isRecord(payload)) return {};
		const auth = isRecord(payload["https://api.openai.com/auth"])
			? payload["https://api.openai.com/auth"]
			: undefined;
		return {
			email: readString(payload.email),
			plan: auth ? readString(auth.chatgpt_plan_type) : undefined,
		};
	} catch {
		return {};
	}
}

async function exchangeCode(
	config: ProviderConfig,
	code: string,
	verifier: string,
	redirectUri: string,
	state: string,
): Promise<void> {
	const isAnthropic = config.id === "anthropic";

	// Anthropic's token endpoint takes JSON and echoes `state`; the others are form-encoded.
	const body = isAnthropic
		? JSON.stringify({
				grant_type: "authorization_code",
				client_id: config.clientId,
				code,
				state,
				redirect_uri: redirectUri,
				code_verifier: verifier,
			})
		: new URLSearchParams({
				grant_type: "authorization_code",
				client_id: config.clientId,
				code,
				...(config.usePkce ? { code_verifier: verifier } : {}),
				redirect_uri: redirectUri,
				...(config.clientSecret ? { client_secret: config.clientSecret } : {}),
			}).toString();

	const response = await fetch(config.tokenUrl, {
		method: "POST",
		headers: {
			"content-type": isAnthropic ? "application/json" : "application/x-www-form-urlencoded",
			accept: "application/json",
		},
		body,
		signal: AbortSignal.timeout(TOKEN_TIMEOUT_MS),
	});

	const text = await response.text();
	if (!response.ok) throw new Error(`token exchange failed (${response.status}): ${text.slice(0, 300)}`);

	const raw: unknown = JSON.parse(text);
	const token = parseTokenResponse(raw);
	const claims = readIdTokenClaims(isRecord(raw) ? readString(raw.id_token) : undefined);

	const credential: StoredCredential = {
		access: token.access,
		refresh: token.refresh,
		expires: token.expires,
		email: token.email ?? claims.email,
		plan: token.plan ?? claims.plan,
		authorizedAt: Date.now(),
	};

	if (config.id === "google-antigravity") {
		credential.projectId = await discoverAntigravityProject(token.access);
	}

	await saveCredential(config.id, credential);
}

/** Refresh an expired access token, persisting whatever the provider rotates. */
export async function refreshCredential(
	providerId: ProviderId,
	credential: StoredCredential,
): Promise<StoredCredential> {
	if (!credential.refresh) throw new Error(`${providerId}: no refresh token — a new login is required`);
	const config = PROVIDERS[providerId];
	const isAnthropic = providerId === "anthropic";

	const body = isAnthropic
		? JSON.stringify({
				grant_type: "refresh_token",
				client_id: config.clientId,
				refresh_token: credential.refresh,
			})
		: new URLSearchParams({
				grant_type: "refresh_token",
				client_id: config.clientId,
				refresh_token: credential.refresh,
				...(config.clientSecret ? { client_secret: config.clientSecret } : {}),
			}).toString();

	const response = await fetch(config.tokenUrl, {
		method: "POST",
		headers: {
			"content-type": isAnthropic ? "application/json" : "application/x-www-form-urlencoded",
			accept: "application/json",
			...(isAnthropic
				? {
						"anthropic-beta": "oauth-2025-04-20",
						"User-Agent": "anthropic-sdk-typescript/0.94.0 userOAuthProvider",
					}
				: {}),
		},
		body,
		signal: AbortSignal.timeout(TOKEN_TIMEOUT_MS),
	});

	const text = await response.text();
	if (!response.ok) throw new Error(`refresh failed (${response.status}): ${text.slice(0, 200)}`);

	const token = parseTokenResponse(JSON.parse(text));
	const refreshed: StoredCredential = {
		...credential,
		access: token.access,
		// Providers rotate refresh tokens inconsistently; keep the old one when absent.
		refresh: token.refresh ?? credential.refresh,
		expires: token.expires,
	};
	await saveCredential(providerId, refreshed);
	return refreshed;
}

/** Return a credential guaranteed fresh for the next minute. */
export async function ensureFresh(
	providerId: ProviderId,
	credential: StoredCredential,
): Promise<StoredCredential> {
	if (credential.expires && credential.expires - Date.now() > 60_000) return credential;
	if (!credential.refresh) return credential;
	return await refreshCredential(providerId, credential);
}

/**
 * Resolve the Cloud project backing an Antigravity account; `fetchAvailableModels`
 * requires it in the request body.
 */
async function discoverAntigravityProject(accessToken: string): Promise<string | undefined> {
	const response = await fetch(`${ANTIGRAVITY_ENDPOINT}/v1internal:loadCodeAssist`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${accessToken}`,
			"Content-Type": "application/json",
			"User-Agent": ANTIGRAVITY_USER_AGENT,
		},
		body: JSON.stringify({ metadata: { ideType: "ANTIGRAVITY" } }),
		signal: AbortSignal.timeout(TOKEN_TIMEOUT_MS),
	});
	if (!response.ok) return undefined;

	const payload: unknown = await response.json();
	if (!isRecord(payload)) return undefined;
	for (const key of ["cloudaicompanionProject", "projectId", "project"]) {
		const value = payload[key];
		const direct = readString(value);
		if (direct) return direct;
		if (isRecord(value)) {
			const nested = readString(value.id);
			if (nested) return nested;
		}
	}
	return undefined;
}
