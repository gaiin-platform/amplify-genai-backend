/**
 * Deterministically repair the common STRUCTURAL JSON defects that some models
 * (Azure gpt-4.1-mini, Bedrock gpt-5.6-*) occasionally emit. These are punctuation defects,
 * not semantic ones, so they can be fixed without changing meaning:
 *   - double / empty commas:  [0,1,,3]  (model dropped an integer)  -> [0,1,3]
 *   - trailing commas:        [0,1,]                                -> [0,1]
 *   - leading commas:         [,0,1]                                -> [0,1]
 *   - doubled closing quote:  {"a":"why?""}                         -> {"a":"why?"}
 * Only meaningful to call after a real JSON.parse failure.
 */
export function repairMalformedJson(text) {
    return text
        .replace(/,(\s*,)+/g, ',')       // collapse ANY run of commas: [0,1,,,5] -> [0,1,5]
        .replace(/,\s*([\]}])/g, '$1')   // strip trailing commas:      [0,1,]    -> [0,1]
        .replace(/([\[{])\s*,/g, '$1')   // strip leading commas:       [,0,1]    -> [0,1]
        // Doubled closing quote after string content: `plating?""}` -> `plating?"}`.
        // The preceding char must be string content (not `:`, `,`, `[`, `{`, whitespace or a
        // quote) so legitimate empty strings (`"k":""`, `["",""]`) are never touched.
        .replace(/([^:\s,\[{"\\])""(\s*[,}\]])/g, '$1"$2');
}

/**
 * Parse JSON, attempting a single deterministic repair pass if the first parse fails.
 * This lets us salvage malformed structured output WITHOUT paying for a full LLM
 * retry. It never corrupts valid JSON because the repair only runs after a parse failure,
 * and if the repair doesn't change the string we rethrow the original error.
 *
 * @returns {{parsed: any, repaired: boolean}}
 * @throws  the original parse error if neither the raw nor repaired text is valid JSON
 */
export function parseJsonWithRepair(text) {
    try {
        return { parsed: JSON.parse(text), repaired: false };
    } catch (firstErr) {
        const repaired = repairMalformedJson(text);
        if (repaired === text) throw firstErr; // nothing to fix — genuinely broken
        return { parsed: JSON.parse(repaired), repaired: true };
    }
}
