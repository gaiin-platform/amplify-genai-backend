# Migration Guide 

This guide outlines the migration process for eliminating the `amplify-lambda-basic-ops` service and transitioning all environment variables to AWS Parameter Store.

## 📝 **STEP 0: Configure Deployment Variables**

⚠️ **REQUIRED BEFORE RUNNING ANY MIGRATION SCRIPTS**

1. **Edit `scripts/config.py`** and update the deployment variables at the top:
   ```python
   # Configuration variables - UPDATE THESE BEFORE RUNNING MIGRATION SCRIPTS
   DEP_NAME = "v6"   # Change to your deployment name (e.g., "v6", "v7", "prod")  
   STAGE = "dev"     # Change to your stage (e.g., "dev", "staging", "prod")
   ```

2. **Example configurations**:
   - Production deployment: `DEP_NAME = "prod"`, `STAGE = "prod"`
   - V7 development: `DEP_NAME = "v7"`, `STAGE = "dev"`
   - Staging environment: `DEP_NAME = "v6"`, `STAGE = "staging"`

These variables will be used to generate correct table and bucket names for your specific deployment during migration.

## 🚨 **CRITICAL: Parameter Store Dependency**

⚠️ **ALL Lambda services now depend on AWS Parameter Store for shared configuration variables.**

### **Before Any Deployments**

1. **Var files still required during migration**: Keep your `/var/{stage}-var.yml` files during the migration period
2. **NEW REQUIREMENT: Add LOG_LEVEL to var files**: Add the `LOG_LEVEL` variable to your `/var/{stage}-var.yml` files:
   ```yaml
   # Example for dev-var.yml
   LOG_LEVEL: DEBUG
   
   # Example for staging-var.yml  
   LOG_LEVEL: INFO
   
   # Example for prod-var.yml
   LOG_LEVEL: WARNING
   ```
   Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
3. **MANDATORY: Run populate script FIRST**:
   ```bash
   python3 scripts/populate_parameter_store.py --stage dev --dep-name your-dep-name
   ```
4. **Verify parameters**: Ensure all shared parameters are populated before deploying any service

### **Shared Variables in Parameter Store**

The following variables are now loaded from Parameter Store instead of var files:
- `DEP_NAME`, `CUSTOM_API_DOMAIN`, `OAUTH_AUDIENCE`, `OAUTH_ISSUER_BASE_URL` 
- `IDP_PREFIX`, `LOG_LEVEL`, `LLM_ENDPOINTS_SECRETS_NAME_ARN`, `COGNITO_USER_POOL_ID`
- `CHANGE_SET_BOOLEAN`, `DEP_REGION`, `PANDOC_LAMBDA_LAYER_ARN`
- And 12 additional shared configuration variables

### **Migration Order**

1. **FIRST**: Run `scripts/populate_parameter_store.py` to create shared parameters
2. **THEN**: Deploy services (they will read from Parameter Store)
3. **AFTER**: Successful deployment, var files can eventually be deprecated

**⚠️ Services WILL FAIL to deploy if Parameter Store is not populated first!**

## 🚨 **CRITICAL: User ID Migration Requirements**

### **Who Needs This Migration?**

This migration serves **TWO PURPOSES**:

1. **🔄 User ID Format Change**: Updating user identifiers from **mutable email addresses** to **immutable usernames**
2. **📦 S3 Consolidation**: Migrating scattered S3 bucket data to centralized storage (required for ALL deployments)

### **Migration Scenarios**

#### **Scenario A: Email → Immutable ID Migration** 
**Use Case**: Your users currently authenticate with email addresses, but you want immutable usernames.

**CSV Format**: Different values in each column
```csv
old_id,new_id
karely.rodriguez@vanderbilt.edu,rodrikm1
allen.karns@vanderbilt.edu,karnsab
```
**Result**: All user data migrated from email-based keys to username-based keys.

---

#### **Scenario B: Same ID + S3 Consolidation Only**
**Use Case**: Your user IDs are already immutable, but you need S3 bucket consolidation.

**CSV Format**: Same value in both columns  
```csv
old_id,new_id
rodrikm1,rodrikm1
karnsab,karnsab
```
**Result**: User IDs remain unchanged, but S3 data gets consolidated and organized.

---

### **⚠️ Important Notes**

