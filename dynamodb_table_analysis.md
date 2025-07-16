# DynamoDB Table Pattern Analysis Report

**Total tables analyzed:** 61

## Summary

- **Pattern 1** (`{self:custom.stageVars.DEP_NAME}` + `${opt:stage, 'dev'}`): 0/61 found
- **Pattern 2** (`${self:service}` + `${sls:stage}`): 35/61 found
- **Pattern 3** (`${self:custom.stageVars.DEP_NAME}` + `${sls:stage}`): 18/61 found

## Detailed Results

### Table: `amplify-tf-state-dev`

**Pattern 1:** `amplify-tf-state-dev`
- **Found:** ❌ No

**Pattern 2:** `amplify-tf-state-dev`
- **Found:** ❌ No

**Pattern 3:** `amplify-tf-state-dev`
- **Found:** ❌ No

---

### Table: `amplify-v6-admin-dev-admin-configs`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-admin-${opt:stage, 'dev'}-admin-configs`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-admin-configs`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-admin/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-admin-${sls:stage}-admin-configs`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml, ./object-access/serverless.yml, ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./chat-billing/serverless.yml

---

### Table: `amplify-v6-admin-dev-admin-logs`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-admin-${opt:stage, 'dev'}-admin-logs`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-admin-logs`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-admin/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-admin-${sls:stage}-admin-logs`
- **Found:** ❌ No

---

### Table: `amplify-v6-agent-js-dev-agent-state`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-js-${opt:stage, 'dev'}-agent-state`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-agent-state`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-js-${sls:stage}-agent-state`
- **Found:** ❌ No

---

### Table: `amplify-v6-agent-loop-dev-agent-event-templates`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-loop-${opt:stage, 'dev'}-agent-event-templates`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-agent-event-templates`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-loop-${sls:stage}-agent-event-templates`
- **Found:** ❌ No

---

### Table: `amplify-v6-agent-loop-dev-agent-state`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-loop-${opt:stage, 'dev'}-agent-state`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-agent-state`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-loop-${sls:stage}-agent-state`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

---

### Table: `amplify-v6-agent-loop-dev-email-allowed-senders`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-loop-${opt:stage, 'dev'}-email-allowed-senders`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-email-allowed-senders`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-loop-${sls:stage}-email-allowed-senders`
- **Found:** ❌ No

---

### Table: `amplify-v6-agent-loop-dev-scheduled-tasks`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-loop-${opt:stage, 'dev'}-scheduled-tasks`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-scheduled-tasks`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-loop-${sls:stage}-scheduled-tasks`
- **Found:** ❌ No

---

### Table: `amplify-v6-agent-loop-dev-workflow-registry`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-agent-loop-${opt:stage, 'dev'}-workflow-registry`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-workflow-registry`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-agent-loop-${sls:stage}-workflow-registry`
- **Found:** ❌ No

---

### Table: `amplify-v6-amplify-js-dev-datasource-registry`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-amplify-js-${opt:stage, 'dev'}-datasource-registry`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-datasource-registry`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-amplify-js-${sls:stage}-datasource-registry`
- **Found:** ❌ No

---

### Table: `amplify-v6-amplify-js-dev-request-state`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-amplify-js-${opt:stage, 'dev'}-request-state`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-request-state`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-amplify-js-${sls:stage}-request-state`
- **Found:** ❌ No

---

### Table: `amplify-v6-artifacts-dev-user-artifacts`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-artifacts-${opt:stage, 'dev'}-user-artifacts`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-user-artifacts`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-artifacts/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-artifacts-${sls:stage}-user-artifacts`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-api-dev-job-status`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-api-${opt:stage, 'dev'}-job-status`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-job-status`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-api-${sls:stage}-job-status`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-api-dev-oauth-state`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-api-${opt:stage, 'dev'}-oauth-state`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-oauth-state`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-api-${sls:stage}-oauth-state`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-api-dev-op-log`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-api-${opt:stage, 'dev'}-op-log`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-op-log`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-api-${sls:stage}-op-log`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-api-dev-user-oauth-integrations`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-api-${opt:stage, 'dev'}-user-oauth-integrations`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-user-oauth-integrations`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-api-${sls:stage}-user-oauth-integrations`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-dev-assistant-aliases`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-assistant-aliases`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistant-aliases`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-assistant-aliases`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

---

### Table: `amplify-v6-assistants-dev-assistant-lookup`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-assistant-lookup`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistant-lookup`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-assistant-lookup`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-dev-assistant-thread-runs`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-assistant-thread-runs`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistant-thread-runs`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-assistant-thread-runs`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-dev-assistant-threads`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-assistant-threads`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistant-threads`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-assistant-threads`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-dev-assistants`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-assistants`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistants`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml, ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-assistants`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

---

