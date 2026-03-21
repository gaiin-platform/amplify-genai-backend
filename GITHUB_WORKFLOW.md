# GitHub Workflow - Model Alias Feature

## Current Status

✅ **Feature branch created**: `feature/model-alias-support`
✅ **All changes committed**: 5 commits total
✅ **Tests passing**: All unit and manual tests passing
✅ **Ready to push**: Branch ready for remote push

## Step-by-Step Workflow

### Step 1: Push Feature Branch to GitHub

```bash
# From: /Users/mick/Developer/amplifyAI/amplify-genai-backend

# Push the feature branch to origin
git push -u origin feature/model-alias-support
```

**Expected output:**
```
Enumerating objects: X, done.
Counting objects: 100% (X/X), done.
...
To github.com:gaiin-platform/amplify-genai-backend.git
 * [new branch]      feature/model-alias-support -> feature/model-alias-support
Branch 'feature/model-alias-support' set up to track remote branch 'feature/model-alias-support' from 'origin'.
```

### Step 2: Create GitHub Issues

Navigate to: https://github.com/gaiin-platform/amplify-genai-backend/issues/new

#### Issue #1: Model Alias Support

1. Click **"New Issue"**
2. **Title:** `Add Model Alias Support for Auto-Updating to Latest Versions`
3. **Body:** Copy content from `.github-issues/ISSUE_283_Model_Alias_Support.md`
4. **Labels:** Add `enhancement`, `models`, `backend`, `high-priority`
5. **Milestone:** (if applicable)
6. Click **"Submit new issue"**
7. **Note the issue number** (e.g., #123)

#### Issue #2: Alias Management APIs

1. Click **"New Issue"**
2. **Title:** `Add Admin API Endpoints for Model Alias Management`
3. **Body:** Copy content from `.github-issues/ISSUE_284_Alias_Management_APIs.md`
4. **Labels:** Add `enhancement`, `api`, `backend`
5. **Milestone:** (if applicable)
6. Click **"Submit new issue"**
7. **Note the issue number** (e.g., #124)

### Step 3: Create Pull Request

Navigate to: https://github.com/gaiin-platform/amplify-genai-backend/compare

1. **Base branch:** `main`
2. **Compare branch:** `feature/model-alias-support`
3. Click **"Create pull request"**
4. **Title:** `Model Alias Support for Auto-Updating to Latest Versions`
5. **Body:** Copy content from `.github-issues/PR_DESCRIPTION.md`
   - **Important:** Update the "Closes" line with actual issue numbers:
     ```
     Closes: #123, #124
     ```
6. **Reviewers:** Add relevant team members
7. **Labels:** Add `enhancement`, `backend`
8. Click **"Create pull request"**

### Step 4: Link Issues to PR

If you didn't use "Closes #XXX" in the PR description:

1. In the PR sidebar, under "Development"
2. Click "Link an issue from this repository"
3. Select Issue #123 and #124
4. Issues will auto-close when PR is merged

### Step 5: Request Reviews

1. In the PR sidebar, under "Reviewers"
2. Request review from:
   - Backend team members
   - Security team (if required)
   - Architecture team (if required)

### Step 6: CI/CD Checks

Wait for automated checks to complete:
- [ ] Tests pass
- [ ] Linting passes
- [ ] Build succeeds
- [ ] Security scan passes (if configured)

### Step 7: Address Review Feedback

If reviewers request changes:
```bash
# Make changes locally
git add <files>
git commit -m "address review feedback: <description>"
git push origin feature/model-alias-support
```

The PR will automatically update with new commits.

### Step 8: Deploy to Dev for Testing

Once PR is approved but before merging:

```bash
# Deploy to dev environment from feature branch
cd /Users/mick/Developer/amplifyAI/amplify-genai-backend
git checkout feature/model-alias-support

# Deploy Lambda functions
serverless amplify-lambda-js:deploy --stage dev

# Deploy Python services
serverless chat-billing:deploy --stage dev
```

**Test in dev:**
```bash
# Test alias endpoint
curl -H "Authorization: Bearer $DEV_TOKEN" \
  https://dev-api.amplify/model_aliases | jq '.'

# Test chat with alias
curl -X POST https://dev-api.amplify/chat \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": {"id": "opus-latest"},
    "messages": [{"role": "user", "content": "test"}]
  }' | jq '.'

# Monitor logs
aws logs tail /aws/lambda/amplify-lambda-js-dev-chat --follow | grep "alias"
```

### Step 9: Merge PR

Once approved and tested:

1. Click **"Squash and merge"** or **"Merge pull request"** (per team convention)
2. Confirm merge
3. Delete feature branch (GitHub will prompt)
4. Issues #123 and #124 will auto-close

### Step 10: Deploy to Staging & Production

After PR is merged to main:

```bash
# Checkout main and pull latest
git checkout main
git pull origin main

# Deploy to staging
serverless amplify-lambda-js:deploy --stage staging
serverless chat-billing:deploy --stage staging

# Test in staging...

# Deploy to production
serverless amplify-lambda-js:deploy --stage prod
serverless chat-billing:deploy --stage prod
```

## Commit History

```
5a93feaf chore: Update package-lock.json
ac046613 docs: Add comprehensive documentation for model alias feature
94b39ab7 test: Add comprehensive test suite for model alias resolution
b0efffb3 feat: Add model alias management API endpoints
23dd4992 feat: Add model alias resolution system
```

## Files Changed

### Created (8 files):
- `chat-billing/model_rates/model_aliases.json`
- `amplify-lambda-js/models/modelAliases.js`
- `amplify-lambda-js/models/__tests__/modelAliases.test.js`
- `amplify-lambda-js/models/__tests__/manual-test-aliases.js`
- `docs/MODEL_ALIASES.md`
- `MODEL_ALIAS_CHANGELOG.md`
- `MODEL_ALIAS_QUICKSTART.md`
- `IMPLEMENTATION_SUMMARY.md`

### Modified (2 files):
- `amplify-lambda-js/router.js` (+13 lines)
- `chat-billing/service/core.py` (+133 lines)

## Quick Reference

### Issue Templates
- `.github-issues/ISSUE_283_Model_Alias_Support.md`
- `.github-issues/ISSUE_284_Alias_Management_APIs.md`

### PR Template
- `.github-issues/PR_DESCRIPTION.md`

### Documentation
- `docs/MODEL_ALIASES.md` - Comprehensive technical docs
- `MODEL_ALIAS_QUICKSTART.md` - Quick start guide
- `MODEL_ALIAS_CHANGELOG.md` - Changelog
- `IMPLEMENTATION_SUMMARY.md` - Implementation summary

### Testing
```bash
# Run manual tests
cd amplify-lambda-js
node models/__tests__/manual-test-aliases.js

# Run Jest tests (if Jest is installed)
npm test models/__tests__/modelAliases.test.js
```

## Rollback Plan

If issues arise in production:

**Option 1: Quick Fix (5 minutes)**
```javascript
// In router.js line 231:
const modelId = rawModelId;  // Bypass alias resolution
```
Redeploy.

**Option 2: Full Revert**
```bash
git revert <merge-commit-hash>
git push origin main
serverless amplify-lambda-js:deploy --stage prod
```

## Support

For questions:
- Review `docs/MODEL_ALIASES.md`
- Check test output: `node models/__tests__/manual-test-aliases.js`
- Review commits: `git log --oneline feature/model-alias-support`

---

**Status:** ✅ Ready to push and create PR
**Branch:** `feature/model-alias-support`
**Commits:** 5 commits
**Tests:** All passing