- **ALL deployments must run this migration** - even if user IDs don't change
- **S3 consolidation is mandatory** for eliminating extra bucket resources
- **The migration is safe** - it copies data, doesn't delete original sources
- **Scripts are idempotent** - safe to re-run if needed

---

## Overview

The biggest change in this migration is:
- **Eliminating** the `amplify-lambda-basic-ops` service
- **Migrating** all locally defined environment variables to AWS Parameter Store
- **Updating** all serverless.yml files to reference Parameter Store instead of hardcoded values

## Migration Steps

### Step 1: Populate AWS Parameter Store

**CRITICAL**: This step must be completed BEFORE deleting the basic-ops service.

#### Prerequisites
1. Ensure you are at the **root** of the amplify-genai-backend repository
2. **Configure AWS credentials** using one of these methods:

   **Option A: Environment Variables (Simpler)**
   ```bash
   export AWS_ACCESS_KEY_ID=your_access_key_here
   export AWS_SECRET_ACCESS_KEY=your_secret_key_here
   export AWS_SESSION_TOKEN=your_session_token_here  # Only if using temporary credentials
   export AWS_DEFAULT_REGION=us-east-1
   ```

   **Option B: AWS Credentials File (More Persistent)**
   ```bash
   # Configure credentials in ~/.aws/credentials
   aws configure
   # Then set profile
   export AWS_PROFILE=your_profile_name
   ```

   **Option C: AWS SSO (If Available)**
   ```bash
   aws sso login --profile your-profile
   export AWS_PROFILE=your-profile
   ```

#### Run the Population Script

```bash
# Dry run first (recommended to verify what will be created)
python3 scripts/populate_parameter_store.py --stage dev --dep-name v6 --dry-run

# Actual run to populate Parameter Store
python3 scripts/populate_parameter_store.py --stage dev --dep-name v6

# For other environments:
python3 scripts/populate_parameter_store.py --stage staging --dep-name v6
python3 scripts/populate_parameter_store.py --stage prod --dep-name v6
```

#### What This Script Does

The `scripts/populate_parameter_store.py` script:

1. **Discovers Services**: Scans all directories for `serverless.yml` files
2. **Extracts Variables**: Parses only the "# Locally Defined Variables" sections
3. **Resolves Placeholders**: Replaces `${self:service}` with actual service names
4. **Creates Parameters**: Stores variables in Parameter Store with path format:
   ```
   /amplify/{stage}/{service_name}/{variable_name}
   ```

#### Example Output
```
✓ amplify-v6-lambda: 24/24 parameters
✓ amplify-v6-artifacts: 3/3 parameters
✓ amplify-v6-object-access: 5/5 parameters
... (15 services total, 84 parameters)
```

### Step 2: Verify Parameter Store Population

After running the script, verify parameters were created:

```bash
# List all parameters for your deployment
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/amplify/dev/"

# Check a specific parameter
aws ssm get-parameter --name "/amplify/dev/amplify-v6-lambda/ACCOUNTS_DYNAMO_TABLE"
```

### Step 3: Update Service Dependencies

The serverless.yml files have been updated to reference Parameter Store instead of basic-ops outputs. Variables now use the format:

```yaml
environment:
  ACCOUNTS_DYNAMO_TABLE: ${ssm:/amplify/${sls:stage}/${self:service}/ACCOUNTS_DYNAMO_TABLE}
```

### Step 4: Handle User Storage Table Migration and API Gateway Conflicts

## 🚨 **IMPORTANT PREREQUISITES** 

### **📊 User Storage Data Migration**
**ONLY REQUIRED IF**: You have data in the `amplify-v6-lambda-basic-ops-dev-user-storage` table.

**Check if you need this**:
```bash
# Check if user storage table exists and has data
aws dynamodb scan --table-name amplify-v6-lambda-basic-ops-dev-user-storage --select COUNT
```
- **If table doesn't exist or COUNT = 0**: Skip user storage migration steps
- **If COUNT > 0**: Follow user storage migration steps below

### **🛠️ API Gateway Endpoint Conflict** 
**ONLY REQUIRED IF**: You have previously deployed `amplify-lambda-basic-ops` service.