### Table: `amplify-v6-assistants-dev-code-interpreter-assistants`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-code-interpreter-assistants`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-code-interpreter-assistants`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-code-interpreter-assistants`
- **Found:** ❌ No

---

### Table: `amplify-v6-assistants-dev-group-assistant-conversations`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-assistants-${opt:stage, 'dev'}-group-assistant-conversations`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-group-assistant-conversations`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-assistants-${sls:stage}-group-assistant-conversations`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

---

### Table: `amplify-v6-chat-billing-dev-additional-charges`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-chat-billing-${opt:stage, 'dev'}-additional-charges`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-additional-charges`
- **Found:** ✅ Yes
- **Files:** ./chat-billing/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-chat-billing-${sls:stage}-additional-charges`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-chat-billing-dev-history-usage`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-chat-billing-${opt:stage, 'dev'}-history-usage`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-history-usage`
- **Found:** ✅ Yes
- **Files:** ./chat-billing/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-chat-billing-${sls:stage}-history-usage`
- **Found:** ❌ No

---

### Table: `amplify-v6-chat-billing-dev-model-rates`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-chat-billing-${opt:stage, 'dev'}-model-rates`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-model-rates`
- **Found:** ✅ Yes
- **Files:** ./chat-billing/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-chat-billing-${sls:stage}-model-rates`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml

---

### Table: `amplify-v6-data-disclosure-dev-acceptance`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-data-disclosure-${opt:stage, 'dev'}-acceptance`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-acceptance`
- **Found:** ✅ Yes
- **Files:** ./data-disclosure/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-data-disclosure-${sls:stage}-acceptance`
- **Found:** ❌ No

---

### Table: `amplify-v6-data-disclosure-dev-versions`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-data-disclosure-${opt:stage, 'dev'}-versions`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-versions`
- **Found:** ✅ Yes
- **Files:** ./data-disclosure/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-data-disclosure-${sls:stage}-versions`
- **Found:** ❌ No

---

### Table: `amplify-v6-embedding-dev-embedding-progress`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-embedding-${opt:stage, 'dev'}-embedding-progress`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-embedding-progress`
- **Found:** ✅ Yes
- **Files:** ./embedding/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-embedding-${sls:stage}-embedding-progress`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

---

### Table: `amplify-v6-lambda-basic-ops-dev-dynamic-code`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${opt:stage, 'dev'}-dynamic-code`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-dynamic-code`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${sls:stage}-dynamic-code`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-basic-ops-dev-user-storage`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${opt:stage, 'dev'}-user-storage`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-user-storage`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${sls:stage}-user-storage`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-basic-ops-dev-work-records`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${opt:stage, 'dev'}-work-records`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-work-records`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${sls:stage}-work-records`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-basic-ops-dev-work-sessions`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${opt:stage, 'dev'}-work-sessions`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-work-sessions`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-basic-ops-${sls:stage}-work-sessions`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev`

**Pattern 1:** `amplify-v6-lambda-dev`
- **Found:** ❌ No

**Pattern 2:** `amplify-v6-lambda-dev`
- **Found:** ❌ No

**Pattern 3:** `amplify-v6-lambda-dev`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-accounting`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-accounting`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-accounting`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-accounting`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-accounts`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-accounts`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-accounts`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml, ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-accounts`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-optimizer/serverless.yml, ./amplify-lambda-artifacts/serverless.yml, ./object-access/serverless.yml, ./data-disclosure/serverless.yml, ./amplify-lambda-ops/serverless.yml, ./amplify-lambda-api/serverless.yml, ./amplify-lambda-admin/serverless.yml, ./embedding/serverless.yml, ./chat-billing/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-lambda-dev-assistants`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-assistants`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-assistants`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml, ./amplify-assistants/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-assistants`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-chat-usage`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-chat-usage`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-chat-usage`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-chat-usage`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./chat-billing/serverless.yml

---

### Table: `amplify-v6-lambda-dev-chat-usage-archive`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-chat-usage-archive`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-chat-usage-archive`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-chat-usage-archive`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-conversation-metadata`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-conversation-metadata`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-conversation-metadata`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-conversation-metadata`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-cost-calculations`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-cost-calculations`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-cost-calculations`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-cost-calculations`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-optimizer/serverless.yml, ./amplify-lambda-artifacts/serverless.yml, ./object-access/serverless.yml, ./data-disclosure/serverless.yml, ./amplify-lambda-ops/serverless.yml, ./amplify-lambda-api/serverless.yml, ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./chat-billing/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-lambda-dev-db-connections`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-db-connections`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-db-connections`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-db-connections`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-dev-hash-files`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-hash-files`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-hash-files`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-hash-files`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml, ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-lambda-dev-history-cost-calculations`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-history-cost-calculations`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-history-cost-calculations`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-history-cost-calculations`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-js/serverless.yml

