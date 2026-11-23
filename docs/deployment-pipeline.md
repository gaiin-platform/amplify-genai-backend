# Dev deployment pipeline

The workflow `.github/workflows/deploy-dev.yml` replaces the manual `npx sls deploy --stage dev` step. It detects which services changed, creates one CloudFormation change set per changed service, and stops. **It never executes a change set.** A person reviews each change set and executes it from the AWS Console, exactly as before.

## What happens on each event

| Event | Result |
|---|---|
| Push to `dev` | Detect changed services, then create a change set for each one |
| Pull request into `dev` | Detect changed services, then run `sls package` for each one. No change set is created |
| Manual run (`workflow_dispatch`) | Create change sets for the services you type in, or `all` |

The run has three jobs.

1. **Detect changed services.** Runs the script self-tests, then maps changed files to service folders.
2. **Change set (service).** One job per service, one at a time, in dependency order. The run stops at the first failure.
3. **Review summary.** One table with every change set of the run, in the order they should be executed.

## How to review and execute a change set

1. Open the workflow run in the **Actions** tab and scroll to the summary.
2. Read the **Change sets created by this run** table. A red caution block at the top means something will be deleted or replaced.
3. Click the change set link for row 1. It opens the change set in the AWS Console. The change set name starts with `gha-`.
4. Review the changes, then choose **Execute change set**.
5. Wait for the stack to reach `UPDATE_COMPLETE`, then continue with the next numbered row.

Rows without a number need no action. "No changes" means the deployed stack already matches the code.

If you decide not to deploy, do nothing. The next run for that service replaces the change set.

## When a deletion or replacement is flagged

A change is flagged when its action is `Remove`, or when it is a `Modify` with a replacement of `True` or `Conditional`. A replacement deletes the resource and creates a new, empty one, so a replaced DynamoDB table or S3 bucket loses its data.

1. Find the resource in the caution block and confirm with the author that the removal is intended.
2. For tables, buckets and user pools, confirm the data is backed up or no longer needed.
3. Execute only after both checks.

`AWS::ApiGateway::Deployment` is never flagged because it is removed and added on almost every deploy. The ignore list is `ignored_resource_types` in `.github/scripts/deploy-config.json`. That file is the only place to change it.

## Change detection rules

All rules are data in `.github/scripts/deploy-config.json`.

| Change | Services selected |
|---|---|
| A file inside a service folder | That service |
| `lambda_layers/**`, `npm-shims/**`, root `package.json` or `package-lock.json`, `serverless-compose.yml` | Every service |
| `*.md`, `docs/**`, `<service>/docs/**` | None |
| `.github/**` and other root files | None |

A push is compared against the last successful run of this workflow on `dev`, not against the previous push. If a run fails or is replaced in the queue, the next run picks up its changes.

## Adding a new service

1. Add the folder name to `services` in `deploy-config.json`, placed after every service it depends on.
2. If the folder must never be deployed, add it to `excluded_folders` instead.

The detect job fails with a clear message when a folder has a `serverless.yml` but is in neither list.

## Required setup

| Item | Where | Notes |
|---|---|---|
| Environment `development` | GitHub repo settings | Restrict deployment branches to `dev` |
| Environment `development-pr` | GitHub repo settings | Used by pull requests |
| Variable `AWS_IAM_ROLE_ARN` | Each Environment | Role assumed through GitHub OIDC |
| Variable `AWS_REGION` | Each Environment | `us-east-1` |
| Secret `DEV_VAR_YML_B64` | Each Environment | `base64 < var/dev-var.yml`. The workflow rebuilds the file at runtime and never prints it |

**Role for `development`.** Trust only `repo:gaiin-platform/amplify-genai-backend:environment:development`. Starting point for permissions, to be confirmed during the first manual runs:

* CloudFormation: `DescribeStacks`, `DescribeStackResource`, `DescribeStackResources`, `ListStackResources`, `GetTemplate`, `ValidateTemplate`, `CreateChangeSet`, `DescribeChangeSet`, `ListChangeSets`, `DeleteChangeSet`
* S3 on the Serverless deployment buckets: `ListBucket`, `GetBucketLocation`, `GetObject`, `PutObject`, `DeleteObject`
* SSM: `GetParameter`, `GetParameters` on `/amplify/dev/*`
* STS: `GetCallerIdentity`
* Whatever the Serverless plugins call during `sls deploy`: the prune plugin lists and deletes old Lambda layer versions, and the domain manager plugin in `amplify-lambda` reads API Gateway, ACM and Route 53
* **Explicit deny:** `cloudformation:ExecuteChangeSet`, `cloudformation:UpdateStack`, `cloudformation:DeleteStack`

**Role for `development-pr`.** Read-only. It needs the SSM and STS permissions above so `sls package` can resolve variables.

## Testing the scripts offline

No AWS access is needed.

```
python3 -m unittest discover -s .github/scripts/tests -v

git diff --name-only --no-renames <base>..<head> | python3 .github/scripts/detect_services.py --changed-files -

python3 .github/scripts/analyze_changeset.py analyze \
  --changeset .github/scripts/tests/samples/remove.json --service amplify-lambda-admin
```

`tests/samples/` holds example `describe-change-set` responses: no changes, a normal update, a removal, a replacement and a failure.

## Design decisions

| Topic | Decision | Reason |
|---|---|---|
| Forcing a change set | Pass `--param="changeset=<name>"` | Works whatever `CHANGE_SET_BOOLEAN` is in the var file |
| Change set name | `gha-<run id>-<attempt>-<sha7>` | Unique per run and per rerun, and easy to trace back to a run |
| Stack name | Read from `.serverless/serverless-state.json` after `sls deploy` | Written by the same run that created the change set, so it cannot disagree with it |
| `--force` | Always passed | Without it, osls can skip the upload when artifacts are unchanged while the plugin still requests a change set from a template that was never uploaded |
| No changes | Treated as success. The empty change set is deleted | CloudFormation reports an empty change set as `FAILED` |
| Rapid pushes | Diff against the last successful run | GitHub keeps only one queued run, so a diff against the previous push could skip a service |
| Stale change sets | Each run deletes older `gha-*` change sets on the same stack | Executing an old change set would roll the stack back to older code. Change sets made by people are never touched |
| Var file | Environment secret, base64 encoded | Simplest option that keeps the file out of the repo. Secrets Manager is the alternative if rotation or audit is needed |
| Ordering | One service at a time, in the order of `services` | Mirrors `serverless-compose.yml` and stops dependents when a base service fails |

## Known limits

* If a service needs a new export or SSM parameter from another service in the same push, its change set fails until the upstream change set is executed. Execute the upstream one, then rerun the workflow.
* `sls deploy` still runs the Serverless plugins that act outside CloudFormation (prune, domain manager, deployment bucket), as it does on a developer machine.
* A service whose stack does not exist yet needs its first deployment done manually.
* Run summaries and logs are visible to anyone who can see the repository's Actions tab. They show stack names, resource logical IDs and the AWS account ID inside the console links.
