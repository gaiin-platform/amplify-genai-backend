# Fargate Deployment Quick Start

## One-Command Test

```bash
# From project root
cd container/scripts && ./test-local.sh dev 8080
```

Visit http://localhost:8080/health

## Deploy to AWS

### 1️⃣ Build & Push (5 minutes)

```bash
cd container/scripts
./build-and-push.sh dev v1.0.0
```

### 2️⃣ Configure Terraform (5 minutes)

```bash
cd ../terraform
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars with your values:
# - VPC ID and subnet IDs
# - Cognito pool/client IDs
# - Deployment name from var.yml
```

### 3️⃣ Deploy Infrastructure (10 minutes)

```bash
terraform init
terraform apply
```

### 4️⃣ Get Endpoint

```bash
terraform output service_endpoint
# Example: http://amplify-chat-alb-xxxxx.us-east-1.elb.amazonaws.com/chat
```

### 5️⃣ Test

```bash
curl -X POST <service_endpoint> \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "options": {"model": {"id": "gpt-4"}}
  }'
```

### 6️⃣ Update Frontend

Change your frontend API endpoint to the ALB DNS from step 4.

## View Logs

```bash
cd container/scripts
./logs.sh dev follow
```

## Update Service

```bash
# After code changes
cd container/scripts
./build-and-push.sh dev v1.0.1
./deploy.sh dev v1.0.1
```

## Troubleshooting

### Container won't start?
```bash
./logs.sh dev
```

### ALB returns 503?
Check: AWS Console → ECS → Service → Events

### Need to rollback?
```bash
./deploy.sh dev v1.0.0  # Previous version
```

## Cost

- Base: ~$100-150/month (2 tasks running 24/7)
- Scales: $50-100/month per 10 additional concurrent requests

## Next Steps

- [ ] Add HTTPS certificate
- [ ] Set up custom domain
- [ ] Configure CloudWatch alarms
- [ ] Set up CI/CD pipeline
- [ ] Add X-Ray tracing

## Key Differences from Lambda

| Feature | Lambda | Fargate |
|---------|--------|---------|
| Cold start | 2-5 seconds | 0 seconds ✅ |
| Response time | 100-200ms | 50-100ms ✅ |
| Max timeout | 15 minutes | Unlimited ✅ |
| Cost model | Per invocation | Per hour |
| Scaling | Automatic | Auto-scaling |

## Support

- Logs: `./scripts/logs.sh dev`
- Metrics: AWS Console → ECS → Service
- Docs: See README.md
