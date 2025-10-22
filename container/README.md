# Amplify Chat Fargate Container Deployment

This directory contains everything needed to deploy the Amplify Chat service as a containerized application on AWS Fargate, eliminating Lambda cold starts while maintaining all existing functionality.

## 🏗️ Architecture

```
┌─────────────────┐
│   Frontend      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ALB (HTTPS)    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│  ECS Fargate Service            │
│  ┌─────────────────────────┐   │
│  │  Express Server         │   │
│  │  (container/server.js)  │   │
│  │                         │   │
│  │  ├─ router.js           │   │
│  │  ├─ assistants/         │   │
│  │  ├─ common/llm.js       │   │
│  │  └─ datasource/         │   │
│  └─────────────────────────┘   │
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│  AWS Services                   │
│  ├─ S3 (Files)                  │
│  ├─ DynamoDB (State/Usage)      │
│  ├─ Secrets Manager             │
│  └─ SQS (Assistant Queue)       │
└─────────────────────────────────┘
```

## 📁 Directory Structure

```
container/
├── server.js                 # Production Express server
├── Dockerfile               # Container definition
├── .dockerignore           # Docker build exclusions
├── README.md               # This file
├── scripts/
│   ├── build-and-push.sh  # Build and push to ECR
│   ├── deploy.sh          # Deploy to ECS
│   ├── test-local.sh      # Local Docker testing
│   └── logs.sh            # View CloudWatch logs
└── terraform/
    ├── main.tf            # Main Terraform config
    ├── variables.tf       # Input variables
    ├── outputs.tf         # Output values
    ├── ecs.tf            # ECS cluster, service, tasks
    ├── alb.tf            # Application Load Balancer
    ├── ecr.tf            # Container registry
    └── terraform.tfvars.example
```

## 🚀 Quick Start

### Prerequisites

- AWS CLI configured with appropriate credentials
- Docker installed and running
- Terraform >= 1.0
- Access to your existing var.yml configuration files

### Step 1: Local Testing

Test the container locally before deploying:

```bash
# From project root
cd container/scripts
./test-local.sh dev 8080
```

Visit `http://localhost:8080/health` to verify it's running.

### Step 2: Build and Push to ECR

```bash
cd container/scripts
./build-and-push.sh dev v1.0.0
```

This will:
- Create ECR repository if it doesn't exist
- Build the Docker image
- Push to ECR with version tag and 'latest'

### Step 3: Deploy Infrastructure with Terraform

```bash
cd container/terraform

# Copy and configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Initialize Terraform
terraform init

# Review changes
terraform plan

# Deploy
terraform apply
```

### Step 4: Update Frontend

After deployment, update your frontend to point to the new ALB endpoint:

```javascript
// Get the ALB DNS from Terraform output
terraform output service_endpoint

// Update your frontend config
const API_ENDPOINT = "http://<alb-dns>/chat";
```

## 🔧 Configuration

### Environment Variables

All environment variables from `amplify-lambda-js/serverless.yml` are replicated in the ECS task definition. Key variables:

| Variable | Description | Required |
|----------|-------------|----------|
| `PORT` | Container listen port | Yes (8080) |
| `NODE_ENV` | Environment mode | Yes (production) |
| `COGNITO_USER_POOL_ID` | Cognito User Pool | Yes |
| `COGNITO_CLIENT_ID` | Cognito Client | Yes |
| `ALLOWED_ORIGINS` | CORS origins | Yes |
| `S3_FILE_TEXT_BUCKET_NAME` | File storage bucket | Yes |
| `HASH_FILES_DYNAMO_TABLE` | File metadata table | Yes |

See `terraform/ecs.tf` for the complete list.

### Terraform Variables

Key variables to configure in `terraform.tfvars`:

```hcl
# Network
vpc_id             = "vpc-xxxxx"
private_subnet_ids = ["subnet-xxxxx", "subnet-yyyyy"]
public_subnet_ids  = ["subnet-aaaaa", "subnet-bbbbb"]

# Scaling
desired_count = 2
min_capacity  = 2
max_capacity  = 10

# Resources
task_cpu    = "1024"  # 1 vCPU
task_memory = "2048"  # 2GB
```

## 📊 Monitoring

### View Logs

```bash
# Recent logs
cd container/scripts
./logs.sh dev

# Follow logs in real-time
./logs.sh dev follow
```

### CloudWatch Metrics

Monitor in AWS Console:
- ECS Service → Metrics
- Application Load Balancer → Monitoring
- Target Group → Health Checks

Key metrics:
- `CPUUtilization`
- `MemoryUtilization`
- `TargetResponseTime`
- `HealthyHostCount`

### Health Checks

- Container: `http://localhost:8080/health`
- ALB: `http://<alb-dns>/health`

## 🔄 Deployment Workflow

### Updating the Service

After code changes:

```bash
# 1. Build and push new image
cd container/scripts
./build-and-push.sh dev v1.0.1

# 2. Deploy to ECS
./deploy.sh dev v1.0.1
```

The deployment process:
1. Pushes new image to ECR
2. Forces ECS service update
3. ECS launches new tasks with new image
4. Waits for health checks to pass
5. Drains connections from old tasks
6. Terminates old tasks

### Rollback

To rollback to a previous version:

```bash
# Deploy specific image tag
cd container/scripts
./deploy.sh dev v1.0.0
```

Or via AWS Console:
1. ECS → Clusters → Service
2. Update Service → Force New Deployment
3. Select previous task definition revision

## 🔐 Security

### IAM Roles

