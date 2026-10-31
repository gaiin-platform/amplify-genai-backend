'use strict';

// serverless@3.x and @serverless/utils do `const decompress = require('decompress'); await decompress(input, output, opts)`.
// The upstream decompress@4.2.1 is unpatched (path traversal / symlink advisories) and unmaintained; the maintained
// fork is ESM-only, so load it lazily via dynamic import (works on every supported Node, no require(esm) needed).
// All callers only invoke it as a function and await the result, so an async wrapper is a drop-in.
module.exports = async function decompress(...args) {
  const { default: patched } = await import('decompress-patched');
  return patched(...args);
};
