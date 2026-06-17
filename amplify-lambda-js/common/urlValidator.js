import { getLogger } from "./logging.js";

const logger = getLogger("urlValidator");

/**
 * Validates a URL to prevent Server-Side Request Forgery (SSRF) attacks.
 * Blocks requests to private/internal networks, cloud metadata endpoints,
 * and non-HTTPS URLs when credentials are being forwarded.
 *
 * @param {string} url - The URL to validate.
 * @param {Object} options - Validation options.
 * @param {boolean} options.allowCredentialForwarding - If true, applies stricter validation (HTTPS required, allowlist enforced).
 * @param {string[]} options.allowedHosts - List of allowed hostnames (derived from API_BASE_URL if not provided).
 * @returns {{ valid: boolean, reason?: string }} Validation result.
 */
export const validateUrl = (url, options = {}) => {
    const { allowCredentialForwarding = false, allowedHosts = null } = options;

    if (!url || typeof url !== "string") {
        return { valid: false, reason: "URL is empty or not a string" };
    }

    let parsed;
    try {
        parsed = new URL(url);
    } catch (e) {
        return { valid: false, reason: "Invalid URL format" };
    }

    // Block non-HTTP(S) protocols
    if (!["http:", "https:"].includes(parsed.protocol)) {
        return { valid: false, reason: `Blocked protocol: ${parsed.protocol}` };
    }

    // Block private/internal IP ranges and cloud metadata endpoints
    const hostname = parsed.hostname.toLowerCase();

    if (isPrivateOrReservedHost(hostname)) {
        return { valid: false, reason: `Blocked internal/private address: ${hostname}` };
    }

    // When forwarding credentials, enforce HTTPS and allowlist
    if (allowCredentialForwarding) {
        if (parsed.protocol !== "https:") {
            return { valid: false, reason: "HTTPS required when forwarding credentials" };
        }

        const hosts = allowedHosts || getAllowedHosts();
        if (hosts.length > 0 && !hosts.some(allowed => hostname === allowed || hostname.endsWith("." + allowed))) {
            logger.warn(`SSRF blocked: credential forwarding to non-allowlisted host: ${hostname}`);
            return { valid: false, reason: `Host not in allowlist: ${hostname}` };
        }
    }

    return { valid: true };
};

/**
 * Checks if a hostname resolves to a private, reserved, or internal address.
 */
function isPrivateOrReservedHost(hostname) {
    // Block cloud metadata endpoints
    const blockedHosts = [
        "169.254.169.254",       // AWS/GCP/Azure metadata
        "metadata.google.internal",
        "metadata.goog",
        "169.254.170.2",         // AWS ECS task metadata
        "fd00:ec2::254",         // AWS IPv6 metadata
    ];

    if (blockedHosts.includes(hostname)) return true;

    // Block localhost variants
    if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]" || hostname === "::1") {
        return true;
    }

    // Block common internal TLDs/patterns
    if (hostname.endsWith(".internal") || hostname.endsWith(".local") || hostname.endsWith(".localhost")) {
        return true;
    }

    // Block private IPv4 ranges
    const ipv4Match = hostname.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (ipv4Match) {
        const [, a, b] = ipv4Match.map(Number);
        if (a === 10) return true;                          // 10.0.0.0/8
        if (a === 172 && b >= 16 && b <= 31) return true;   // 172.16.0.0/12
        if (a === 192 && b === 168) return true;            // 192.168.0.0/16
        if (a === 127) return true;                         // 127.0.0.0/8
        if (a === 0) return true;                           // 0.0.0.0/8
        if (a === 169 && b === 254) return true;            // 169.254.0.0/16 (link-local)
    }

    return false;
}

/**
 * Derives the list of allowed hosts from API_BASE_URL environment variable.
 */
function getAllowedHosts() {
    const apiBaseUrl = process.env.API_BASE_URL;
    if (!apiBaseUrl) return [];

    try {
        const parsed = new URL(apiBaseUrl);
        return [parsed.hostname];
    } catch {
        return [];
    }
}