---

### Table: `amplify-v6-lambda-dev-user-files`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-user-files`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-user-files`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-user-files`
- **Found:** ✅ Yes
- **Files:** ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-lambda-dev-user-tags`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-${opt:stage, 'dev'}-user-tags`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-user-tags`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-${sls:stage}-user-tags`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-ops-dev-ops`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-ops-${opt:stage, 'dev'}-ops`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-ops`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-ops/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-ops-${sls:stage}-ops`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-personal-db-dev-personal-db`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-personal-db-${opt:stage, 'dev'}-personal-db`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-personal-db`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-personal-db-${sls:stage}-personal-db`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-personal-sql-dev-personal-sql`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-personal-sql-${opt:stage, 'dev'}-personal-sql`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-personal-sql`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-personal-sql-${sls:stage}-personal-sql`
- **Found:** ❌ No

---

### Table: `amplify-v6-lambda-personal-sql-dev-personal-sql-metadata`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-lambda-personal-sql-${opt:stage, 'dev'}-personal-sql-metadata`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-personal-sql-metadata`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-lambda-personal-sql-${sls:stage}-personal-sql-metadata`
- **Found:** ❌ No

---

### Table: `amplify-v6-memory-dev-memory`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-memory-${opt:stage, 'dev'}-memory`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-memory`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-memory-${sls:stage}-memory`
- **Found:** ❌ No

---

### Table: `amplify-v6-object-access-dev-amplify-group-logs`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-object-access-${opt:stage, 'dev'}-amplify-group-logs`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-amplify-group-logs`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-object-access-${sls:stage}-amplify-group-logs`
- **Found:** ❌ No

---

### Table: `amplify-v6-object-access-dev-amplify-groups`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-object-access-${opt:stage, 'dev'}-amplify-groups`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-amplify-groups`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-object-access-${sls:stage}-amplify-groups`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-admin/serverless.yml, ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-object-access-dev-api-keys`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-object-access-${opt:stage, 'dev'}-api-keys`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-api-keys`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-object-access-${sls:stage}-api-keys`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-optimizer/serverless.yml, ./amplify-lambda/serverless.yml, ./amplify-lambda-artifacts/serverless.yml, ./data-disclosure/serverless.yml, ./amplify-lambda-ops/serverless.yml, ./amplify-lambda-api/serverless.yml, ./amplify-lambda-admin/serverless.yml, ./amplify-lambda-js/serverless.yml, ./embedding/serverless.yml, ./chat-billing/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-object-access-dev-cognito-users`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-object-access-${opt:stage, 'dev'}-cognito-users`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-cognito-users`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-object-access-${sls:stage}-cognito-users`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda-api/serverless.yml

---

### Table: `amplify-v6-object-access-dev-object-access`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-object-access-${opt:stage, 'dev'}-object-access`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-object-access`
- **Found:** ✅ Yes
- **Files:** ./object-access/serverless.yml

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-object-access-${sls:stage}-object-access`
- **Found:** ✅ Yes
- **Files:** ./amplify-lambda/serverless.yml, ./embedding/serverless.yml, ./amplify-assistants/serverless.yml

---

### Table: `amplify-v6-se-dev-ai-code`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-se-${opt:stage, 'dev'}-ai-code`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-ai-code`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-se-${sls:stage}-ai-code`
- **Found:** ❌ No

---

### Table: `amplify-v6-se-dev-ops-common-data`

**Pattern 1:** `amplify-{self:custom.stageVars.DEP_NAME}-se-${opt:stage, 'dev'}-ops-common-data`
- **Found:** ❌ No

**Pattern 2:** `${self:service}-${sls:stage}-ops-common-data`
- **Found:** ❌ No

**Pattern 3:** `amplify-${self:custom.stageVars.DEP_NAME}-se-${sls:stage}-ops-common-data`
- **Found:** ❌ No

---

### Table: `resource-migration-checkpoints-dev`

**Pattern 1:** `resource-migration-checkpoints-dev`
- **Found:** ❌ No

**Pattern 2:** `resource-migration-checkpoints-dev`
- **Found:** ❌ No

**Pattern 3:** `resource-migration-checkpoints-dev`
- **Found:** ❌ No

---

### Table: `resource-migration-failures-dev`

**Pattern 1:** `resource-migration-failures-dev`
- **Found:** ❌ No

**Pattern 2:** `resource-migration-failures-dev`
- **Found:** ❌ No

**Pattern 3:** `resource-migration-failures-dev`
- **Found:** ❌ No

---

### Table: `resource-migration-success-dev`

**Pattern 1:** `resource-migration-success-dev`
- **Found:** ❌ No

**Pattern 2:** `resource-migration-success-dev`
- **Found:** ❌ No

**Pattern 3:** `resource-migration-success-dev`
- **Found:** ❌ No

---

