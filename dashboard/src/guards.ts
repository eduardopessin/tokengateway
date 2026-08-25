/**
 * Canonical runtime guards for this package.
 *
 * Upstream provider payloads are undocumented and change without notice, so
 * every field read goes through these helpers instead of a cast.
 */

/** Narrow an unknown value to an object whose fields are still `unknown`. */
export function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read a numeric field, tolerating numeric strings (providers mix both). */
export function readNumber(value: unknown): number | undefined {
	if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
	if (typeof value === "string" && value.trim() !== "") {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : undefined;
	}
	return undefined;
}

/** Read a non-empty string field. */
export function readString(value: unknown): string | undefined {
	return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Parse an ISO-8601 or epoch-ish timestamp into epoch milliseconds. */
export function readTimestampMs(value: unknown): number | undefined {
	if (typeof value === "number" && Number.isFinite(value)) {
		// Providers send both seconds and milliseconds; disambiguate by magnitude.
		return value > 1e11 ? value : value * 1000;
	}
	if (typeof value === "string" && value.trim() !== "") {
		const parsed = Date.parse(value);
		if (Number.isFinite(parsed)) return parsed;
		const numeric = Number(value);
		if (Number.isFinite(numeric)) return numeric > 1e11 ? numeric : numeric * 1000;
	}
	return undefined;
}
