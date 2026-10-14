// Which Bedrock Claude models use *adaptive* thinking.
//
// Newer Claude models reject the legacy `thinking.type = "enabled"` + `budget_tokens`
// request with:
//   "thinking.type.enabled" is not supported for this model. Use "thinking.type.adaptive"
//   and "output_config.effort" to control thinking behavior.
// and require `thinking.type = "adaptive"` plus `output_config.effort` instead.
//
// Adaptive: Opus 4.6+, Opus 5+, Sonnet 5+, Haiku 5+ (also two-digit major versions).
// Older reasoning-capable models keep the legacy `enabled` + `budget_tokens` request.
//
// The `(?!\d)` guards stop date-suffixed IDs such as `claude-3-5-haiku-20241022` from
// being read as a two-digit major version.
const ADAPTIVE_THINKING_MODEL =
    /claude.*opus-4-[6-9]|claude.*opus-4-[1-9][0-9]|claude.*opus-[5-9]|claude.*opus-[1-9][0-9](?!\d)|claude.*sonnet-[5-9]|claude.*sonnet-[1-9][0-9](?!\d)|claude.*haiku-[5-9]|claude.*haiku-[1-9][0-9](?!\d)/i;

export const usesAdaptiveThinking = (modelId) => ADAPTIVE_THINKING_MODEL.test(modelId ?? "");
