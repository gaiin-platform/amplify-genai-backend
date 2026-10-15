# Local Projects development

This harness runs the new Projects CRUD and project-memory endpoints entirely
on the local machine. It does not read or write AWS DynamoDB, IAM, SSM, or the
deployed Amplify API.

## Start

1. Start Docker Desktop.
2. From `amplify-genai-backend/amplify-assistants`, run:

   ```bash
   ./run-projects-local.sh
   ```

The script starts DynamoDB Local on `127.0.0.1:8000`, creates the two local
tables, and starts the Projects HTTP adapter on `127.0.0.1:3020`.

## Point the frontend at it

Set this value in `amplify-genai-frontend/.env.local` and restart Next.js:

```dotenv
NEXT_PUBLIC_LOCAL_SERVICES=projects:3020:dev
NEXT_PUBLIC_PROJECTS_LOCAL_MODE=true
```

Only calls from `services/projectService.ts` are routed locally, including the project file manifest (list/add/update/remove). The Projects
view disables chat and knowledge-base controls in this mode so a local test
cannot accidentally read or write the deployed chat or file services.

## Safety rails

- The adapter refuses to start unless `PROJECTS_LOCAL_MODE=true` **and**
  `DYNAMODB_ENDPOINT_URL` points at a loopback host (DynamoDB Local). The
  service module enforces the same rule on import, so a stray
  `DYNAMODB_ENDPOINT_URL` in a deployed Lambda fails loudly instead of
  redirecting traffic.
- Requests are validated against the same JSON schemas the deployed
  `@validated` decorator uses, so local runs reject the same bad input.
- Use Python 3.11 (what Lambda runs). `run-projects-local.sh` prefers
  `python3.11` and warns otherwise; override with `PYTHON_BIN`.
- Everything in `local/`, the `.venv*` directory, this file and the docker-compose
  file are excluded from the Lambda package (`package.exclude` in `serverless.yml`).

## Scope

This validates the Projects gallery, project CRUD, archive state, instructions
editing, and project-memory CRUD against the same Python business functions and
DynamoDB keys/indexes used in production. The adapter bypasses API Gateway
authentication and IAM only in explicit local mode and binds to loopback.

Knowledge-base uploads and model chat execution belong to other Amplify
services and are not made local by this harness. Do not use local test projects
to upload files or validate end-to-end model responses.

## Stop and reset

Stop DynamoDB Local while preserving data:

```bash
docker compose -f docker-compose.projects-local.yml down
```

Remove all local Projects data:

```bash
docker compose -f docker-compose.projects-local.yml down -v
```
