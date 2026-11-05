# Release Notes: v0.9.5 (New UI) - Backend

**Release Date**: 2026-11-06 (planned)
**Previous Version**: v0.9.4 (September 10, 2026)
**Branch**: `dev` (merges to `main` at release)
**Companion releases**:
- Frontend: `amplify-genai-frontend/RELEASE_NOTES_NEW_UI.md` (v0.9.5). **Deploy this backend first.**
- Shared library: [`pycommon`](https://github.com/gaiin-platform/pycommon), pinned at `v0.1.2` (see [section 3](#3-pycommon-v012)).

---

## Summary

Version 0.9.5 is the backend release that supports the Amplify **New UI**. Compared with `v0.9.4` it contains **47 commits** and **177 changed files**. The New UI is off by default and switched on by an admin, so deploying this release does not change what any user sees.

The release has two kinds of content, and this document keeps them apart:

- **[Part 1 - Before you upgrade](#part-1---before-you-upgrade)**: things an operator must know or do that go beyond a normal `deploy`. Read this part first.
- **[Part 2 - What's new](#part-2---whats-new)**: the changes in this release, grouped by area.

### Highlights at a Glance
- :warning: **Deploy the backend before the frontend.** The New UI calls routes and reads settings that only exist in this release.
- :warning: **Tooling change** - the backend now uses `osls` (an open-source fork of Serverless Framework v3) instead of upstream `serverless`. Run `npm install` at the repo root and use `npx sls`. A globally installed upstream `serverless` must be removed.
- :warning: **Parameter Store populators are now per stack**, using a shared handler from `pycommon` v0.1.2. The central `ParameterStorePopulatorArn` CloudFormation export is gone.
- :warning: **New defaults** - prompt highlighter and memory are **off** until an admin enables them (existing deployments included).
- :rocket: **New admin settings** - `newUi` and `amplifyHelper` feature flags, `deploymentFeatures`, and `systemPrompts`.
- :link: **OpenAI-compatible API** - `POST /v1/chat/completions` and `GET /v1/models`.
- :package: **No data migration** is required.

### Terms used in this document
- **Feature flag**: an on/off switch stored in the admin DynamoDB table and edited in the Admin panel. Flags can have per-user and per-group exceptions.
- **osls**: [`oss-serverless/osls`](https://github.com/oss-serverless/osls), an open-source fork that continues Serverless Framework **v3**. It keeps the `sls` / `serverless` command names.
- **Compose**: `serverless-compose.yml`, the file that deploys several services in dependency order from the repo root.
- **Stage**: the deployment environment name (`dev`, `staging`, `prod`).

---

# Part 1 - Before you upgrade

## 1. Deploy order

| Step | What | Notes |
|---|---|---|
| 1 | Check CloudFormation imports | See [section 4](#4-parameter-store-changes). |
| 2 | Install tooling (`npm install` at repo root) | See [section 2](#2-osls-replaces-upstream-serverless). |
| 3 | Make sure build machines can reach GitHub | pycommon is installed from `github.com/gaiin-platform/pycommon`. |
| 4 | (Optional) set Bedrock prompt caching | See [section 7](#7-optional-bedrock-prompt-caching). |
| 5 | Deploy the compose services | `npx sls deploy --stage <stage>` from the repo root. |
| 6 | Deploy the standalone services | See [below](#standalone-services). |
| 7 | Deploy the frontend | See the frontend release notes. |
| 8 | Admin: turn on the `newUi` flag | See the frontend release notes. Nothing changes for users before this. |

Back up production DynamoDB tables before upgrading, and use the CloudFormation changeset plugin (`CHANGE_SET_BOOLEAN: true` in your var file) to review infrastructure changes first, as in v0.9.0.

### Services changed in this release

Every service below changed (new `pycommon` pin, per-stack Parameter Store populator, or code). Redeploy all of them.

- **Compose services** (deploy from the repo root): `amplify-lambda`, `amplify-assistants`, `amplify-lambda-admin`, `amplify-lambda-api`, `amplify-lambda-artifacts`, `amplify-lambda-js`, `amplify-lambda-ops`, `chat-billing`, `data-disclosure`, `embedding`, `object-access`.
- <a id="standalone-services"></a>**Standalone services** (not in compose; deploy each from its own directory): `amplify-agent-loop-lambda`, `amplify-lambda-assistants-api`, `amplify-lambda-assistants-api-office365`, `amplify-lambda-assistants-api-google`. `amplify-lambda-python-base` also changed (dependency pins only).

Commands (replace `<stage>`):

```bash
# From the repo root - one service, or all compose services
npx sls amplify-lambda:deploy --stage <stage>
npx sls deploy --stage <stage>

# Standalone service
cd amplify-lambda-assistants-api && npx sls deploy --stage <stage>
```

> **Not tested by us:** deploying a standalone service with `npx sls` from its own directory (the standalone directories have no `package.json`, so `npx` has to find the root `node_modules`). If `npx sls` is not found there, run it as `../node_modules/.bin/sls deploy --stage <stage>`.

## 2. osls replaces upstream `serverless`

Commit `f1e6db5e` ("switch to osls and implement caching") changed the Serverless tooling.

**What changed**

| Item | Before (v0.9.4) | Now |
|---|---|---|
| Framework | `serverless ^3.38.0` (upstream) | `"serverless": "npm:osls@^3.79.0"` (osls, installed under the name `serverless`) |
| Compose plugin | `@serverless/compose ^1.3.0` | `@osls/compose ^1.9.2` |
| Framework version in each `serverless.yml` | `frameworkVersion: "3"` | unchanged (osls is v3) |
| `serverless-compose.yml` | - | **unchanged** |

`package.json` uses an **npm alias**: the osls package is installed in `node_modules` under the name `serverless`, so `npm install` at the repo root puts an `sls` command (and `serverless`, `osls`) in `node_modules/.bin`. `amplify-lambda-js/package.json` has the same alias for local runs.

**What you need to do**

1. Use Node.js **20.19 or newer** (20.19+, 22.13+, or 24+). osls declares this in its `engines`. `amplify-lambda-js` itself targets Node 22 (`engines >=22`, runtime `nodejs22.x`).
2. Run `npm install` at the repo root. This installs osls, `@osls/compose` and the plugins used by the services.
3. Run the framework with **`npx sls ...`** so you get the local copy.
4. If you ever installed upstream Serverless globally, remove it:
   ```bash
   npm uninstall -g serverless
   ```
   osls does **not** hand over to a project-local install the way upstream does, so a global upstream `serverless` on your `PATH` will run instead of the local osls.
5. You do not need `npm install --save-dev serverless-offline` or `sls plugin install -n serverless-python-requirements`. Both plugins are already in the root `package.json` and installed by step 2.

There are no new deploy flags or compose commands. The existing commands (`service:deploy --stage <stage>`, `deploy --stage <stage>`) work the same way. Compose does not support `serverless offline`; run offline from the service directory as described in `README.md`.

**What "caching" in that commit means.** Despite the commit title, the commit contains **no build or deploy caching** change. The "caching" is **Bedrock prompt caching** in `amplify-lambda-js` (see [section 7](#7-optional-bedrock-prompt-caching)). It is **off by default**. Nothing is written to disk by the deploy and there is no cache to clear. Deploys behave the same on the first and later runs.

**Local shims and overrides in `package.json`**

`package.json` also contains:

- `"decompress": "file:./npm-shims/decompress"` and `"simple-git": "file:./npm-shims/simple-git"` - small local packages that wrap patched versions of two libraries so that Serverless v3 can still `require()` them.
- `"overrides": { "tar": "^7.5.22", ... }` - forces a patched `tar`.

They were added (commit `de2dafa5`) to clear security alerts on dependencies pinned by upstream Serverless v3, and `npm install` still installs them. We have **not** tested whether osls still needs them. Leave them in place. `npm-shims/README.md` was written for the earlier `serverless@3.40.0` setup and is out of date.

**Other places that still say `serverless`.** `README.md` deploy examples and several per-service READMEs (`amplify-lambda`, `amplify-lambda-artifacts`, `amplify-lambda-ops`, `amplify-lambda-api`, `amplify-lambda-admin`) still mention `npm install -g serverless` and `sls deploy`. If you follow them, use `npx sls` and do not install upstream globally.

## 3. pycommon v0.1.2

[`pycommon`](https://github.com/gaiin-platform/pycommon) is the shared Python library used by every Python service. This release moves all services to **v0.1.2**.

- 15 `requirements.txt` files pin `git+https://github.com/gaiin-platform/pycommon.git@v0.1.2`: `amplify-agent-loop-lambda`, `amplify-assistants`, `amplify-lambda`, `amplify-lambda-admin`, `amplify-lambda-api`, `amplify-lambda-artifacts`, `amplify-lambda-assistants-api`, `amplify-lambda-assistants-api-google`, `amplify-lambda-assistants-api-office365`, `amplify-lambda-ops`, `amplify-lambda-python-base`, `chat-billing`, `data-disclosure`, `embedding` and `object-access`.
- **`v0.1.2` is a Git branch in the pycommon repository, not a tag**, and pycommon's `pyproject.toml` still reports `version = "0.1.0"`. `pip` installs whatever the branch points at when you build. To record what you shipped, note the commit your build used:
  ```bash
  git ls-remote https://github.com/gaiin-platform/pycommon.git refs/heads/v0.1.2
  ```
- Build machines need outbound access to GitHub, as they did for v0.1.1.
- `scripts/requirements.txt` (migration and backup scripts) and `amplify-lambda/markitdown/markitdown.sh` (the markitdown layer) still reference **v0.1.1**. No script imports pycommon, so the scripts are unaffected; the markitdown layer keeps the older copy of the pycommon modules it bundles.

**What changed in pycommon, in brief** (details in the pycommon repository's `RELEASE_NOTES_v0.1.2.md`):

| Change | Effect for operators |
|---|---|
| Shared Parameter Store handler (`cfn_handler`) | Each stack runs its own populator. See [section 4](#4-parameter-store-changes). |
| LZW unicode fix | Conversations and other compressed payloads containing emoji, non-Latin scripts or the text `U+` are now stored and read correctly. See [section 8](#8-conversation-storage-lzw-compatibility). |
| Clearer rate-limit error messages (`get_rate_limit_notice`) | Added to the library; no backend service calls it yet. |
| JWT verification moved from `python-jose` to PyJWT | Same checks (RS256, issuer, audience, access token). `python-jose` is no longer pulled in by pycommon. |
| Model rate table optional when a flat cost is given | `MODEL_RATE_TABLE` is only required when `flat_cost` is not set. |
| Dependency updates; Python 3.11+ required | All backend Lambdas already run `python3.11`. |

## 4. Parameter Store changes

Each stack now has its **own** populator Lambda, instead of one central function in `amplify-lambda`.

- Every Python stack except the Google and Office365 assistant-API stacks declares a `param_populator` function (`ast_param_populator` in `amplify-assistants`) and a `Custom::ParamStorePopulate` resource. The handler is `pycommon.deployment.parameter_store_sync.cfn_handler`.
- On each deploy, the populator writes every environment variable of that stack whose value starts with `<service>-<stage>-` to `/amplify/<stage>/<service>/<VARIABLE>`. It needs `SERVICE_NAME` and `STAGE` in its environment. On stack delete it does nothing, so parameters are kept.
- Each stack's role gets `ssm:GetParameter` and `ssm:PutParameter` on `/amplify/<stage>/<service>/*` only.
- `amplify-lambda` **no longer has** the central `parameter_store_populator` function or the CloudFormation export `<stage>-ParameterStorePopulatorArn`. The resource that used it was renamed from `ParameterStoreAutoPopulate` to `ParamStoreAutoPopulate`.

**Migration check (do this before deploying an existing environment).** CloudFormation refuses to remove an export while another stack imports it. Nothing in this repository imports `ParameterStorePopulatorArn`, but a stack you manage elsewhere (for example in your own infrastructure repo) could. Run:

```bash
aws cloudformation list-imports --export-name <stage>-ParameterStorePopulatorArn
```

- An error saying the export is not imported by any stack (or an empty result) means it is safe to proceed.
- If stacks are listed, update them to stop importing it before you deploy `amplify-lambda`.

No other migration step is needed for Parameter Store. Existing `/amplify/<stage>/...` parameters stay in place. Re-running `scripts/populate_parameter_store.py` is **not** required for this release (see [section 7](#7-optional-bedrock-prompt-caching) if you want to set the new optional keys):

```bash
python3 scripts/populate_parameter_store.py --stage <stage> --dep-name <dep-name>   # optional: --region, --dry-run
```

This describes the code. We have not run the upgrade against a live CloudFormation environment.

## 5. Feature flags and admin settings

All of these are served to the frontend by `GET /amplifymin/feature_flags` in `amplify-lambda-admin`. The frontend release notes explain how to turn the New UI on; this section is the backend side.

### Feature flags

| Flag | Default | Notes |
|---|---|---|
| `newUi` | **off** | Gates the whole New UI. Added to `base_feature_flags/feature_flags.py`. |
| `amplifyHelper` | **off** | Gates the Amplify Helper (a built-in guide assistant). Also a base flag. |
| `notebook` | not present | **Not** a base flag. The frontend reads it. An admin must create it with **Add Feature** if wanted. |
| `halloween` | not present | **Not** a base flag. Seasonal theme. Create with **Add Feature** if wanted. |

- **Existing deployments**: the first time `GET /amplifymin/feature_flags` (or the admin configs read) runs, any missing base flag is added to the admin table at its default. You do not have to create `newUi` yourself. Deleting a base flag in the Admin panel re-creates it at its default on the next read.
- **Per-user and per-group exceptions**: an entry in `userExceptions` or `amplifyGroupExceptions` **inverts** the flag for that user. The API returns a plain true/false per flag.
- **Naming**: the backend accepts any flag name (`^.*$`) and does not normalize it. The Admin panel's Add Feature form converts "New UI" to the exact name `newUi`. A flag saved by hand as `newUI` is a different, unused flag.
- `get_user_feature_flags` returns an error (not "not an admin") if the admin lookup fails. The frontend retries and falls back to the Classic UI.

### `deploymentFeatures` (new admin config)

Stored in the admin table (`<service>-<stage>-admin-configs`) under `config_id = deploymentFeatures`, created with defaults the first time it is read, and returned inside the `/feature_flags` payload.

| Field | Default | Meaning |
|---|---|---|
| `availability.promptHighlighter` | **false** | Changed default. |
| `availability.memory` | **false** | Changed default. |
| `availability.artifacts` | true | |
| `availability.webSearch` | true | |
| `availability.codeInterpreter` | true | |
| `allowClassicUiSwitch` | true | When false, users cannot choose or stay on the Classic UI. |
| `routingEnabled` | false | Model routing for ordinary chat. |
| `routingModelId` | empty (max 200 chars) | Model the router uses. Empty means the built-in default. |
| `announcement` | none | Site-wide banner: `enabled`, `message`, `id`, `expiresAt`. The schema allows 1000 characters; the Admin UI limits the message to 500. |

**Must know**
- On an existing deployment with no `deploymentFeatures` record, **prompt highlighter and memory become unavailable** until an admin turns them on (Admin panel, Deployment tab).
- The chat service enforces the availability switches for web search, artifacts and code interpreter on the server. We found no server-side check for prompt highlighter or memory, so those two are enforced by the frontend.
- If the admin table cannot be read, the chat services turn optional features **off**. One code path in `get_user_feature_flags` (when reading `deploymentFeatures` raises an error) reports highlighter and memory as **on**; that is a known inconsistency.
- **Classic-switch enforcement** (`amplify-lambda/state/usersettings.py`): when `allowClassicUiSwitch` is false, saving `uiPreference: "classic"` is rewritten to `"new"`. If the admin table cannot be read, it is rewritten as well. If there is no record, no `allowClassicUiSwitch` key, or the `AMPLIFY_ADMIN_DYNAMODB_TABLE` variable is missing, the Classic value is saved. The variable and read access are configured in `amplify-lambda/serverless.yml`.

### `systemPrompts` (new admin config)

Versioned prompt overrides stored under `config_id = systemPrompts`. Keys: `ordinaryChat.base`, `webSearch.use`, `artifacts.generate`, `codeInterpreter.use`, `amplifyHelper.base`. Empty text means "use the built-in prompt". Each prompt is limited to 16 KiB. A save **replaces the whole record**.

- The built-in `amplifyHelper.base` prompt (`amplify-lambda-js/common/systemPrompts.js`) names "Vanderbilt Amplify" and `amplify@vanderbilt.edu`. On a white-labeled deployment, set your own text in the Admin panel (System Prompts tab) before enabling the `amplifyHelper` flag. The Admin text box always shows the built-in Vanderbilt text as its starting point.
- The System Prompts and Deployment tabs exist only in the New UI's admin panel, not in the Classic admin.

## 6. New API routes

| Route | Service | Used for |
|---|---|---|
| `POST /artifacts/rename` | `amplify-lambda-artifacts` | Renaming artifacts. |
| `POST /files/rename` | `amplify-lambda` | Renaming library files. |
| `GET /state/share/sent` | `amplify-lambda` | What the current user has shared with others. The New UI share dialog uses it to show share history across browsers and devices. API keys need the `share` or `full_access` scope. |
| `POST /v1/chat/completions` | `amplify-lambda` | OpenAI-compatible chat. |
| `GET /v1/models` | `amplify-lambda` | OpenAI-compatible model list. |

- The OpenAI-compatible routes use an Amplify API key (`Authorization: Bearer amp-...`) with the `chat` or `full_access` scope for completions. See `docs/openai-compatible-chat-api.md`. Streaming is answered as a buffered replay, not token by token.
- `/v1/chat/completions` was added to the `ApiGatewayTimeoutConfig` custom resource (180 s function timeout).
- A New UI feature that calls a route missing from an older backend will fail, which is why the backend goes first.
- **`GET /state/share/sent` limits.** A record of each share is stored only by this release's `share_with_users`, so history starts at upgrade: shares made before it do not appear. The query returns at most 100 records per user, taken from the oldest end, so a user with more than 100 recorded shares will not see the newest ones. The share dialog also keeps its own history in the browser, so the list is a supplement and a failed call is ignored.

## 7. Optional: Bedrock prompt caching

New variables for `amplify-lambda-js`, read from Parameter Store with defaults, so nothing needs to be set:

| Variable | Default | Meaning |
|---|---|---|
| `BEDROCK_PROMPT_CACHING` | `off` | `on` enables explicit prompt caching for supported Claude models on Bedrock. |
| `BEDROCK_PROMPT_CACHING_POINTS` | `tools,system,history,tail` | Which checkpoints to cache (any subset). |
| `BEDROCK_PROMPT_CACHING_MODELS` | empty | Comma list of regexes to restrict which model ids are cached. Empty means all supported Claude families. |

To use them, set the values in your `<stage>-var.yml` (see `dev-var.yml-example`) and run `scripts/populate_parameter_store.py` for the stage, or write `/amplify/<stage>/BEDROCK_PROMPT_CACHING` and the others directly. Each LLM call logs a `[CACHE-USAGE]` line (cache read tokens, cache write tokens, prompt tokens). Design notes: `docs/chat-request-architecture/10-bedrock-prompt-caching-plan.md`.

## 8. Conversation storage (LZW) compatibility

Conversations and some other payloads are compressed with an LZW routine that exists in three copies: `pycommon/lzw.py` (Python services), `amplify-lambda-js/common/lzwCompression.js`, and the frontend's `utils/app/lzwCompression.ts`. Commit `2e7a223c` and pycommon v0.1.2 fix how non-ASCII characters are encoded.

- **The bug**: characters were written as `U+` plus hex. Python wrote 4 digits (so emoji came out as 5-digit tags that were read back as garbage); JavaScript wrote unpadded hex (so U+0100-U+0FFF, such as Cyrillic, Greek, Hebrew, Arabic and Thai, got 3-digit tags that could not be read). A typed `U+0041` was read back as `A`.
- **The fix**: all three copies now write UTF-16 code units above 255 as `U+` plus exactly 4 hex digits (an emoji is two tags), escape a literal `U+`, and still read the old 3-digit tags. The three copies share identical test vectors.
- **Old data**: conversations written before this release still read, with one limit. Data that was already unreadable (emoji written by the Python encoder, or 3-digit tags directly followed by a hex character) stays unreadable. No migration or re-save is needed or possible.
- **Mixed versions**: new readers read old data. Old readers read new data correctly for ordinary text and non-Latin BMP scripts. For emoji an old Python reader returns two separate surrogate characters instead of one. Deploy **all** Python services on pycommon v0.1.2 and the frontend of this release **together** to avoid a window like this.

## 9. Stored data

No migration scripts are required.

- **Conversations**: saves accept an optional `expectedVersion` (the S3 object's last-modified time in milliseconds). If the stored copy is newer, the save returns `CONFLICT` instead of overwriting it. Clients that do not send it behave as before. A known limit: the check relies on S3 timestamps (one-second resolution noted in the code).
- **Artifacts**: version entries may carry an optional `lineageId`. Entries without it still load.
- **Shares and groups**: `object-access` gained group grants (`group_grants.py`).
- **Code interpreter sessions**: `amplify-assistants` adds a DynamoDB index (`ActivityIndex`, on `activityBucket` and `updatedAt`) to the code-interpreter table and a scheduled Lambda (`reap_idle_code_interpreter_sessions`, every 5 minutes) that closes idle sessions. Both are created by the normal deploy.

## 10. Other operator notes

- Python dependencies were bumped across services for security (for example Pillow 12.2.0). Rebuild layers as part of a normal deploy.
- The Google and Office365 assistant-API stacks do not auto-populate Parameter Store.
- Variable templates (`dev-var.yml-example`) gained only the three optional Bedrock caching entries.

---

# Part 2 - What's new

### Feature flags and admin configuration
- `newUi` and `amplifyHelper` base flags; `deploymentFeatures` (availability switches, Classic-switch policy, model routing, announcements); `systemPrompts`; an admin-configurable router model; an announcement schema.
- Feature-flag lookups now report failures instead of treating them as "not admin".
- Prompt highlighter and memory default to off. Chat respects the web-search, artifacts and code-interpreter switches and each user's choice to disable web search.

### OpenAI-compatible API
- `POST /v1/chat/completions` and `GET /v1/models`, with documented reasoning controls and rejected-parameter behavior (`docs/openai-compatible-chat-api.md`). Latest commit adds support for more OpenAI request parameters.
- Support for the OpenAI response format (commit `c0d5e822`).

### Chat, models and routing
- Request handling and routing now use the deployment configuration; routing through the API was improved and a routing usage-recording error fixed.
- Bedrock: explicit prompt caching (off by default), hardened structured-output parsing for Bedrock GPT-5.6 models with raw-response logging on stream errors, socket-timeout handling and better request cancellation, and the adaptive-reasoning pattern now includes Haiku 5+.

### Artifacts
- Rename artifacts (`/artifacts/rename`); version entries carry a `lineageId` so saved versions stack under one artifact instead of overwriting; artifact edits return the updated content rather than placeholders; a cut-off artifact can be resumed instead of regenerated; artifact type rules with tests.

### Conversations
- Optional `expectedVersion` conflict detection and the LZW unicode fix (see Part 1, sections 8 and 9).

### Files, sharing and groups
- Rename library files (`/files/rename`) with validation. Each share is now recorded for the sender, and `GET /state/share/sent` returns that history.
- A failed share now says when the recipient has never signed in to Amplify.
- Group grants in `object-access`; assistant sharing persists tools; improved switching of an assistant between private and group; group assistant access and skills fixed on the agent path.

### Amplify Helper
- Backend configuration and routing for the Helper (`amplifyHelper.base` system prompt, `amplifyHelper` flag).

### MCP, code interpreter and agents
- MCP calls use the session id issued at `initialize`, with clearer errors; MCP and code-interpreter status messages; connectors and assistants no longer conflict in one chat.
- Code interpreter sessions are used more efficiently, and idle sessions are reaped on a schedule.
- More descriptive errors for agents and scheduled tasks, and better viewing of earlier scheduled-task run logs.

### API keys
- Non-updatable fields are ignored in API key updates.

### Infrastructure and dependencies
- Per-stack Parameter Store populators on pycommon v0.1.2; osls and `@osls/compose`; security updates for pip and npm dependencies across services.

---

## Stats

Measured with `git diff origin/main...dev` on 2026-11-05; commits keep landing, so re-run before publishing. Line counts include regenerated lockfiles.

| Metric | Count |
|--------|-------|
| Commits | 47 |
| Files changed | 177 |
| Insertions | +23,414 |
| Deletions | -45,128 |
| Merged pull requests | 4 (#388, #402, #403, #404) |

## Contributors

- **Max Moundas** - New UI backend, feature flags, admin configs, OpenAI-compatible API, osls, prompt caching
- **Harsha Raj Kumar** - Artifact lineage, edit, and cut-off fixes
- **Karely Rodriguez** - Parameter Store standardization, Bedrock client timeouts
- **Jagadeesh Reddy Vanga** - Dependabot security fixes

## Links

- Frontend release notes: `amplify-genai-frontend/RELEASE_NOTES_NEW_UI.md`
- pycommon: https://github.com/gaiin-platform/pycommon (`RELEASE_NOTES_v0.1.2.md`)
- Previous release: `RELEASE_NOTES_v0.9.0.md` (Parameter Store migration, still relevant if you are upgrading from before v0.9.0)
- Full deployment guide: https://github.com/gaiin-platform/.github/blob/main/profile/README.md
- OpenAI-compatible API: `docs/openai-compatible-chat-api.md`

---

## Draft status (delete this section before publishing)

Open items for the release owner. These are findings from the audit and are not stated as facts above.

1. **`GET /state/share/sent` route (new, uncommitted).** The handler existed but had no `http` event. The `user_get_sent_shares` function was added to `amplify-lambda/serverless.yml` and is listed in section 6. The change is **uncommitted**, and the route has **not been deployed or called** by us. After deploying `amplify-lambda`, check it: `curl -H "Authorization: Bearer <token>" https://<api-base>/state/share/sent` should return `{"success": true, "items": [...]}`.
2. **pycommon dependency conflicts at the current v0.1.2 branch tip.** `object-access` imports `jose` and pins `jose==1.0.0`, an unrelated package that does not provide `jwt`/`JWTError`; it previously worked only because pycommon pulled in `python-jose`. `amplify-lambda-assistants-api` pins `certifi==2025.6.15` and `pyjwt[crypto]==2.13.0`, which are older than pycommon's new minimums (`certifi>=2026.10.23`, `PyJWT>=2.15.1`). Verify both builds before publishing. Not run by us.
3. **Version numbers.** Both `package.json` files still say `0.9.0`; the last tag is `v0.9.4`.
4. Not verified by us: any deployed environment, a real `pip` resolve, plugin loading under osls, `npx sls` from standalone service directories, IAM coverage for each stack's populator.
