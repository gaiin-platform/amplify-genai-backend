# npm-shims

Local drop-in packages referenced from `overrides` in `package.json` (and `amplify-lambda-js/package.json`).

We pin `serverless@3.40.0` / dashboard plugin `7.2.3`. Serverless v3 is end-of-life and hard-pins
`decompress@4` (no patched release exists), `simple-git@3` and `tar@6`, which carry **critical** advisories.
`tar` is overridden directly to `^7.5.22` (serverless only calls `tar.x({ cwd, strip })`, unchanged in v7).
The other two have breaking export shapes in their patched versions, so these shims restore the old shape:

| shim | patched implementation | why a shim |
| --- | --- | --- |
| `simple-git` | `simple-git@4.0.2` | v4's CJS export is no longer callable; dashboard-plugin calls `simpleGit()` at module load |
| `decompress` | `@xhmikosr/decompress@^11.1.4` | maintained fork is ESM-only; serverless `require()`s it and calls it as a function |

Remove these (and the `overrides`) once serverless is upgraded past v3.