**Check if you need this**:
```bash
# Check if basic-ops CloudFormation stack exists
aws cloudformation describe-stacks --stack-name amplify-v6-lambda-basic-ops-dev
```
- **If stack doesn't exist**: No conflict resolution needed
- **If stack exists**: Must free `/user-data` endpoint before deploying amplify-lambda

---

#### Background (If Migration Required)
- **Old table**: `amplify-v6-lambda-basic-ops-dev-user-storage` (in basic-ops service)
- **New table**: `amplify-v6-lambda-dev-user-data-storage` (in amplify-lambda service) 
- **API Conflict**: Both services try to use `/user-data` endpoint, causing deployment failures
- **Error**: `CREATE_FAILED: ApiGatewayResourceUserDashdata - Another resource with the same parent already has this name: user-data`

#### Required Deployment Sequence (If Both Prerequisites Apply)

**⚠️ CRITICAL**: Follow this exact order to avoid API Gateway conflicts:

##### Step 4a: Backup User Storage Data (If Table Has Data)
```bash
# Create backup of user storage table BEFORE freeing /user-data endpoint
python3 scripts/user_storage_backup.py
```

##### Step 4b: Free the `/user-data` API Gateway Endpoint
**REQUIREMENT**: The `/user-data` endpoint must be freed before deploying amplify-lambda.

**How to accomplish this**: Use any method to remove or disable the basic-ops service that currently owns this endpoint:
- Remove the CloudFormation stack
- Disable the serverless service
- Delete the API Gateway resource manually
- Any other method that frees the `/user-data` path

**The goal**: Ensure no service currently claims the `/user-data` endpoint path.

##### Step 4c: Deploy Amplify Lambda with New Table
```bash
# Deploy amplify-lambda (now /user-data endpoint is available)
serverless amplify-lambda:deploy --stage dev
```

##### Step 4d: Import User Storage Data (If You Backed Up Data)
The user storage data will be automatically imported during the ID migration script (Step 6). No manual import is needed - the migration script handles this automatically during Step 2 of the migration process.

**✅ This sequence resolves the API Gateway `/user-data` endpoint conflict by ensuring only one service owns the endpoint at any time.**

---

#### Alternative: Skip User Storage Migration Entirely
**If your user storage table is empty or doesn't exist**:
1. Skip all backup steps
2. Free the `/user-data` endpoint (if needed)  
3. Deploy amplify-lambda directly
4. Proceed to Step 5

### Step 5: Deploy Other Services with New Configuration

Deploy remaining services with the updated Parameter Store configuration:

```bash
# Deploy from repository root (REQUIRED for proper variable resolution)
serverless amplify-assistants:deploy --stage dev
serverless amplify-lambda-js:deploy --stage dev
# ... continue for all other services (except basic-ops which is removed)
```

## Verification

### Test Service Functionality
After migration, verify that all services are functioning correctly:

1. Check service health endpoints
2. Verify database connectivity
3. Test core functionality
4. Monitor CloudWatch logs for any missing environment variables

### Rollback Plan

If issues occur:

