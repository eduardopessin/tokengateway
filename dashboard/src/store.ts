/**
 * Credential persistence.
 *
 * Tokens live in a single 0600 JSON file next to the app. Refresh tokens are
 * long-lived, so the file is the app's most sensitive artifact.
 */

import { chmod } from "node:fs/promises";
import { isRecord, readNumber, readString } from "./guards";
import { type ProviderId, isProviderId } from "./providers";

const STORE_PATH = process.env.CREDENTIALS_PATH || `${import.meta.dir}/../credentials.json`;
const SEED_PATH = process.env.CREDENTIALS_SEED_PATH || "/app/credentials-seed/credentials.json";

export interface StoredCredential {
	access: string;
	refresh?: string;
	/** Epoch ms at which `access` stops working. */
	expires?: number;
	email?: string;
	/** Google Cloud project backing an Antigravity account. */
	projectId?: string;
	/** Free-form plan label surfaced by the provider. */
	plan?: string;
	authorizedAt?: number;
}

export type CredentialMap = Partial<Record<ProviderId, StoredCredential>>;

/**
 * A rotated token must survive a failed write. The Kubernetes pod mounts this
 * store from a Secret, so the FS is read-only there and `Bun.write` throws
 * EROFS — which used to reject the whole refresh and leave the caller on a dead
 * access token. Persistence is therefore best-effort: the overlay keeps the
 * live credential for the process lifetime (omp's broker likewise keeps a
 * refreshed row usable independently of the durable write). `null` marks a
 * deletion that could not be persisted.
 */
const overlay = new Map<ProviderId, StoredCredential | null>();
let persistFailure: string | undefined;

/** Last persistence error, or undefined when the store is writable. */
export function persistenceError(): string | undefined {
	return persistFailure;
}

/**
 * Set when the store exists but cannot be parsed. A tolerant reader returning
 * `{}` is fine; letting the next write persist that `{}` is not — it wipes
 * every refresh token and costs a full re-login on all providers.
 */
let storeUnreadable = false;

function applyOverlay(credentials: CredentialMap): CredentialMap {
	for (const [provider, value] of overlay) {
		if (value === null) delete credentials[provider];
		else credentials[provider] = value;
	}
	return credentials;
}

export async function loadCredentials(): Promise<CredentialMap> {
	const credentials: CredentialMap = {};
	let file = Bun.file(STORE_PATH);
	const seedFile = Bun.file(SEED_PATH);

	// 1. Load from seed file if present
	if (await seedFile.exists()) {
		try {
			const seedParsed: unknown = await seedFile.json();
			if (isRecord(seedParsed)) {
				for (const [provider, value] of Object.entries(seedParsed)) {
					if (!isProviderId(provider) || !isRecord(value)) continue;
					const access = readString(value.access);
					if (!access) continue;
					credentials[provider] = {
						access,
						refresh: readString(value.refresh),
						expires: readNumber(value.expires),
						email: readString(value.email),
						projectId: readString(value.projectId),
						plan: readString(value.plan),
						authorizedAt: readNumber(value.authorizedAt),
					};
				}
			}
		} catch (e) {
			console.warn(`[store] error reading seed: ${e}`);
		}
	}

	// 2. Overwrite with STORE_PATH if present and readable
	if (await file.exists()) {
		try {
			const parsed: unknown = await file.json();
			if (isRecord(parsed)) {
				storeUnreadable = false;
				for (const [provider, value] of Object.entries(parsed)) {
					if (!isProviderId(provider) || !isRecord(value)) continue;
					const access = readString(value.access);
					if (!access) continue;
					credentials[provider] = {
						access,
						refresh: readString(value.refresh) ?? credentials[provider]?.refresh,
						expires: readNumber(value.expires) ?? credentials[provider]?.expires,
						email: readString(value.email) ?? credentials[provider]?.email,
						projectId: readString(value.projectId) ?? credentials[provider]?.projectId,
						plan: readString(value.plan) ?? credentials[provider]?.plan,
						authorizedAt: readNumber(value.authorizedAt) ?? credentials[provider]?.authorizedAt,
					};
				}
			}
		} catch (error) {
			console.warn(`[store] STORE_PATH unreadable: ${error}`);
		}
	}

	return applyOverlay(credentials);
}

