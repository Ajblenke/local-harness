import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * Compatibility stub for an old symlink in ~/.pi/agent/extensions.
 *
 * The previous experiment duplicated telemetry, used a machine-specific prompt
 * path, and attempted to inject clarification messages after a provider reply.
 * System guidance now comes from the normal pi prompt/skill mechanisms, while
 * telemetry has one owner in telemetry.ts.
 */
export default function agentSystemPromptDisabled(_pi: ExtensionAPI): void {}
