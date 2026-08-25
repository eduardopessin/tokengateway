/**
 * OAuth + usage endpoint configuration per provider.
 *
 * These are the first-party desktop-client registrations (Claude Code, Codex
 * CLI, Antigravity). They are public clients: the "secret" Google ships is
 * embedded in the shipped binary and carries no confidentiality, which is why
 * PKCE is what actually protects the exchange.
 *
 * Consequence worth knowing: consumer-plan OAuth tokens are contractually
 * scoped to those first-party clients, so this app is only appropriate for
 * inspecting your own account on your own machine.
 */

export type ProviderId = "anthropic" | "openai-codex" | "google-antigravity";

export interface ProviderConfig {
	id: ProviderId;
	label: string;
	authorizeUrl: string;
	tokenUrl: string;
	clientId: string;
	/** Public-client secret required by Google's token endpoint. */
	clientSecret?: string;
	/**
	 * Whether to add PKCE to the exchange. Anthropic and OpenAI register their
	 * desktop clients as public and require it; Google's Antigravity client
	 * authenticates with the shipped secret and its authorization codes are not
	 * challenge-bound, so sending a challenge there breaks the redirect.
	 */
	usePkce: boolean;
	scopes: string;
	/** Loopback port the provider has whitelisted for this client. */
	callbackPort: number;
	callbackPath: string;
	/** Extra authorize-query parameters the provider requires. */
	extraAuthParams: Record<string, string>;
	/** Where the provider's own usage dashboard lives. */
	dashboardUrl: string;
}

const ANTHROPIC_SCOPES = [
	"org:create_api_key",
	"user:profile",
	"user:inference",
	"user:sessions:claude_code",
	"user:mcp_servers",
	"user:file_upload",
].join(" ");

const GOOGLE_SCOPES = [
	"https://www.googleapis.com/auth/cloud-platform",
	"https://www.googleapis.com/auth/userinfo.email",
	"https://www.googleapis.com/auth/userinfo.profile",
	"https://www.googleapis.com/auth/cclog",
	"https://www.googleapis.com/auth/experimentsandconfigs",
].join(" ");

export const PROVIDERS: Record<ProviderId, ProviderConfig> = {
	anthropic: {
		id: "anthropic",
		label: "Anthropic",
		authorizeUrl: "https://claude.ai/oauth/authorize",
		tokenUrl: "https://api.anthropic.com/v1/oauth/token",
		clientId: "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
		usePkce: true,
		scopes: ANTHROPIC_SCOPES,
		callbackPort: 54545,
		callbackPath: "/callback",
		// `code=true` makes claude.ai render the copyable code instead of a bare redirect.
		extraAuthParams: { code: "true" },
		dashboardUrl: "https://claude.ai/settings/usage",
	},
	"openai-codex": {
		id: "openai-codex",
		label: "OpenAI Codex",
		authorizeUrl: "https://auth.openai.com/oauth/authorize",
		tokenUrl: "https://auth.openai.com/oauth/token",
		clientId: "app_EMoamEEZ73f0CkXaXp7hrann",
		usePkce: true,
		scopes: "openid profile email offline_access api.connectors.read api.connectors.invoke",
		callbackPort: 1455,
		callbackPath: "/auth/callback",
		extraAuthParams: {
			id_token_add_organizations: "true",
			codex_cli_simplified_flow: "true",
			originator: "pi",
		},
		dashboardUrl: "https://chatgpt.com/#settings/Usage",
	},
	"google-antigravity": {
		id: "google-antigravity",
		label: "Google Antigravity",
		authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth",
		tokenUrl: "https://oauth2.googleapis.com/token",
		clientId: "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
		clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? "",
		usePkce: false,
		scopes: GOOGLE_SCOPES,
		callbackPort: 51121,
		callbackPath: "/oauth-callback",
		// Google only returns a refresh token when consent is forced.
		extraAuthParams: { access_type: "offline", prompt: "consent" },
		dashboardUrl: "https://antigravity.google",
	},
};

export const PROVIDER_IDS: readonly ProviderId[] = Object.keys(PROVIDERS) as ProviderId[];

export function isProviderId(value: string): value is ProviderId {
	return value in PROVIDERS;
}

// ── Upstream usage surfaces ────────────────────────────────────────────

export const ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage";
export const CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage";
export const ANTIGRAVITY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com";

/**
 * Claude's usage endpoint gates on the Claude Code client fingerprint: a
 * generic user-agent gets a 403, so the CLI's beta flags travel with it.
 */
export const CLAUDE_HEADERS: Record<string, string> = {
	accept: "application/json, text/plain, */*",
	"anthropic-beta":
		"claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,redact-thinking-2026-02-12,context-management-2025-06-27,prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,advanced-tool-use-2025-11-20,effort-2025-11-24,extended-cache-ttl-2025-04-11",
	"content-type": "application/json",
	"user-agent": "claude-cli/2.1.206 (external, cli)",
};

/** Antigravity gates model/quota access on this client version string. */
export const ANTIGRAVITY_USER_AGENT =
	"antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)";