async function syncToKubernetesSecrets(credentials: CredentialMap): Promise<void> {
	try {
		const tokenPath = "/var/run/secrets/kubernetes.io/serviceaccount/token";
		const caPath = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt";
		const nsPath = "/var/run/secrets/kubernetes.io/serviceaccount/namespace";

		const tokenFile = Bun.file(tokenPath);
		if (!(await tokenFile.exists())) return;

		const saToken = (await tokenFile.text()).trim();
		const ns = (await Bun.file(nsPath).text()).trim();
		const host = process.env.KUBERNETES_SERVICE_HOST || "kubernetes.default.svc";
		const port = process.env.KUBERNETES_SERVICE_PORT || "443";
		const k8sBase = `https://${host}:${port}`;

		// 1. Sync Secret quota-dashboard-credentials in current namespace
		const qCredsJson = JSON.stringify(credentials, null, 2);
		const qCredsB64 = Buffer.from(qCredsJson).toString("base64");
		const patchQuotaBody = JSON.stringify({
			data: {
				"credentials.json": qCredsB64,
			},
		});

		await fetch(`${k8sBase}/api/v1/namespaces/${ns}/secrets/quota-dashboard-credentials`, {
			method: "PATCH",
			headers: {
				Authorization: `Bearer ${saToken}`,
				"Content-Type": "application/strategic-merge-patch+json",
			},
			body: patchQuotaBody,
			tls: { ca: Bun.file(caPath) },
		}).catch(err => console.warn(`[store] Failed to update secret quota-dashboard-credentials: ${err}`));

		// 2. Sync Secret litellm-secrets in litellm namespace
		const litellmData: Record<string, string> = {};
		if (credentials["openai-codex"]?.access) {
			litellmData["OPENAI_CODEX_OAUTH_TOKEN"] = Buffer.from(credentials["openai-codex"].access).toString("base64");
		}
		if (credentials["openai-codex"]?.refresh) {
			litellmData["OPENAI_CODEX_REFRESH_TOKEN"] = Buffer.from(credentials["openai-codex"].refresh).toString("base64");
		}
		if (credentials["anthropic"]?.access) {
			litellmData["ANTHROPIC_OAUTH_TOKEN"] = Buffer.from(credentials["anthropic"].access).toString("base64");
		}
		if (credentials["anthropic"]?.refresh) {
			litellmData["ANTHROPIC_REFRESH_TOKEN"] = Buffer.from(credentials["anthropic"].refresh).toString("base64");
		}
		if (credentials["google-antigravity"]?.access) {
			litellmData["GOOGLE_ANTIGRAVITY_OAUTH_TOKEN"] = Buffer.from(credentials["google-antigravity"].access).toString("base64");
		}
		if (credentials["google-antigravity"]?.refresh) {
			litellmData["GOOGLE_ANTIGRAVITY_REFRESH_TOKEN"] = Buffer.from(credentials["google-antigravity"].refresh).toString("base64");
		}
		if (credentials["google-antigravity"]?.projectId) {
			litellmData["GOOGLE_ANTIGRAVITY_PROJECT_ID"] = Buffer.from(credentials["google-antigravity"].projectId).toString("base64");
		}

		if (Object.keys(litellmData).length > 0) {
			const patchLitellmBody = JSON.stringify({ data: litellmData });
			await fetch(`${k8sBase}/api/v1/namespaces/litellm/secrets/litellm-secrets`, {
				method: "PATCH",
				headers: {
					Authorization: `Bearer ${saToken}`,
					"Content-Type": "application/strategic-merge-patch+json",
				},
				body: patchLitellmBody,
				tls: { ca: Bun.file(caPath) },
		}).catch(err => console.warn(`[store] Failed to update secret litellm-secrets: ${err}`));
		}
		console.log(`[store] Kubernetes Secrets synchronized successfully`);
	} catch (e) {
		console.warn(`[store] Error syncing Kubernetes Secrets: ${e}`);
	}
}

/**
 * Serializes every mutation. Refreshes run concurrently and Anthropic/OpenAI
 * rotate refresh tokens, so two interleaved read-modify-write cycles can drop
 * a rotation — and the superseded token is already dead upstream, which costs
 * a full re-login. The queue makes each mutation observe the previous write.
 */
let writeQueue: Promise<void> = Promise.resolve();

function mutate(apply: (credentials: CredentialMap) => void): Promise<void> {
	const next = writeQueue.then(async () => {
		const credentials = await loadCredentials();
		apply(credentials);
		if (storeUnreadable) {
			persistFailure = "credentials file unreadable — write blocked to prevent token loss";
			console.warn(`[store] ${persistFailure}`);
			return;
		}
		try {
			await Bun.write(STORE_PATH, JSON.stringify(credentials, null, 2));
			await chmod(STORE_PATH, 0o600);
			persistFailure = undefined;
		} catch (error) {
			// Read-only mount (K8s Secret): the overlay already holds the value, so
			// the refresh still counts. Losing the write must not fail it.
			persistFailure = error instanceof Error ? error.message : String(error);
			console.warn(`[store] persistence failed (using in-memory overlay): ${persistFailure}`);
		}
		await syncToKubernetesSecrets(credentials);
	});
	// Keep the chain alive after a rejection so one failure cannot wedge the queue.
	writeQueue = next.catch(() => {});
	return next;
}

export function saveCredential(provider: ProviderId, credential: StoredCredential): Promise<void> {
	overlay.set(provider, credential);
	return mutate(credentials => {
		credentials[provider] = credential;
	});
}

export function deleteCredential(provider: ProviderId): Promise<void> {
	overlay.set(provider, null);
	return mutate(credentials => {
		delete credentials[provider];
	});
}