Two roles are created:

1. **Task Execution Role**: For ECS to pull images and write logs
2. **Task Role**: Application permissions (same as Lambda)
   - Reuses existing Lambda IAM policies
   - Access to S3, DynamoDB, Secrets Manager, SQS

### Network Security

- ECS tasks in private subnets (no public IP)
- ALB in public subnets
- Security groups restrict traffic:
  - ALB → Port 443/80 from internet
  - ECS → Port 8080 from ALB only
  - ECS → Outbound to AWS services

### Secrets Management

Secrets can be injected via:
1. Environment variables in task definition
2. AWS Secrets Manager (recommended)
3. Parameter Store

Example in `ecs.tf`:
```hcl
secrets = [
  {
    name      = "LLM_API_KEY"
    valueFrom = "arn:aws:secretsmanager:region:account:secret:name"
  }
]
```

## 💰 Cost Estimation

### Fargate Costs (us-east-1)

With default configuration (2 tasks, 1 vCPU, 2GB RAM):

```
Monthly Cost Breakdown:
- vCPU:    $0.04048/hour × 1 × 2 tasks × 730 hours = $59.10
- Memory:  $0.004445/GB/hour × 2GB × 2 tasks × 730 hours = $12.97
- ALB:     $0.0225/hour × 730 hours = $16.43
- Data:    ~$10-50/month depending on traffic

Total: ~$100-150/month base + scaling
```

Auto-scaling will add costs during peak usage but only when needed.

### Cost vs Lambda

| Scenario | Lambda (Current) | Fargate (New) | Winner |
|----------|------------------|---------------|---------|
| Low traffic (<100k req/mo) | $50-100 | $100-150 | Lambda |
| Medium traffic (100k-1M req/mo) | $500-800 | $100-200 | Fargate |
| High traffic (>1M req/mo) | $800+ | $150-300 | Fargate |
| Need low latency | ❌ Cold starts | ✅ Always warm | Fargate |

## 🧪 Testing

### Local Docker Test

```bash
# Test with curl
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "options": {
      "model": {"id": "gpt-4"}
    }
  }'
```

### Load Testing

```bash
# Install hey (HTTP load generator)
brew install hey  # or apt-get install hey

# Test with 100 requests, 10 concurrent
hey -n 100 -c 10 \
  -H "Authorization: Bearer $TOKEN" \
  -m POST \
  -d '{"messages":[{"role":"user","content":"test"}]}' \
  http://<alb-dns>/chat
```

## 🐛 Troubleshooting

### Container Won't Start

Check logs:
```bash
cd container/scripts
./logs.sh dev
```

Common issues:
- Missing environment variables
- IAM permission errors
- Network connectivity issues

### 503 Errors from ALB

Possible causes:
1. No healthy targets
   - Check: ALB → Target Groups → Targets
   - Fix: Verify health check endpoint returns 200

2. Tasks failing health checks
   - Check: ECS → Service → Events
   - Fix: Review container logs

3. Security group misconfiguration
   - Check: ECS tasks can reach ALB
   - Fix: Verify security group rules

### High Memory/CPU Usage

Scale up:
```hcl
# In terraform.tfvars
task_cpu    = "2048"  # 2 vCPU
task_memory = "4096"  # 4GB
```

Then apply:
```bash
terraform apply
```

## 🔄 Migration from Lambda

### Hybrid Approach (Recommended)

Keep both deployments during migration:

1. Deploy Fargate alongside Lambda
2. Update frontend to use Fargate for chat
3. Monitor for 1-2 weeks
4. If successful, decommission Lambda chat
5. Keep Lambda for queue/billing functions

### Frontend Update

```javascript
// Before (Lambda)
const CHAT_ENDPOINT = "https://xxx.lambda-url.us-east-1.on.aws";

// After (Fargate)
const CHAT_ENDPOINT = "http://<alb-dns>/chat";
// Or with custom domain
const CHAT_ENDPOINT = "https://chat-api.yourdomain.com/chat";
```

### Feature Parity

✅ All features work identically:
- Assistants (default, mapReduce, agent, codeInterpreter, etc.)
- RAG integration
- Data sources (S3, tags, external)
- All LLM providers (OpenAI, Azure, Bedrock, Gemini)
- Streaming responses
- Billing & usage tracking
- Rate limiting
- Authentication (Cognito, API keys)

❌ No X-Ray tracing (in this PoC - can be added later)

## 📝 Next Steps

### Production Readiness

1. **Add HTTPS**
   - Request ACM certificate
   - Set `alb_certificate_arn` in terraform.tfvars
   - Apply changes

2. **Custom Domain**
   - Create Route53 A record → ALB
   - Update CORS origins

3. **Monitoring & Alerts**
   - Set up CloudWatch alarms
   - Configure SNS notifications
   - Add application monitoring (DataDog, New Relic, etc.)

4. **CI/CD Pipeline**
   - Automate build-and-push on git push
   - Run tests before deployment
   - Blue/green deployments

5. **Add X-Ray Tracing**
   - Add X-Ray daemon sidecar container
   - Update task definition
   - Enable in application code

## 📚 Additional Resources

- [AWS Fargate Documentation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate.html)
- [ECS Task Definitions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definitions.html)
- [Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/introduction.html)
- [Express.js Server-Sent Events](https://expressjs.com/)

## 🤝 Support

For issues or questions:
1. Check CloudWatch logs: `./scripts/logs.sh dev`
2. Review ECS service events in AWS Console
3. Verify terraform state: `terraform show`
4. Contact the platform team