1. **Keep Parameter Store intact** (it doesn't interfere with existing deployments)
2. **Redeploy services** from the previous branch/commit
3. **Investigate issues** before attempting migration again

## Benefits of This Migration

1. **Centralized Configuration**: All environment variables in one location
2. **Better Security**: Parameter Store provides encryption and access control
3. **Simplified Dependencies**: Eliminates cross-service CloudFormation dependencies
4. **Easier Management**: Update configurations without redeploying services

## Important Notes

- **Deploy Order**: Always deploy from repository root for proper variable resolution
- **AWS Credentials**: Ensure proper IAM permissions for Parameter Store access
- **Stage Consistency**: Use consistent stage names across all services
- **Backup**: Parameter Store maintains version history automatically
- **Monitoring**: Watch CloudWatch logs during the migration for any issues

## Step 6: Run ID Migration Scripts

After Parameter Store population and service deployments, run the user ID migration scripts to migrate all user data across DynamoDB tables and S3 buckets.

### Script Overview

#### 1. ID Migration Script (`id_migration.py`)
**Purpose**: Migrates user IDs from old format (email) to new format (username) across all DynamoDB tables and triggers S3 migrations.

**Features**:
- Processes CSV file with old_id → new_id mappings
- Updates 40+ DynamoDB tables with user ID references
- Migrates associated S3 bucket data to consolidation bucket
- Comprehensive logging with dry-run capability
- Handles complex data structures (admin configs, feature flags, etc.)

**Command Line Options**:
- `--dry-run`: Do not make any changes, just show what would happen
- `--csv-file`: Path to the CSV file containing migration data (default: migration_users.csv)
- `--no-id-change`: Generate migration_users.csv with same old_id and new_id for S3 consolidation only (no username changes)
- `--dont-backup`: **NEW** - Skip both backup creation and verification (for users who already have backups)
- `--region`: **NEW** - AWS region for DynamoDB and S3 operations (default: us-east-1)
- `--log`: Log output to the specified file (auto-generated if not provided)

**⚠️ All table and bucket names come exclusively from `config.py` - no command-line overrides allowed for consistency.**

#### 2. S3 Data Migration Script (`s3_data_migration.py`)
**Purpose**: Migrates data from legacy S3 buckets to consolidated storage.

**Migration Types**:
- **To Consolidation Bucket**: Conversations, shares, code interpreter files, agent state, group conversations
- **To USER_DATA_STORAGE_TABLE**: Artifacts, workflow templates, scheduled task logs, user settings
- **Standalone Buckets**: Data disclosure storage, API documentation

### Prerequisites

#### Critical Infrastructure Requirements

⚠️ **MANDATORY: The migration script now performs automatic prerequisite validation**

Before migration begins, the script validates that critical infrastructure exists:

1. **S3 Consolidation Bucket** (`S3_CONSOLIDATION_BUCKET_NAME`): Required for all S3 migrations
2. **User Data Storage Table** (`USER_DATA_STORAGE_TABLE`): Required for data consolidation

**If these don't exist**, the script will display:
```
❌ CRITICAL ERROR: S3 consolidation bucket does not exist!
🚨 MIGRATION CANNOT PROCEED!
SOLUTION: Deploy amplify-lambda service first:
   serverless amplify-lambda:deploy --stage dev
```

**✅ Deploy amplify-lambda service FIRST** to create required infrastructure before running migration.

#### Backup Strategy

The migration script includes built-in backup verification to ensure data safety:

**Option 1: Automatic Backup Verification (Default)**
```bash
# Script automatically checks for recent backups before migration
python3 scripts/id_migration.py --csv-file migration_users.csv --log migration_full.log
```

**Option 2: Skip Backup Process (For Users Who Already Have Backups)**
```bash
# Use this if you've already created backups manually
python3 scripts/id_migration.py --dont-backup --csv-file migration_users.csv --log migration_full.log
```

**Option 3: Specify Custom AWS Region**
```bash
# Use this if your AWS resources are in a different region
python3 scripts/id_migration.py --region us-west-2 --csv-file migration_users.csv --log migration_full.log
```

**Creating Backups Manually:**
```bash
# Create comprehensive backups before migration
python3 scripts/backup_prereq.py --backup-name "pre-migration-$(date +%Y%m%d-%H%M%S)"

# Verify backups exist
python3 scripts/backup_prereq.py --verify-only --backup-name "pre-migration-20241022"
```

#### CSV File Setup

**Option 1: Manual CSV Creation**
Create `migration_users.csv` with user mappings for ID changes:
```csv
old_id,new_id
karely.rodriguez@vanderbilt.edu,rodrikm1
allen.karns@vanderbilt.edu,karnsab
```

**Option 2: Automatic CSV Generation (No ID Changes)**
If you only need S3 consolidation without changing user IDs, use the `--no-id-change` flag:
```bash
# Automatically generates migration_users.csv from all Cognito users
python3 scripts/id_migration.py --no-id-change --dry-run --log migration_setup.log

# This creates migration_users.csv with same old_id and new_id:
# old_id,new_id
# user1,user1
# user2,user2
# ...
```

This is useful when:
- Your user IDs are already in the correct format (e.g., already using usernames)
- You only need to migrate data to consolidation buckets
- You want to ensure all users are included in the migration

#### Automatic Configuration Loading
**✅ NEW: No manual environment variables needed!**

The migration scripts now automatically load all bucket and table names from `config.py`. 
Make sure you've configured your `DEP_NAME` and `STAGE` in Step 0.

```bash
# All bucket names are loaded from config.py automatically
# No need to set environment variables manually!
```

**Optional Override**: If you need to use different bucket names than those in config.py, 
you can still set environment variables which will take precedence:
```bash
export S3_CONSOLIDATION_BUCKET_NAME="custom-consolidation-bucket"  # Optional override
```

### Running the Migration Scripts

#### Phase 1: Dry Run Analysis
**ALWAYS run dry runs first** to understand scope and verify correctness:

```bash
# 1. Dry run ID migration (shows what tables/data would be migrated)
python3 scripts/id_migration.py --dry-run --csv-file migration_users.csv --log migration_dryrun.log

# 3. Dry run S3-only migration (standalone buckets)  
python3 scripts/s3_data_migration.py --dry-run --bucket all --log s3_dryrun.log

# 4. Review logs to understand migration scope
tail -f migration_dryrun.log
```

#### Phase 2: Execute Migration
**Run actual migration after dry run validation**:

```bash
# 1. Execute ID migration (includes user storage migration + offers S3 migration)
python3 scripts/id_migration.py --csv-file migration_users.csv --log migration_full.log

# 1a. If you already have backups, skip backup verification:
python3 scripts/id_migration.py --dont-backup --csv-file migration_users.csv --log migration_full.log

# 2. Optional: Execute standalone S3 bucket migrations manually
python3 scripts/s3_data_migration.py --bucket all --log s3_migration.log

# 3. Monitor progress in real-time
tail -f migration_full.log
```

**⚡ New Feature**: The ID migration script now automatically:
- **Migrates user storage table** from basic-ops to amplify-lambda
- **Offers S3 bucket migration** after completing user ID migration
- **Integrates both processes** for a streamlined migration experience

### Migration Scope and Impact

#### Automatic Resource Detection

**NEW**: The migration script automatically detects which resources exist and adapts the migration plan accordingly:

- ✅ **Tables/Buckets that exist**: Will be migrated
- ⏭️ **Missing resources**: Automatically skipped with clear logging
- 🚨 **Critical missing infrastructure**: Migration stops with deployment instructions

#### DynamoDB Tables Updated
The ID migration script processes **40+ tables** including:

**Core Tables**: User identity, accounts, API keys, conversations, files  
**AI/Assistant Tables**: Assistant definitions, threads, code interpreter, groups  
**Agent Tables**: Agent state, workflow templates, scheduled tasks, email settings  
**Administrative**: Admin configs, object access, OAuth integrations, cost tracking

**Special Handling**:
- `USER_DATA_STORAGE_TABLE`: Migrated from basic-ops to amplify-lambda with ID translation
- Tables with existence checking: Script validates table exists before processing
- Missing table handling: Graceful skipping with informational logging

#### S3 Data Migrations

**To Consolidation Bucket** (`S3_CONSOLIDATION_BUCKET_NAME`):
```
conversations/{new_user_id}/         ← S3_CONVERSATIONS_BUCKET_NAME/{old_user_id}/
shares/{recipient}/{sharer}/         ← S3_SHARE_BUCKET_NAME/{old_pattern}/  
codeInterpreter/{new_user_id}/       ← ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME/{old_user_id}/
agentState/{new_user_id}/            ← AGENT_STATE_BUCKET/{old_user_id}/
agentConversations/astgp/            ← S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME/astgp/
dataDisclosure/                      ← DATA_DISCLOSURE_STORAGE_BUCKET/
apiDocumentation/                    ← S3_API_DOCUMENTATION_BUCKET/
```

**To USER_DATA_STORAGE_TABLE** (DynamoDB):
```
{new_user_id}#amplify-artifacts#artifact-content     ← S3_ARTIFACTS_BUCKET content
{new_user_id}#amplify-workflows#workflow-templates   ← WORKFLOW_TEMPLATES_BUCKET content  
{new_user_id}#amplify-agent-logs#scheduled-task-logs ← SCHEDULED_TASKS_LOGS_BUCKET content
{new_user_id}#amplify-user-settings#user-settings    ← SHARES_DYNAMODB_TABLE.settings column
```

### Migration Validation

#### Verify Migration Success
```bash
# Check DynamoDB records updated
aws dynamodb query --table-name amplify-v6-object-access-dev-cognito-users --key-condition-expression "user_id = :uid" --expression-attribute-values '{":uid":{"S":"haysgs"}}'

# Check S3 consolidation bucket contents
aws s3 ls s3://amplify-v6-lambda-dev-consolidation/conversations/haysgs/ --recursive

# Check USER_DATA_STORAGE_TABLE entries  
aws dynamodb scan --table-name amplify-v6-lambda-basic-ops-dev-user-storage --filter-expression "contains(PK, :uid)" --expression-attribute-values '{":uid":{"S":"haysgs"}}'
```

#### Migration Rollback
If issues occur during migration:

1. **Database Rollback**: Use DynamoDB Point-in-Time Recovery
2. **S3 Rollback**: Objects remain in source buckets (migration copies, doesn't move)
3. **Selective Re-run**: Scripts are idempotent - safe to re-run for specific users

### Post-Migration Verification

#### Verify Migration Success
```bash
# Check updated DynamoDB records
aws dynamodb query --table-name amplify-v6-object-access-dev-cognito-users \
  --key-condition-expression "user_id = :uid" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'

# Check S3 consolidation bucket contents
aws s3 ls s3://amplify-v6-lambda-dev-user-data-storage/conversations/NEW_USER_ID/ --recursive

# Check USER_DATA_STORAGE_TABLE entries  
aws dynamodb scan --table-name amplify-v6-lambda-dev-user-data-storage \
  --filter-expression "contains(PK, :uid)" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'
```

#### Migration Rollback
If issues occur:

1. **Database Rollback**: Use DynamoDB Point-in-Time Recovery
2. **S3 Rollback**: Objects remain in source buckets (migration copies, doesn't move)
3. **Selective Re-run**: Scripts are idempotent - safe to re-run for specific users

### Key Migration Features

#### Smart Resource Detection
- **Prerequisite Validation**: Automatically checks for critical infrastructure before starting
- **Adaptive Migration Plans**: Skips missing resources with clear explanations
- **Comprehensive Logging**: Detailed logs with timestamps for troubleshooting

#### Error Handling  
- **Graceful Degradation**: Continues processing when non-critical resources are missing
- **Partial Migration Support**: Processes available resources, skips unavailable ones
- **Validation Checks**: Verifies S3 copies and DynamoDB updates before marking complete

#### Performance & Safety
- **Idempotent Operations**: Safe to re-run migrations multiple times
- **Backup Integration**: Built-in backup verification before dangerous operations
- **Size Monitoring**: Warns about DynamoDB 400KB item limit approaches

## Troubleshooting

### Migration Script Issues

#### Common Migration Issues

**Prerequisites Not Met**:
```bash
# Error: "CRITICAL ERROR: S3 consolidation bucket does not exist!"
# Solution: Deploy amplify-lambda service first
serverless amplify-lambda:deploy --stage dev
```

**Configuration Issues**:
```bash
# Verify config.py is properly configured
grep -E "^(DEP_NAME|STAGE)" scripts/config.py

# Validate configuration generates correct names
python3 -c "from config import get_config; print(get_config()['S3_CONSOLIDATION_BUCKET_NAME'])"
```

**CSV File Issues**:
```bash
# Check CSV format and duplicates
head -5 migration_users.csv
cut -d',' -f2 migration_users.csv | sort | uniq -d
```

**Access Issues**:
```bash
# Check AWS credentials and permissions
aws sts get-caller-identity
aws dynamodb list-tables --max-items 5
```

#### API Gateway Conflicts
**Error**: "Another resource with the same parent already has this name: user-data"

**SOLUTION**: Deploy amplify-lambda service first (before migration), which creates all required infrastructure:
```bash
# This creates the consolidation bucket and user data storage table
serverless amplify-lambda:deploy --stage dev
```

**If conflict exists**: Remove conflicting service first, then deploy amplify-lambda.

### Service Deployment Issues

#### Missing Parameters
```bash
# Check if parameter exists
aws ssm get-parameter --name "/amplify/dev/service-name/VARIABLE_NAME"

# Re-run population script
python3 scripts/populate_parameter_store.py --stage dev --dep-name v6
```

#### Required Permissions
Ensure Lambda execution roles have Parameter Store access:
- `ssm:GetParameter`, `ssm:GetParameters`, `ssm:GetParametersByPath`