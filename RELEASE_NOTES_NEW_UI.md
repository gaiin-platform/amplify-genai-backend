# Release Notes: New UI (Backend)

**Status**: DRAFT. Scope is "must-knows and requirements outside a normal service deployment".
**Compared against**: `origin/main` (`3d8f4ed1`) → `dev` (45 commits, 165 files)
**Deploy order**: backend **before** the frontend (see `amplify-genai-frontend/RELEASE_NOTES_NEW_UI.md`).

---

## TL;DR

1. Deploy all services changed on `dev`. The ones the New UI depends on directly are `amplify-lambda-admin`, `amplify-lambda`, `amplify-lambda-artifacts` and `object-access`. `amplify-lambda-js` carries the chat changes.
2. Nothing turns the New UI on by itself. The `newUi` flag is created **disabled**. An admin enables it.
3. Parameter Store population was restructured (details below). Check it before deploying.
4. No data migration scripts are required. New stored fields are optional.

---

## 1. Feature flag: `newUi`

- Added to `amplify-lambda-admin/base_feature_flags/feature_flags.py` as `"newUi": False` (also `"amplifyHelper": False`).
- Existing deployments: `GET /amplifymin/feature_flags` adds any missing base flag to the admin table the first time it is read, so no manual step is needed to create it.
- Per-user evaluation: an entry in `userExceptions` or `amplifyGroupExceptions` **inverts** the flag for that user. The API returns a plain boolean.
- The admin update schema accepts any flag name, so an admin can also create flags by hand.
- **`notebook` and `halloween` are NOT base flags.** The frontend reads them, but an admin must add them via Add Feature if wanted.
- `get_user_feature_flags` now returns an error if the admin lookup fails, instead of reporting "not admin". The frontend retries on error and fails closed to Classic.

## 2. `deploymentFeatures` admin config (new)

Stored in the admin table under `config_id = deploymentFeatures` and returned inside the `/feature_flags` payload. Created with defaults on first read if absent.

| Field | Default | Meaning |
|---|---|---|
| `availability.promptHighlighter` | **false** | Changed default. |
| `availability.memory` | **false** | Changed default. |
| `availability.artifacts` / `webSearch` / `codeInterpreter` | true | |
| `allowClassicUiSwitch` | true | When false, users cannot stay on or switch to Classic. |
| `routingEnabled` | false | AI model routing for ordinary chat. |
| `routingModelId` | empty | Model used by the router; empty uses the built-in default. |
| `announcement` | none | Site-wide banner: `enabled`, `message` (max 1000), `id`, `expiresAt`. |

**Must know:** on an existing deployment with no `deploymentFeatures` record, **prompt highlighter and memory become unavailable** until an admin enables them. Chat honors this server-side too (web search, artifacts and code interpreter respect the availability switches).

Also new: a `systemPrompts` admin config (versioned prompts such as `ordinaryChat.base`, `webSearch.use`, `artifacts.generate`, `codeInterpreter.use`, `amplifyHelper.base`). Empty text means the built-in prompt.

**Classic-switch enforcement** (`amplify-lambda/state/usersettings.py`): when `allowClassicUiSwitch` is false, saving `uiPreference: "classic"` is rewritten to `"new"`. If the admin table cannot be read, it **fails closed** the same way. `AMPLIFY_ADMIN_DYNAMODB_TABLE` and read access to it are already configured in `amplify-lambda/serverless.yml`.

## 3. New API routes the New UI calls

| Route | Service | Used for |
|---|---|---|
| `POST /artifacts/rename` | `amplify-lambda-artifacts` | Renaming artifacts. |
| `POST /files/rename` | `amplify-lambda` | Renaming library files. |
| `/state/share/sent` (read) | `amplify-lambda` | "Sent" shares list. Frontend route: `pages/api/share/sent.ts`. |
| `POST /v1/chat/completions`, `GET /v1/models` | `amplify-lambda` | OpenAI-compatible API. Auth: Amplify API key with `chat` or `full_access`. See `docs/openai-compatible-chat-api.md`. |

Frontend features that call routes missing from an old backend will fail, so deploy the backend first. `/v1/chat/completions` is also added to the `ApiGatewayTimeoutConfig` custom resource (180 s function timeout).

## 4. Parameter Store changes (read before deploying)

- Each stack now has **its own** `param_populator` Lambda, running a shared handler from **`pycommon` v0.1.2** (`git+https://github.com/gaiin-platform/pycommon.git@v0.1.2`). Build machines need GitHub access, same as for v0.1.1.
- `amplify-lambda` **no longer has** the central `parameter_store_populator` function or its CloudFormation export `${stage}-ParameterStorePopulatorArn`. Nothing in this repo imports it. **Check that no stack outside this repo does**, because CloudFormation refuses to delete an export that is still imported.
- Each stack's role gets `ssm:PutParameter` / `ssm:GetParameter` on `/amplify/<stage>/<service>/*` only.
- The Google and Office365 assistant-API stacks do not auto-populate. `amplify-lambda-js` had the populator added and then removed in the same range.

## 5. Build and dependency notes

- Run `npm install` at the repo root first. `package.json` now uses local shims (`npm-shims/decompress`, `npm-shims/simple-git`) and a `tar` override to clear security alerts.
- Python dependencies were bumped across services for security (for example Pillow 12.2.0, aiohttp 3.14.3 for `scripts/`). Rebuild layers and requirements as part of a normal deploy.

## 6. Stored-data changes (no migration needed)

- **Conversations**: saves accept an optional `expectedVersion` (S3 LastModified, ms). A newer stored copy returns `CONFLICT`. Old clients omit it and behave as before.
- **Artifacts**: each version entry may carry optional `lineageId` and `version`. Older entries without them still load.
- **Shares and groups**: `object-access` gained group grants (`group_grants.py`), covered by tests.

## Known gaps to confirm before release
- I could not check what is deployed in any environment, only the code.
- Version number and release date are not set.
