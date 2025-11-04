# 🚨 AWS Backup & Restore Guide

## **IMMEDIATE: Restore Lost Table**

### **Option 1: Point-in-Time Recovery (If Enabled)**
```bash
# Check if PITR was enabled (look for recent backup)
aws dynamodb describe-continuous-backups --table-name YOUR-LOST-TABLE-NAME

# If PITR was enabled, restore to a specific time (before deletion)
aws dynamodb restore-table-to-point-in-time \
  --source-table-name YOUR-LOST-TABLE-NAME \
  --target-table-name YOUR-LOST-TABLE-NAME-restored \
  --restore-date-time 2024-10-22T10:00:00.000Z  # CHANGE TO BEFORE DELETION
```

### **Option 2: From On-Demand Backup**
```bash
# List available backups
aws dynamodb list-backups --table-name YOUR-LOST-TABLE-NAME

# Restore from specific backup
aws dynamodb restore-table-from-backup \
  --target-table-name YOUR-LOST-TABLE-NAME-restored \
  --backup-arn "BACKUP-ARN-FROM-LIST-COMMAND"

# Rename restored table back to original
aws dynamodb restore-table-from-backup \
  --target-table-name YOUR-ORIGINAL-TABLE-NAME \
  --backup-arn "BACKUP-ARN"
```

### **Option 3: AWS Console (Easier)**
1. Go to **DynamoDB Console** → **Backups**
2. Find your table in **Point-in-time recovery** or **On-demand backups**
3. Click **Restore** → Choose restore time/backup
4. Create new table with original name

---

## **📋 NEW BACKUP SYSTEM FOR MIGRATION**

### **Step 1: Create Backups (BEFORE Migration)**
```bash
# Create comprehensive backups
python backup_prereq.py --backup-name "pre-migration-$(date +%Y%m%d-%H%M%S)"

# Verify backups were created successfully
python backup_prereq.py --verify-only --backup-name "pre-migration-20241022"

# Dry run to see what would be backed up
python backup_prereq.py --dry-run
```

### **Step 2: Run Migration (Backup-Protected)**
```bash
# Safe migration (checks for backups first)
python id_migration.py --dry-run

# Production migration (with backup verification)
python id_migration.py

# Skip backup check (NOT RECOMMENDED)
python id_migration.py --skip-backup-check
```

---

## **🔧 Backup Types Created**

### **DynamoDB Protection:**
1. **Point-in-Time Recovery (PITR)**: Continuous backups, restore to any second
2. **On-Demand Backups**: Named snapshots before migration
3. **Automatic verification**: Script checks backups exist before migration

### **S3 Protection:**
1. **Versioning Enabled**: Keeps multiple versions of each object
2. **Cross-Region Replication** (optional): Copies to backup region
3. **Pre-migration snapshots**: Manual backup creation

---

## **⚡ Quick Recovery Commands**

### **List Recent Backups:**
```bash
# DynamoDB backups from last 7 days
aws dynamodb list-backups \
  --time-range-lower-bound $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S.000Z) \
  --time-range-upper-bound $(date -u +%Y-%m-%dT%H:%M:%S.000Z)

# Check PITR status for all tables
for table in $(aws dynamodb list-tables --output text --query 'TableNames[]'); do
  echo "Table: $table"
  aws dynamodb describe-continuous-backups --table-name $table --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' --output text
done
```

### **Batch Restore (If Needed):**
```bash
# Restore multiple tables from backups
cat << 'EOF' > restore_tables.sh
#!/bin/bash
BACKUP_PREFIX="pre-migration-20241022"

for backup in $(aws dynamodb list-backups --query 'BackupSummaries[?contains(BackupName, `'$BACKUP_PREFIX'`)].BackupArn' --output text); do
  table_name=$(aws dynamodb describe-backup --backup-arn $backup --query 'BackupDescription.SourceTableDetails.TableName' --output text)
  echo "Restoring $table_name from $backup"
  
  aws dynamodb restore-table-from-backup \
    --target-table-name "${table_name}-restored" \
    --backup-arn "$backup"
done
EOF

chmod +x restore_tables.sh
./restore_tables.sh
```

---

## **🎯 Production Migration Workflow**

### **Safe Production Process:**
```bash
# 1. Create backups first
python backup_prereq.py --backup-name "production-migration-$(date +%Y%m%d-%H%M%S)"

# 2. Verify backups
python backup_prereq.py --verify-only --backup-name "production-migration-20241022"

# 3. Test with dry run
python id_migration.py --dry-run

# 4. Run actual migration (backup-protected)
python id_migration.py

# 5. Verify migration success
python id_migration.py --dry-run  # Should show no old data

# 6. Test application functionality

# 7. Clean up old backups (after verification period)
# aws dynamodb delete-backup --backup-arn "OLD-BACKUP-ARN"
```


## **⚠️ Important Notes**

1. **PITR has 35-day limit** - older than 35 days cannot be recovered
2. **Backup costs money** - but much less than data loss
3. **Restore creates NEW table** - you'll need to rename/swap tables
4. **S3 versioning** protects against accidental deletes/overwrites
5. **Test restores periodically** to ensure backup system works

**🚀 The new backup system makes migration much safer!**