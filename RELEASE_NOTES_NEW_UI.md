# Release Notes: v0.9.5 (New UI) - Backend

**Release Date**: 2026-11-06 (planned)
**Previous Version**: v0.9.4 (September 10, 2026)
**Branch**: `dev` (merges to `main` at release)
**Companion releases**:
- Frontend: `amplify-genai-frontend/RELEASE_NOTES_NEW_UI.md` (v0.9.5). **Deploy this backend first.**
- Shared library: [`pycommon`](https://github.com/gaiin-platform/pycommon), pinned at `v0.1.2` (see [section 3](#3-pycommon-v012)).

---

## Summary

Version 0.9.5 is the backend release that supports the Amplify **New UI**. Compared with `v0.9.4` it contains **51 commits** and **234 changed files**. The New UI is off by default and switched on by an admin, so deploying this release does not change what any user sees.

The release has two kinds of content, and this document keeps them apart:

- **[Part 1 - Before you upgrade](#part-1---before-you-upgrade)**: things an operator must know or do that go beyond a normal `deploy`. Read this part first.
- **[Part 2 - What's new](#part-2---whats-new)**: the changes in this release, grouped by area.

### Highlights at a Glance
- :warning: **Deploy the backend before the frontend.** The New UI calls routes and reads settings that only exist in this release.
- :warning: **Deploy `amplify-lambda` to completion before `embedding`.** `embedding` reads a Parameter Store value that `amplify-lambda` creates during its own deploy (see [section 1](#1-deploy-order)).
- :warning: **Tooling change** - the backend now uses `osls` (an open-source fork of Serverless Framework v3) instead of upstream `serverless`. Run `npm install` at the repo root and use `npx sls`. A globally installed upstream `serverless` must be removed.
- :warning: **Parameter Store populators are now per stack**, using a shared handler from `pycommon` v0.1.2. The central `ParameterStorePopulatorArn` CloudFormation export is gone.
- :warning: **New defaults** - prompt highlighter and memory are **off** until an admin enables them (existing deployments included).
- :rocket: **New admin settings** - `newUi` and `amplifyHelper` feature flags, `deploymentFeatures`, and `systemPrompts`.
- :link: **OpenAI-compatible API** - `POST /v1/chat/completions` and `GET /v1/models`.
- :mag: **RAG pipeline upgrade** - new uploads are processed by a new document pipeline (Nova multimodal embeddings in a DynamoDB vector table, Textract OCR, page images), with an admin tool to move existing documents. See [section 9](#10-rag-pipeline-upgrade).
- :package: **No mandatory data migration.** Existing conversations, artifacts and documents keep working. Moving existing documents to the new RAG storage is optional and admin-driven ([section 9](#10-rag-pipeline-upgrade)).

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
| 4 | Deploy `amplify-lambda` and **wait until it has finished** | `npx sls amplify-lambda:deploy --stage <stage>` from the repo root. |
| 5 | Deploy the other compose services, including `embedding` | `embedding` must not start before step 4 is complete. See the note below. |
| 6 | Deploy the standalone services | See [below](#standalone-services). |
| 7 | Deploy the frontend | See the frontend release notes. |
| 8 | Admin: turn on the `newUi` flag | See the frontend release notes. Nothing changes for users before this. |

> **`amplify-lambda` must finish deploying before you deploy `embedding`.**
> - `embedding/serverless.yml` now reads the Parameter Store value `/amplify/<stage>/amplify-<dep-name>-lambda/RAG_MIGRATION_TABLE` with **no fallback value**. `amplify-lambda`'s own Parameter Store populator writes that value while `amplify-lambda` deploys (see [section 4](#4-parameter-store-changes)). `embedding` also imports several CloudFormation exports from the `amplify-lambda` stack (API Gateway ids and the document queues).
> - If `embedding` deploys before `amplify-lambda` has finished, or at the same time, expect the `embedding` deploy to fail because that variable cannot be resolved. Serverless stops on an `ssm` variable that has no value and no default. We did not reproduce this failure. If it happens, run the `embedding` deploy again after `amplify-lambda` is done; that should be all that is needed.
> - **Deploying everything from the repo root (`npx sls deploy --stage <stage>`) already does this for you.** `serverless-compose.yml` declares `amplify-embedding` with `dependsOn: amplify-lambda`, so compose waits. The rule matters when you deploy services one at a time, in parallel, or from separate CI jobs.

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

**What "caching" in that commit means.** Despite the commit title, the commit contains **no build or deploy caching** change. Nothing is written to disk by the deploy and there is no cache to clear. Deploys behave the same on the first and later runs.

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
- **The branch has moved during this release.** The RAG upgrade imports `pycommon.rag` (`contracts`, `migration_cost`, `timing`), which was added to the `v0.1.2` branch on 2026-11-05. A build that installed `v0.1.2` before that (for example from a cached layer or an older pip cache) lacks those modules, and the RAG code in `amplify-lambda` and `embedding` fails with an import error. Rebuild every Python layer from a fresh install, and check that `pycommon/rag/contracts.py` is in the built package.
- Build machines need outbound access to GitHub, as they did for v0.1.1.
- `scripts/requirements.txt` (migration and backup scripts) and `amplify-lambda/markitdown/markitdown.sh` (the markitdown layer) still reference **v0.1.1**. No script imports pycommon, so the scripts are unaffected; the markitdown layer keeps the older copy of the pycommon modules it bundles.

**What changed in pycommon, in brief** (details in the pycommon repository's `RELEASE_NOTES_v0.1.2.md`):

| Change | Effect for operators |
|---|---|
| Shared Parameter Store handler (`cfn_handler`) | Each stack runs its own populator. See [section 4](#4-parameter-store-changes). |
| LZW unicode fix | Conversations and other compressed payloads containing emoji, non-Latin scripts or the text `U+` are now stored and read correctly. See [section 7](#7-conversation-storage-lzw-compatibility). |
| Clearer rate-limit error messages (`get_rate_limit_notice`) | Added to the library; no backend service calls it yet. |
| JWT verification moved from `python-jose` to PyJWT | Same checks (RS256, issuer, audience, access token). `python-jose` is no longer pulled in by pycommon. |
| Model rate table optional when a flat cost is given | `MODEL_RATE_TABLE` is only required when `flat_cost` is not set. |
| New `pycommon.rag` package | Shared building blocks for the RAG upgrade: storage namespaces (`global`, `global2`), the Nova embedding model id, page identities, per-document cost tracking and timing. Required by `amplify-lambda` and `embedding`. See [section 9](#10-rag-pipeline-upgrade). |
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

No other migration step is needed for Parameter Store. Existing `/amplify/<stage>/...` parameters stay in place. Re-running `scripts/populate_parameter_store.py` is **not** required for this release:

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

### `documentRetention` (new admin config)

Stored under `config_id = documentRetention`: `{isActive: boolean, days: 1-3650}`. Both fields are required. The default created on first read is **`{isActive: false, days: 90}`**, and an absent or malformed record is treated as off. It controls a daily clean-up of unused chat-only attachments; see [section 9](#10-rag-pipeline-upgrade). Nothing is deleted until an admin turns it on (Admin panel, Configurations, Document Retention).

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
| `POST /rag_migration/status`, `/rag_migration/settings`, `/rag_migration/action` | `amplify-lambda` | Admin RAG migration tab. Admin only. See [section 9](#10-rag-pipeline-upgrade). |
| `POST /embedding/legacy-cleanup` | `embedding` | Removes migrated documents' rows from PostgreSQL. Admin only. |
| `POST /assistant/migrate_references` | `amplify-assistants` | Points assistants at the new copy of a migrated document. Admin only. |

- The three admin RAG routes check for an admin inside the handler (`verify_user_as_admin`). The shared `permissions.py` entries for them allow everyone, so that handler check is the only guard.
- The OpenAI-compatible routes use an Amplify API key (`Authorization: Bearer amp-...`) with the `chat` or `full_access` scope for completions. See `docs/openai-compatible-chat-api.md`. Streaming is answered as a buffered replay, not token by token.
- `/v1/chat/completions` was added to the `ApiGatewayTimeoutConfig` custom resource (180 s function timeout).
- A New UI feature that calls a route missing from an older backend will fail, which is why the backend goes first.
- **`GET /state/share/sent` limits.** A record of each share is stored only by this release's `share_with_users`, so history starts at upgrade: shares made before it do not appear. The query returns at most 100 records per user, taken from the oldest end, so a user with more than 100 recorded shares will not see the newest ones. The share dialog also keeps its own history in the browser, so the list is a supplement and a failed call is ignored.

## 7. Conversation storage (LZW) compatibility

Conversations and some other payloads are compressed with an LZW routine that exists in three copies: `pycommon/lzw.py` (Python services), `amplify-lambda-js/common/lzwCompression.js`, and the frontend's `utils/app/lzwCompression.ts`. Commit `2e7a223c` and pycommon v0.1.2 fix how non-ASCII characters are encoded.

- **The bug**: characters were written as `U+` plus hex. Python wrote 4 digits (so emoji came out as 5-digit tags that were read back as garbage); JavaScript wrote unpadded hex (so U+0100-U+0FFF, such as Cyrillic, Greek, Hebrew, Arabic and Thai, got 3-digit tags that could not be read). A typed `U+0041` was read back as `A`.
- **The fix**: all three copies now write UTF-16 code units above 255 as `U+` plus exactly 4 hex digits (an emoji is two tags), escape a literal `U+`, and still read the old 3-digit tags. The three copies share identical test vectors.
- **Old data**: conversations written before this release still read, with one limit. Data that was already unreadable (emoji written by the Python encoder, or 3-digit tags directly followed by a hex character) stays unreadable. No migration or re-save is needed or possible.
- **Mixed versions**: new readers read old data. Old readers read new data correctly for ordinary text and non-Latin BMP scripts. For emoji an old Python reader returns two separate surrogate characters instead of one. Deploy **all** Python services on pycommon v0.1.2 and the frontend of this release **together** to avoid a window like this.

## 8. Stored data

No migration scripts are required, and nothing you have stored needs to be converted before or after the deploy. The one optional, admin-run migration is for documents in the RAG store ([section 9](#10-rag-pipeline-upgrade)); skipping it leaves existing documents working as they do today.

- **Conversations**: saves accept an optional `expectedVersion` (the S3 object's last-modified time in milliseconds). If the stored copy is newer, the save returns `CONFLICT` instead of overwriting it. Clients that do not send it behave as before. A known limit: the check relies on S3 timestamps (one-second resolution noted in the code).
- **Artifacts**: version entries may carry an optional `lineageId`. Entries without it still load.
- **Shares and groups**: `object-access` gained group grants (`group_grants.py`).
- **Code interpreter sessions**: `amplify-assistants` adds a DynamoDB index (`ActivityIndex`, on `activityBucket` and `updatedAt`) to the code-interpreter table and a scheduled Lambda (`reap_idle_code_interpreter_sessions`, every 5 minutes) that closes idle sessions. Both are created by the normal deploy.

## 9. Other operator notes

- Python dependencies were bumped across services for security (for example Pillow 12.2.0). Rebuild layers as part of a normal deploy.
- The Google and Office365 assistant-API stacks do not auto-populate Parameter Store.
- Variable templates (`dev-var.yml-example`) are unchanged. The RAG settings in [section 9](#10-rag-pipeline-upgrade) are not in the template; they have defaults.

## 10. RAG pipeline upgrade

**RAG** (retrieval-augmented generation) is how Amplify searches the documents users upload so that chat answers can quote them. An **embedding** is the numeric form of a piece of text (or a page image) that makes it searchable. This release (backend PR #405, with frontend PR #350) changes how **new** documents are processed and adds an admin tool for moving **existing** ones. The team's own description of the document flows is in `amplify-lambda/rag/DOCUMENT_FLOWS.md`.

### What happens when you deploy (no action required)

- **New uploads use the new pipeline ("global2") straight away.** No flag or admin action is involved. Text and page images are embedded with Amazon Nova multimodal embeddings (`amazon.nova-2-multimodal-embeddings-v1:0`) and stored in a new DynamoDB vector table.
- **Existing documents keep working.** They stay in PostgreSQL (the "legacy" or `global` store), and search reads both stores. A document moves only if an admin migrates it ([below](#moving-existing-documents-admin-tool)), or if someone uploads identical file contents again, which creates a new-pipeline copy.
- **OCR is on by default.** Scanned or picture-heavy PDF pages, and pictures inside Word, PowerPoint and Excel files, are read with Amazon Textract (`TEXTRACT_ENABLED`, default `true`). This costs money for every upload that needs it, not just during migration.
- **Page images.** PDF and PowerPoint pages are rendered and embedded as images (at most 200 per document). The image files expire after 3 days.
- **Tables and spreadsheets** are read directly and stored as markdown tables.
- **New upload limits.** An upload over a limit is rejected with a message to the user.

  | Limit | Default | Set by |
  |---|---|---|
  | Document size | 100 MB | `MAX_DOCUMENT_MB` |
  | Spreadsheet size | 25 MB | `MAX_SPREADSHEET_MB` |
  | Plain-text file size | 10 MB | `MAX_TEXT_FILE_MB` |
  | PDF pages | 500 with RAG, 1000 chat-only | `MAX_PDF_PAGES_RAG`, `MAX_PDF_PAGES_CHAT` |
  | Spreadsheet cells | 2,000,000 | `MAX_SPREADSHEET_CELLS` |
  | Tokens per document | 2,500,000 with RAG (set in `amplify-lambda/serverless.yml`), 1,000,000 chat-only | `MAX_DOCUMENT_TOKENS_RAG`, `MAX_DOCUMENT_TOKENS_CHAT` |
  | Chunks per document | 3000 (was 1000) | `MAX_CHUNKS` |

- **Other behavior.** Re-scraped web pages that did not change are no longer re-embedded. Deleting a file removes that upload's pointer and keeps content other uploads share. The chat service now records when a document was last used.

### Requirements for your AWS account

- **Bedrock model access** to the Nova multimodal embedding model in the region set by `GLOBAL2_BEDROCK_REGION` (default `us-east-1`). A comment in `embedding/serverless.yml` says Nova is offered in us-east-1 only. If your stack runs elsewhere, the embedding Lambda calls Bedrock in that setting's region. Whether model access is enabled in your account is something only you can check.
- **Amazon Textract** in the stack's region. We did not verify regional availability.
- **DynamoDB vector indexes.** The new table uses the `VectorIndexes` table property and the `dynamodb:SearchVectors` permission. AWS documents `VectorIndexes` for `AWS::DynamoDB::Table`; we could not find the supported regions and did not test this on a live account. `embedding/requirements.txt` pins `boto3==1.43.118`, and a comment there says the Lambda runtime's built-in boto3 is too old for these calls.
- **A pycommon build that includes `pycommon/rag/`** ([section 3](#3-pycommon-v012)).

### New AWS resources (created by the normal deploy)

| Stack | Resource | Notes |
|---|---|---|
| `amplify-lambda` | DynamoDB table `amplify-<dep>-lambda-<stage>-rag-migration` | Migration progress. Key `id`, index `status-index`. Pay-per-request, point-in-time recovery on. |
| `amplify-lambda` | Lambdas `rag_migration_status`, `rag_migration_settings`, `rag_migration_action` | Admin routes; `rag_migration_action` has a 900 s timeout. |
| `amplify-lambda` | Lambda `rag_migration_tick` | Runs **every minute**. When no migration is active it exits at once. |
| `amplify-lambda` | Lambda `sweep_ephemeral_files` | Runs daily at 04:00 UTC. Does nothing unless retention is on. |
| `amplify-lambda` | IAM: Textract, `s3:PutObjectTagging`, DynamoDB access to assistants, model-rate and cost tables | Plus a new managed policy for the migration. |
| `amplify-lambda` | S3 lifecycle rules | Chunk bucket objects expire after 14 days. Text bucket: `textract-partial/` after 2 days, page images after 3 days. |
| `amplify-lambda` | Longer timeouts | `process_document_for_rag` 300 s to 600 s. The document queue's visibility timeout 300 s to 660 s, and the embedding-chunk queue's 120 s to 360 s. |
| `embedding` | DynamoDB table `amplify-<dep>-embedding-<stage>-vectors` | The new vector store. Index `global2-vector-index`, cosine distance, `GLOBAL2_EMBEDDING_DIM` dimensions. Pay-per-request, point-in-time recovery and encryption on. |
| `embedding` | Lambda `legacy_cleanup` (in the VPC) | Backs the admin clean-up step. |
| `embedding` | Longer chunk-embedding timeout | 120 s to 300 s. |
| `amplify-assistants` | Lambda `migrate_references` | 300 s timeout. |

Neither new DynamoDB table has a deletion policy, so deleting the CloudFormation stack deletes the table and its data (point-in-time recovery is on).

### Settings

All have defaults. Set them in Parameter Store **before** the first `embedding` deploy if you want different values.

| Setting | Where | Default | Notes |
|---|---|---|---|
| `GLOBAL2_EMBEDDING_DIM` | `/amplify/<stage>/amplify-<dep>-embedding/GLOBAL2_EMBEDDING_DIM` | `1024` | Must be 256, 384, 1024 or 3072 (per a code comment). **Fixed once the table exists:** AWS documents that a vector index's dimensions cannot be changed after creation, so changing it later fails the stack update. We did not test this. |
| `GLOBAL2_BEDROCK_REGION` | `/amplify/<stage>/amplify-<dep>-embedding/GLOBAL2_BEDROCK_REGION` | `us-east-1` | Region used for the Nova calls. |
| `RAG_MIGRATION_TABLE` | `/amplify/<stage>/amplify-<dep>-lambda/RAG_MIGRATION_TABLE` | none | Written automatically by `amplify-lambda`'s Parameter Store populator. This is why `amplify-lambda` must deploy before `embedding` ([section 1](#1-deploy-order)). |
| `TEXTRACT_ENABLED` | Lambda environment | `true` | Set to `false` to turn OCR off. |
| Upload limits | Lambda environment | see table above | |

### Moving existing documents (admin tool)

**Migration is optional and never starts by itself.** Nothing is moved or deleted by deploying, and an admin can leave it alone indefinitely.

An admin opens **Admin Panel, RAG Migration** in the New UI (the tab is not in the Classic admin). The backend routes are admin-only. In short:

1. **Choose** which documents (assistant documents or all embedded documents, optionally filtered by type or last use).
2. **Set the pace** (how many to start at once and in total). Defaults are 10 started at a time and 20 building at once.
3. **Create embeddings.** The admin starts the run; the tool builds a new-pipeline copy of each document. The run uses the **admin's own login** (the confirmation says it lasts about an hour), and new documents start only while the page stays open. Pausing stops new starts, and stopping empties the waiting queue.
4. **Move references.** Assistants that use a document are pointed at the new copy. This can be rolled back. "Move references automatically" is off by default and recommended off for a first run. Each press handles up to 50 documents.
5. **Remove old embeddings.** This deletes the migrated documents' rows from PostgreSQL. It is the **only destructive step**, it is never run automatically, and after it a rollback is not possible. Each press handles up to 200 documents. The backend refuses a document whose references have not been moved, whose new copy has no vectors in the new store, or which an upload still points to the old copy of. The only confirmation in the web page is a browser confirm dialog.

Other points:
- **While idle, the every-minute job does very little.** It only checks progress and marks documents that stalled for over 2 hours as failed; it cuts over built documents only if automatic cut-over is on. It never starts documents and never deletes.
- Documents cannot be started between about 02:55 and 03:20 UTC (a nightly credential clean-up).
- **Costs.** Migration re-embeds documents with Nova, calls an LLM to generate questions for each chunk, and may run Textract. The tab shows estimates, but its numbers are rough: they include fixed per-page and per-call values written in the code, and the tool itself says the page-image price comes from a catalog not confirmed with AWS. Check current AWS pricing before a large run.
- **The PostgreSQL database keeps running.** Nothing in this release removes it; the clean-up step deletes only the migrated documents' rows.
- **Orphaned files.** The tab can also list and remove files that no assistant uses any more (deleted assistants, drive, website and group files). Removal needs the admin to type `DELETE`, skips files newer than 14 days or still in use, and handles up to 100 files per request.

### Document retention (optional, off by default)

Admin Panel, Configurations, **Document Retention** turns on a daily clean-up (04:00 UTC) of chat-only attachments (files attached to a chat only, which are not embedded for search) that have not been used for the chosen number of days (default 90). Deleted files cannot be recovered. It never deletes files that an assistant or group uses, images and videos, or files that have embeddings.
- Only files uploaded **after this deploy** are covered; the upload step now marks them as temporary.
- Deploy `amplify-lambda-js` before turning retention on. That service records when a file was last used, and without it files in active use could look unused.
- The sweep will not run unless the assistants, layered-assistants and related tables are configured.

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
- Bedrock: hardened structured-output parsing for Bedrock GPT-5.6 models with raw-response logging on stream errors, socket-timeout handling and better request cancellation, and the adaptive-reasoning pattern now includes Haiku 5+.

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

### Documents and RAG
- New document pipeline ("global2"): Nova multimodal embeddings in a DynamoDB vector table, Textract OCR for scanned pages and images in Office files, page-image embeddings, and direct table and spreadsheet reading.
- New upload limits and a higher chunk limit (3000).
- An admin RAG Migration tool (moves existing documents, with rollback before clean-up), an orphaned-file finder, and an optional document-retention sweep for chat-only attachments. See Part 1, [section 9](#10-rag-pipeline-upgrade).

### Infrastructure and dependencies
- Per-stack Parameter Store populators on pycommon v0.1.2; osls and `@osls/compose`; security updates for pip and npm dependencies across services.

---

## Stats

Measured with `git diff origin/main...origin/dev` on 2026-11-06 (at `cb31207b`); commits keep landing, so re-run before publishing. Line counts include regenerated lockfiles.

| Metric | Count |
|--------|-------|
| Commits | 51 |
| Files changed | 234 |
| Insertions | +33,035 |
| Deletions | -45,450 |
| Merged pull requests | 5 (#388, #402, #403, #404, #405) |

## Contributors

- **Max Moundas** - New UI backend, feature flags, admin configs, OpenAI-compatible API, osls
- **Harsha Raj Kumar** - Artifact lineage, edit, and cut-off fixes
- **Karely Rodriguez** - RAG pipeline upgrade (global2 embeddings, migration tool, Textract OCR), Parameter Store standardization, Bedrock client timeouts
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
2. **`object-access` still depends on `python-jose` indirectly (open).** `object-access/users.py` does `import jose` / `from jose import jwt`, and its `requirements.txt` pins `jose==1.0.0`, an unrelated package with no `jwt` or `JWTError`. It worked only because pycommon v0.1.1 pulled in `python-jose`; pycommon v0.1.2 no longer does. Add `python-jose` to `object-access/requirements.txt` (or move `users.py` to PyJWT) and verify the build. Not run by us. (The `amplify-lambda-assistants-api` `certifi`/`pyjwt` conflict was fixed by `afd757be`.)
3. **The pycommon `v0.1.2` branch keeps moving.** It gained `pycommon/rag/` on 2026-11-05 and the backend RAG code imports it. Any layer built from an older install breaks `amplify-lambda` and `embedding` at import time. Decide the exact commit you ship, and re-check the pin before publishing. The tip when this was written was `7b4056b`.
4. **Version numbers.** Both `package.json` files still say `0.9.0`; the last tag is `v0.9.4`.
5. **RAG: code and docs disagree (frontend wording).** Confirm text in the RAG Migration tab says "update assistants" (that button was removed) and points to a "Documents section" that no longer exists; the clean-up confirm says "every document" but one press handles at most 200. The clean-up step relies on a browser confirm dialog only. See the frontend draft-status section.
6. **RAG: no operator variable template.** `dev-var.yml-example` and `scripts/populate_parameter_store.py` were not changed by the RAG commit. The `GLOBAL2_*` settings and the Textract and limit variables exist only as defaults in `serverless.yml` and code. They are documented only in [section 9](#10-rag-pipeline-upgrade).
7. **RAG: not run by us.** We read the code and `rag/DOCUMENT_FLOWS.md`. We did not deploy, run a migration, or confirm Nova, Textract or DynamoDB vector-index availability in any region. `RagMigrationTable` and `Global2VectorTable` have no deletion policy.
8. **Uncommitted local change (not part of the notes).** At the time of writing, `amplify-lambda-js/serverless.yml` in the working tree changes the service name to `${self:custom.stageVars.DEP_NAME}-js`, changes `ssmBasePath`, and removes the three `BEDROCK_PROMPT_CACHING*` variables. A service rename creates a new stack and changes the Parameter Store paths other stacks read. If that is not intended, discard it before releasing. `amplify-lambda-assistants-api/serverless.yml` and `amplify-lambda-assistants-api-office365/serverless.yml` also have uncommitted edits.
9. Not verified by us: any deployed environment, a real `pip` resolve, plugin loading under osls, `npx sls` from standalone service directories, IAM coverage for each stack's populator.
