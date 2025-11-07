# Migration Guide 

## ✨ **FRESH DEPLOYMENT? YOU'RE IN LUCK!**

**If this is a brand new deployment with no existing data:**

✅ **You ONLY need to do Step 1 & 2 (Environment Variables + Parameter Store)**  
❌ **Skip Steps 3-5 entirely** - no migration needed!

```bash
# For fresh deployments, just do this:
1. Update scripts/config.py with your DEP_NAME and STAGE
2. Add LOG_LEVEL to your /var/{stage}-var.yml files
3. Run: python3 scripts/populate_parameter_store.py --stage dev --dep-name v6
4. Deploy services normally: serverless amplify-lambda:deploy --stage dev
   # Don't forget: cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..
5. Done! 🎉
```

**Continue reading only if you have existing deployments with data to migrate.**

---

🚨 **For existing deployments: Change sets and backups are HIGHLY recommended before starting any migration process.**

This guide outlines the migration process for eliminating the `amplify-lambda-basic-ops` service, transitioning all environment variables to AWS Parameter Store, and consolidating S3 data storage.

## 📋 **WHAT'S IN THIS DOCUMENT - READ ONLY WHAT APPLIES TO YOU**

### 🟢 **EVERYONE MUST DO** (No Exceptions)
- [**Step 1: Environment Variables Setup**](#step-1-configure-deployment-variables-mandatory) - Configure deployment variables
- [**Step 2: Parameter Store Population**](#step-2-parameter-store-setup-mandatory) - Populate AWS Parameter Store

### 🟡 **CONDITIONAL SECTIONS** (Read Based on Your Situation)
- [**Step 3a: IF YOU HAVE Basic Ops Service**](#step-3a-if-you-have-basic-ops-service) - Special deployment sequence
- [**Step 3b: IF YOU DON'T HAVE Basic Ops**](#step-3b-if-you-dont-have-basic-ops-service) - Standard deployment
- [**Step 4a: IF YOU NEED User ID Migration**](#step-4a-if-you-need-user-id-migration) - Email → Username migration
- [**Step 4b: IF YOU ONLY NEED S3 Consolidation**](#step-4b-if-you-only-need-s3-consolidation-highly-recommended) - Same IDs, consolidate buckets

### 🔵 **OPTIONAL BUT RECOMMENDED**
- [**Backup Strategy**](#backup-strategy-recommended) - How backups work vs migration verification
- [**Advanced Troubleshooting**](#troubleshooting) - For when things go wrong

### 🎯 **MIGRATION GOALS**
1. **Immediate**: Remove dependency on `amplify-lambda-basic-ops` service
2. **Data Safety**: Migrate user data to consolidated, immutable ID system  
3. **Future Cleanup**: Eventually delete old S3 buckets and tables (code remains backward compatible until then)
4. **⚠️ Important**: If you skip consolidation and we later delete old buckets/tables, you may lose data when pulling in changes

---

## 🚨 **STEP 1: Configure Deployment Variables (MANDATORY)**

### ⚠️ **REQUIRED BEFORE RUNNING ANY MIGRATION SCRIPTS - NO EXCEPTIONS**

1. **Edit `scripts/config.py`** and update the deployment variables at the top:
   ```python
   # Configuration variables - UPDATE THESE BEFORE RUNNING MIGRATION SCRIPTS
   DEP_NAME = "v6"  # Change this to match your deployment name (e.g., "v6", "v7", etc.)
   STAGE = "dev"    # Change this to match your deployment stage (e.g., "dev", "staging", "prod")
   LOG_LEVEL = "INFO" # Valid Values Include: DEBUG | INFO | WARNING | ERROR | CRITICAL
   ```
   
   **⚠️ CRITICAL**: These values **MUST** match your actual AWS deployment. All table and bucket names are generated from these variables.

2. **Example configurations**:
   - Production deployment: `DEP_NAME = "prod"`, `STAGE = "prod"`
   - V7 development: `DEP_NAME = "v7"`, `STAGE = "dev"`
   - Staging environment: `DEP_NAME = "v6"`, `STAGE = "staging"`

These variables will be used to generate correct table and bucket names for your specific deployment during migration.

3. **Validate your configuration** (recommended):
   ```bash
   # Verify config.py generates correct resource names for your deployment
   cd scripts && python3 -c "from config import get_config; config = get_config(); print('✓ S3 Consolidation Bucket:', config['S3_CONSOLIDATION_BUCKET_NAME']); print('✓ User Data Storage Table:', config['USER_DATA_STORAGE_TABLE']); print('✓ Accounts Table:', config['ACCOUNTS_DYNAMO_TABLE'])"
   ```
   
   **Expected output format** (for DEP_NAME="v6", STAGE="dev"):
   ```
   ✓ S3 Consolidation Bucket: amplify-v6-lambda-dev-consolidation
   ✓ User Data Storage Table: amplify-v6-lambda-dev-user-data-storage
   ✓ Accounts Table: amplify-v6-lambda-dev-accounts
   ```
   
   If the resource names don't match your actual AWS deployment, **update `DEP_NAME` and `STAGE` in config.py**.

---

## 🚨 **STEP 2: Parameter Store Setup (MANDATORY)**

### ⚠️ **ALL Lambda services now depend on AWS Parameter Store for shared configuration variables.**

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

```python
shared_var_names = [
    'ADMINS', 'CHANGE_SET_BOOLEAN', 'CUSTOM_API_DOMAIN', 'DEP_REGION', 'IDP_PREFIX',
    'LOG_LEVEL', 'OAUTH_AUDIENCE', 'OAUTH_ISSUER_BASE_URL', 'PANDOC_LAMBDA_LAYER_ARN',
    'ASSISTANTS_OPENAI_PROVIDER', 'LLM_ENDPOINTS_SECRETS_NAME_ARN',
    'AGENT_ENDPOINT', 'BEDROCK_GUARDRAIL_ID', 'BEDROCK_GUARDRAIL_VERSION',
    'COGNITO_CLIENT_ID', 'COGNITO_USER_POOL_ID', 'ORGANIZATION_EMAIL_DOMAIN',
    'API_VERSION', 'MAX_ACU', 'MIN_ACU', 'PRIVATE_SUBNET_ONE',
    'PRIVATE_SUBNET_TWO', 'VPC_CIDR', 'VPC_ID'
]
```

**Total**: 24 shared configuration variables loaded from Parameter Store

### **Migration Order**

1. **FIRST**: Run `scripts/populate_parameter_store.py` to create shared parameters
2. **THEN**: Deploy services (they will read from Parameter Store)
3. **AFTER**: Successful deployment, var files can eventually be deprecated

**⚠️ Services WILL FAIL to deploy if Parameter Store is not populated first!**

---

## 🟠 **STEP 3: Deploy Services** 

### 🟡 **STEP 3a: IF YOU HAVE Basic Ops Service**

**How to check if you have Basic Ops service:**
```bash
# Check if basic-ops CloudFormation stack exists
aws cloudformation describe-stacks --stack-name amplify-{DEP_NAME}-lambda-basic-ops-{STAGE}

# Example for DEP_NAME="v6", STAGE="dev":
aws cloudformation describe-stacks --stack-name amplify-v6-lambda-basic-ops-dev
```

**If the stack exists, follow these steps:**

#### 3a.1: Check for User Storage Data Migration Need
```bash
# Check if user storage table has data
aws dynamodb scan --table-name amplify-{DEP_NAME}-lambda-basic-ops-{STAGE}-user-storage --select COUNT

# Example:
aws dynamodb scan --table-name amplify-v6-lambda-basic-ops-dev-user-storage --select COUNT
```

**Decision Tree:**
- **If COUNT = 0 or table doesn't exist**: ➡️ Go to [3a.2a: No Data Path](#3a2a-no-data-path)
- **If COUNT > 0**: ➡️ Go to [3a.2b: Has Data Path](#3a2b-has-data-path) 

#### 3a.2a: No Data Path (Safe to Remove Basic Ops)
**Since you have no user storage data, you can completely remove basic-ops:**

```bash
# Remove basic-ops service completely (frees /user-data endpoint)
serverless amplify-lambda-basic-ops:remove --stage dev

# Now deploy amplify-lambda (endpoint is free)
serverless amplify-lambda:deploy --stage dev

# 🚨 CRITICAL: After deployment, run the markitdown script
cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..  
```

**➡️ Skip to [3a.3: Deploy Other Services](#3a3-deploy-other-services)**

#### 3a.2b: Has Data Path (Must Keep Basic Ops Until Migration)
**Since you have user storage data, you must keep basic-ops until Step 4 migration:**

**🚨 Problem:** Both basic-ops and amplify-lambda try to use `/user-data` endpoint

**✅ Solution:** Temporarily disable basic-ops `/user-data` endpoint:

```bash
# Option 1: Comment out the user-data endpoint in basic-ops serverless.yml
# Edit amplify-lambda-basic-ops/serverless.yml and comment out:
#   - http:
#       path: /user-data
#       method: post

# Then redeploy basic-ops without the endpoint
serverless amplify-lambda-basic-ops:deploy --stage dev

# Now deploy amplify-lambda (endpoint is free) 
serverless amplify-lambda:deploy --stage dev

# 🚨 CRITICAL: After deployment, run the markitdown script
cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..  
```

**Alternative Options to Free Endpoint:**
- **Option 2**: Temporarily remove basic-ops stack (`serverless amplify-lambda-basic-ops:remove`) but **BACKUP FIRST**
- **Option 3**: Manually delete API Gateway resource in AWS Console
- **Goal**: Ensure amplify-lambda can claim `/user-data` endpoint

**⚠️ Important:** Keep basic-ops data accessible until Step 4 migration completes

#### 3a.3: Deploy Other Services
```bash  
# Deploy remaining services
serverless amplify-assistants:deploy --stage dev
serverless amplify-lambda-js:deploy --stage dev
# Continue for other services...

# Note: If you have user storage data, basic-ops will be fully removed AFTER Step 4 migration
```

**➡️ Continue to [Step 4: Data Migration](#step-4-data-migration)**

---

### 🟢 **STEP 3b: IF YOU DON'T HAVE Basic Ops Service**

**This is the simpler path!**

#### 3b.1: Standard Service Deployment
```bash
# Deploy services in dependency order from repository root
# amplify-lambda is the BASE DEPENDENCY - deploy it FIRST
serverless amplify-lambda:deploy --stage dev

# 🚨 CRITICAL: After amplify-lambda deployment, run the markitdown script
cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..

# Now deploy other services that depend on amplify-lambda
serverless amplify-assistants:deploy --stage dev
serverless amplify-lambda-js:deploy --stage dev
# Continue for other services...
```

**➡️ Continue to [Step 4: Data Migration](#step-4-data-migration)**

---

## 🔄 **STEP 4: Data Migration**

### 🟡 **STEP 4a: IF YOU NEED User ID Migration** 

**Use this if:** Your users currently authenticate with a mutable field, but you want immutable usernames.

**What this does:** Migrates all user data from email-based keys to username-based keys + consolidates S3 storage.

#### 4a.1: Create Migration CSV
Create `migration_users.csv` with **different** values in each column:
```csv
old_id,new_id
karely.rodriguez@vanderbilt.edu,rodrikm1
allen.karns@vanderbilt.edu,karnsab
```

#### 4a.2: Run Migration

**🚨 IMPORTANT: Choose the right command for your situation:**

```bash
# 1. ALWAYS start with dry run to see what will happen
python3 scripts/id_migration.py --dry-run --csv-file migration_users.csv --log migration_dryrun.log

# 2. STANDARD migration (script will handle backups automatically)
python3 scripts/id_migration.py --csv-file migration_users.csv --log migration_full.log

# 3. IF YOU ALREADY HAVE BACKUPS (skip backup verification)
python3 scripts/id_migration.py --dont-backup --csv-file migration_users.csv --log migration_full.log

# 4. FOR AUTOMATION/CI-CD (no interactive prompts)
python3 scripts/id_migration.py --no-confirmation --csv-file migration_users.csv --log migration_full.log

# 5. IF NOT IN us-east-1 region
python3 scripts/id_migration.py --region us-west-2 --csv-file migration_users.csv --log migration_full.log
```

**📚 Need more options?** See [ID Migration Scripts Technical Details](#id-migration-scripts-technical-details) for all command-line options.

**✅ Result:** All user data migrated from email-based keys to username-based keys + S3 consolidation completed.

---

### 🟠 **STEP 4b: IF YOU ONLY NEED S3 Consolidation (HIGHLY RECOMMENDED)**

**Use this if:** Your user IDs are already immutable, but you need S3 bucket consolidation.

**⚠️ Why this is highly recommended:** 
- **Goal**: Eventually delete old S3 buckets and tables to clean up resources
- **Backward Compatibility**: Code currently works with old buckets, but if they're deleted and you pull in changes, you may lose data
- **Future-Proofing**: Ensures your data is in the correct consolidated location

#### 4b.1: Auto-Generated Migration CSV (Same IDs)
```bash
# Migration automatically creates migration_users.csv with same old_id and new_id
python3 scripts/id_migration.py --no-id-change --dry-run --log migration_setup.log
```

This creates:
```csv
old_id,new_id
rodrikm1,rodrikm1
karnsab,karnsab
```

#### 4b.2: Run Consolidation Migration

**🚨 IMPORTANT: Choose the right command for your situation:**

```bash
# 1. ALWAYS start with dry run to see what will happen (no ID changes, just consolidation)
python3 scripts/id_migration.py --no-id-change --dry-run --log consolidation_dryrun.log

# 2. STANDARD consolidation (script will handle backups automatically)  
python3 scripts/id_migration.py --no-id-change --log migration_consolidation.log

# 3. IF YOU ALREADY HAVE BACKUPS (skip backup verification)
python3 scripts/id_migration.py --no-id-change --dont-backup --log migration_consolidation.log

# 4. FOR AUTOMATION/CI-CD (no interactive prompts)
python3 scripts/id_migration.py --no-id-change --no-confirmation --log migration_consolidation.log

# 5. IF NOT IN us-east-1 region
python3 scripts/id_migration.py --no-id-change --region us-west-2 --log migration_consolidation.log
```

**📚 Need more options?** See [ID Migration Scripts Technical Details](#id-migration-scripts-technical-details) for all command-line options.

**✅ Result:** User IDs remain unchanged, but S3 data gets consolidated and organized for future cleanup.

---

## 🧹 **STEP 5: Final Cleanup (For Basic Ops Users Only)**

### 🟡 **IF YOU HAD Basic Ops Service with Data (Step 3a.2b path)**

**After Step 4 migration completes successfully, you can now safely remove basic-ops:**

```bash
# Verify migration completed successfully first
# Check that your data is in the new amplify-lambda user-data-storage table
aws dynamodb scan --table-name amplify-{DEP_NAME}-lambda-{STAGE}-user-data-storage --select COUNT

# If migration was successful, remove basic-ops completely
serverless amplify-lambda-basic-ops:remove --stage dev
```

**🎉 Congratulations!** Your migration is now complete:
- ✅ Basic-ops service removed
- ✅ User data migrated to consolidated storage  
- ✅ S3 buckets consolidated
- ✅ Environment variables in Parameter Store
- ✅ Clean, modern infrastructure setup

### 🟢 **IF YOU DIDN'T HAVE Basic Ops Service (Step 3b path)**

**No cleanup needed!** Your migration is already complete.

---

## 🔧 **Advanced Technical Details**

### **🔧 Environment Variable Tracking System**

**NEW**: All Lambda functions now use the `@required_env_vars` decorator to specify their environment variable requirements and AWS operation usage.

#### **How the @required_env_vars Decorator Works**

**Purpose**: The decorator serves three critical functions:
1. **Environment Variable Resolution**: Automatically resolves variables from Lambda environment → Parameter Store → Error
2. **Usage Tracking**: Records which functions use which environment variables and AWS operations in DynamoDB
3. **IAM Documentation**: Provides precise AWS operation requirements for security auditing and IAM policy creation

#### **Decorator Syntax**

```python
@required_env_vars({
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "USER_TAGS_DYNAMO_TABLE": [DynamoDBOperation.UPDATE_ITEM, DynamoDBOperation.PUT_ITEM],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT, S3Operation.PUT_OBJECT]
})
def my_lambda_function(event, context):
    # Function implementation
```

#### **Environment Variable Population Process**

**CRITICAL**: The decorator creates entries in the `ENV_VARS_TRACKING_TABLE` which:
- **Specifies** which environment variables each Lambda function requires
- **Documents** the specific AWS operations (DynamoDB, S3, etc.) each function performs
- **Tracks** environment variable usage across the entire platform
- **Provides** IAM policy requirements for security auditing

#### **Lambda Execution Requirements**

⚠️ **IMPORTANT**: Lambdas decorated with `@required_env_vars` will **NOT be runnable** until:

1. **Parameter Store is populated** with required values:
   ```bash
   python3 scripts/populate_parameter_store.py --stage dev --dep-name v6
   ```

2. **Environment variables are resolved** from Parameter Store during Lambda execution

3. **Tracking entries are created** in the `ENV_VARS_TRACKING_TABLE` automatically

#### **Decorator Benefits**

- **Automatic Resolution**: No manual environment variable configuration needed
- **Security Auditing**: Clear documentation of AWS operations for IAM policies
- **Centralized Tracking**: Single source of truth for environment variable usage
- **Error Prevention**: Early failure if required variables are missing
- **Compliance**: Detailed logging of AWS resource access patterns

## 📊 **BACKUP STRATEGY (RECOMMENDED)**

### **Understanding Backups vs Migration Verification**

**Two different things:**
1. **Backups**: Creating snapshots of your data BEFORE migration (for recovery if something goes wrong)
2. **Migration Verification**: Checking that backups exist before starting migration (safety check)

### **How It Works:**

#### **Option 1: Automatic Backup Verification (Recommended)**
```bash
# Migration script automatically checks for recent backups before proceeding
python3 scripts/id_migration.py --csv-file migration_users.csv
# ✓ Script will verify backups exist or offer to create them
```

#### **Option 2: Manual Backup Creation** 
```bash
# Create backups manually before migration
python3 scripts/backup_prereq.py --backup-name "pre-migration-$(date +%Y%m%d-%H%M%S)"

# Then run migration with backup verification skip
python3 scripts/id_migration.py --dont-backup --csv-file migration_users.csv
```

**🚨 Why Backups Are Critical:**
- Migration **automatically deletes old data** after successful copying
- **No separate cleanup step** - cleanup happens during migration
- Backups are your **only recovery option** if something goes wrong

---

## 📝 **Migration Steps Overview**

The migration eliminates the `amplify-lambda-basic-ops` service and transitions all environment variables to AWS Parameter Store while consolidating S3 data storage.

### **Previously Covered Steps:**
- ✅ **Step 1**: Configure deployment variables 
- ✅ **Step 2**: Populate Parameter Store
- ✅ **Step 3**: Deploy services (with Basic Ops considerations)
- ✅ **Step 4**: Run data migration

---

## 🔧 **Advanced: Parameter Store Technical Details**

### Step 1: Populate AWS Parameter Store (Technical Reference)

**This step is already covered in Step 2 above, but here are technical details:**

#### Technical Prerequisites
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

#### Expected Output Example
When you run the populate script, you should see output like this:

```
Populating Parameter Store for stage: dev, dep_name: v6
Region: us-east-1
================================================================================
Found serverless.yml in: amplify-assistants
Found serverless.yml in: amplify-lambda
Found serverless.yml in: amplify-lambda-js
... (other services)

Found 15 serverless.yml files

Processing: amplify-lambda
==================================================
Service: amplify-v6-lambda
Found 24 locally defined variables:
  ACCOUNTS_DYNAMO_TABLE: amplify-v6-lambda-dev-accounts
  CONVERSATION_METADATA_TABLE: amplify-v6-lambda-dev-conversation-metadata
  ... (other variables)
Successfully processed 24/24 parameters

Processing: amplify-assistants  
==================================================
Service: amplify-v6-assistants
Found 8 locally defined variables:
  ... (variables for this service)
Successfully processed 8/8 parameters

... (other services)

================================================================================
SUMMARY
================================================================================
Total services processed: 15
Successful: 15
Failed: 0

Details:
✓ amplify-v6-lambda: 24/24 parameters
✓ amplify-v6-assistants: 8/8 parameters  
✓ amplify-v6-artifacts: 3/3 parameters
... (other services)

✓ All services processed successfully!
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

#### Technical Verification Commands

**Verify Parameter Store Population:**
```bash
# List all parameters for your deployment
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/amplify/dev/"

# Check a specific parameter
aws ssm get-parameter --name "/amplify/dev/amplify-v6-lambda/ACCOUNTS_DYNAMO_TABLE"
```

**Updated serverless.yml Format:**
Variables now use Parameter Store format:
```yaml
environment:
  ACCOUNTS_DYNAMO_TABLE: ${ssm:/amplify/${sls:stage}/${self:service}/ACCOUNTS_DYNAMO_TABLE}
```

---

## 📚 **Detailed Migration Scripts Reference**

**This section provides detailed technical reference for the migration scripts mentioned in Step 4 above.**

### **Migration Benefits**

1. **Centralized Configuration**: All environment variables in one location
2. **Better Security**: Parameter Store provides encryption and access control  
3. **Simplified Dependencies**: Eliminates cross-service CloudFormation dependencies
4. **Easier Management**: Update configurations without redeploying services
5. **Data Consolidation**: Eliminates scattered S3 buckets for cleaner architecture

### **Important Migration Notes**

- **Deploy Order**: Always deploy from repository root for proper variable resolution
- **AWS Credentials**: Ensure proper IAM permissions for Parameter Store access
- **Stage Consistency**: Use consistent stage names across all services  
- **Backup**: Parameter Store maintains version history automatically
- **Monitoring**: Watch CloudWatch logs during the migration for any issues
- **User Storage**: Data migration happens automatically during ID migration - no manual steps needed

### **ID Migration Scripts Technical Details**

**Detailed technical reference for the migration scripts used in Step 4:**

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
- `--dont-backup`: Skip both backup creation and verification (for users who already have backups)
- `--no-confirmation`: Skip all interactive prompts (useful for automation/CI/CD)
- `--region`: AWS region for DynamoDB and S3 operations (default: us-east-1)
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

**🚨 CRITICAL**: Backups are **ESSENTIAL** because the migration performs automatic cleanup during the process - old records are **permanently deleted** after successful migration.

**Why backups are necessary**:
- Migration **deletes old S3 files** after copying to consolidation bucket
- Migration **removes old DynamoDB records** after creating new ones  
- Migration **cleans up legacy data** automatically during execution
- **No separate cleanup step needed** - all cleanup happens during migration

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

The migration scripts now automatically load all bucket and table names from `config.py`. 

**🚨 PREREQUISITE**: You **MUST** have updated `DEP_NAME` and `STAGE` in `scripts/config.py` (Step 0) before running any migration scripts. The migration will **fail** if these don't match your actual AWS deployment.

```bash
# All bucket names are loaded from config.py automatically
# No need to set environment variables manually!
```


### Running the Migration Scripts

#### Phase 1: Dry Run Analysis
**ALWAYS run dry runs first** to understand scope and verify correctness:

```bash
# 1. Dry run ID migration (shows what tables/data would be migrated)
python3 scripts/id_migration.py --dry-run --csv-file migration_users.csv --log migration_dryrun.log

# 1a. Dry run with no prompts (for automation):
python3 scripts/id_migration.py --dry-run --no-confirmation --csv-file migration_users.csv --log migration_dryrun.log

# 2. Review logs to understand migration scope
tail -f migration_dryrun.log
```

#### Phase 2: Execute Migration
**Run actual migration after dry run validation**:

```bash
# 1. Execute ID migration (includes user storage migration + offers S3 migration)
python3 scripts/id_migration.py --csv-file migration_users.csv --log migration_full.log

# 1a. If you already have backups, skip backup verification:
python3 scripts/id_migration.py --dont-backup --csv-file migration_users.csv --log migration_full.log

# 1b. For automation/CI/CD (skip all prompts):
python3 scripts/id_migration.py --no-confirmation --csv-file migration_users.csv --log migration_full.log

# 2. Monitor progress in real-time
tail -f migration_full.log
```

**⚡ Comprehensive Migration Features**: The ID migration script provides:
- **User Storage Migration**: Automatically migrates data from old basic-ops user storage → new amplify-lambda user-data-storage
- **S3 Consolidation**: Migrates scattered S3 data → centralized consolidation bucket
- **Integrated Workflow**: Combines user ID updates, table migration, and S3 consolidation in one process
- **Smart Detection**: Automatically detects what needs migration and skips what's already done
- **Safety First**: Built-in backup verification and rollback capabilities

### Migration Scope and Impact

#### Automatic Resource Detection

The migration script automatically detects which resources exist and adapts the migration plan accordingly:

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
- **User Storage Migration**: Data from old `amplify-{dep-name}-lambda-basic-ops-{stage}-user-storage` → new `amplify-{dep-name}-lambda-{stage}-user-data-storage` with ID translation
- **Table Validation**: Script validates each table exists before processing
- **Missing Resources**: Graceful skipping with informational logging
- **Cross-Service Migration**: Handles data movement between different Lambda services

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
# Check DynamoDB records updated (using your deployment config)
aws dynamodb query --table-name amplify-v6-object-access-dev-cognito-users \
  --key-condition-expression "user_id = :uid" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'

# Check S3 consolidation bucket contents (replace bucket name with your deployment)
aws s3 ls s3://amplify-v6-lambda-dev-consolidation/conversations/NEW_USER_ID/ --recursive

# Check NEW user data storage table entries (replace table name with your deployment)
aws dynamodb scan --table-name amplify-v6-lambda-dev-user-data-storage \
  --filter-expression "contains(PK, :uid)" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'
```

**Replace in commands**:
- Replace `v6` → your `DEP_NAME`
- Replace `dev` → your `STAGE` 
- Replace `NEW_USER_ID` → actual migrated user ID

#### Migration Rollback
If issues occur during migration:

1. **Database Rollback**: Use DynamoDB Point-in-Time Recovery
2. **S3 Rollback**: Objects remain in source buckets (migration copies, doesn't move)
3. **Selective Re-run**: Scripts are idempotent - safe to re-run for specific users

### Post-Migration Verification

#### Final Migration Verification
```bash
# 1. Check user ID updates in DynamoDB tables
aws dynamodb query --table-name amplify-v6-object-access-dev-cognito-users \
  --key-condition-expression "user_id = :uid" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'

# 2. Check S3 consolidation bucket (conversations migrated here)
aws s3 ls s3://amplify-v6-lambda-dev-consolidation/conversations/NEW_USER_ID/ --recursive

# 3. Check USER_DATA_STORAGE_TABLE (artifacts, workflows, etc. migrated here)
aws dynamodb scan --table-name amplify-v6-lambda-dev-user-data-storage \
  --filter-expression "contains(PK, :uid)" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}'

# 4. Verify old basic-ops user storage data was migrated (should show migrated entries)
aws dynamodb scan --table-name amplify-v6-lambda-dev-user-data-storage \
  --filter-expression "contains(PK, :uid)" \
  --expression-attribute-values '{":uid":{"S":"NEW_USER_ID"}}' \
  --projection-expression "PK,SK,entityType"
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
- **Automatic Cleanup**: Deletes old records/files immediately after successful migration (no manual cleanup needed)
- **Backup Integration**: Built-in backup verification before destructive operations
- **Size Monitoring**: Warns about DynamoDB 400KB item limit approaches
- **Data Safety**: Verifies successful copy/creation before deleting original data

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

---

## 🎯 **QUICK REFERENCE - WHAT DO I ACTUALLY NEED TO DO?**

### 🆕 **FRESH DEPLOYMENT (No existing data)**
```bash
# Steps 1 & 2 only - no migration needed!
1. Update scripts/config.py (DEP_NAME and STAGE)
2. Add LOG_LEVEL to /var/{stage}-var.yml files  
3. Run: python3 scripts/populate_parameter_store.py --stage dev --dep-name v6
4. Deploy: serverless amplify-lambda:deploy --stage dev
5. Run: cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..
# You're done! 🎉
```

### 🚨 **EXISTING DEPLOYMENT (Has data to migrate)**
1. **Update `scripts/config.py`** with your `DEP_NAME` and `STAGE`
2. **Validate config**: `cd scripts && python3 -c "from config import get_config; config = get_config(); print('✓ Bucket:', config['S3_CONSOLIDATION_BUCKET_NAME'])"`
3. **Add `LOG_LEVEL` to your `/var/{stage}-var.yml`** files  
4. **Run**: `python3 scripts/populate_parameter_store.py --stage dev --dep-name v6`

### **DO I HAVE BASIC OPS SERVICE?**
```bash
# Check if this returns a stack:
aws cloudformation describe-stacks --stack-name amplify-{DEP_NAME}-lambda-basic-ops-{STAGE}
```
- **YES**: Follow [Step 3a](#step-3a-if-you-have-basic-ops-service)
- **NO**: Follow [Step 3b](#step-3b-if-you-dont-have-basic-ops-service)

### **DO I NEED ID MIGRATION OR JUST CONSOLIDATION?**
- **ID Migration** (Email → Username): Follow [Step 4a](#step-4a-if-you-need-user-id-migration)  
- **S3 Consolidation Only** (Highly Recommended): Follow [Step 4b](#step-4b-if-you-only-need-s3-consolidation-highly-recommended)

### ⚡ **QUICK MIGRATION COMMANDS**

**For ID Migration (Step 4a):**
```bash
# ALWAYS START WITH DRY RUN
python3 scripts/id_migration.py --dry-run --csv-file migration_users.csv

# STANDARD MIGRATION (auto-backup handling)
python3 scripts/id_migration.py --csv-file migration_users.csv --log migration.log
```

**For S3 Consolidation Only (Step 4b):**
```bash
# ALWAYS START WITH DRY RUN (consolidation only)
python3 scripts/id_migration.py --no-id-change --dry-run

# STANDARD CONSOLIDATION (auto-backup handling)
python3 scripts/id_migration.py --no-id-change --log consolidation.log
```

**Common Flags for Both:**
```bash
# SKIP BACKUP VERIFICATION (if you already have backups)
--dont-backup

# NO PROMPTS (for automation)  
--no-confirmation

# DIFFERENT REGION
--region us-west-2
```

### 🎯 **CRITICAL REMINDERS**
- 🥇 **Deploy amplify-lambda FIRST**: It's the base dependency all other services need
- 🚨 **After deploying amplify-lambda**: Run `cd amplify-lambda/markitdown && ./markitdown.sh && cd ../..`
- 🔒 **Backups are critical**: Migration deletes old data after copying
- 🏗️ **Future cleanup**: S3 consolidation prepares for eventual bucket deletion
- 🧹 **Basic-ops cleanup**: Remove basic-ops service AFTER migration completes (Step 5)
- ⚡ **Scripts are idempotent**: Safe to re-run if needed

### 🆘 **COMMON ISSUES**
- **"Parameter not found"** → Re-run populate_parameter_store.py  
- **"S3 bucket doesn't exist"** → Deploy amplify-lambda first
- **"API Gateway conflict"** → Remove basic-ops service first
- **"Permission denied"** → Check AWS credentials and IAM permissions