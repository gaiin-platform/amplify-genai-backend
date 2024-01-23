

// By default, we stream data back to the prompt source
// and this is the incremental results for a LLM response.
export const promptSource = "prompt";
export const defaultSource = promptSource;

// This source provides metadata needed by the client to properly handle
// the response data that is streamed to it.
export const metaSource = "meta";
