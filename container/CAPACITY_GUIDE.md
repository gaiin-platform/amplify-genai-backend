# Capacity Planning & Optimization Guide

## 📊 Your Traffic Profile: 1000 Users/Day

### Estimated Concurrent Load

```
Assumptions:
├─ 1000 daily active users
├─ 5 requests per user per day = 5,000 requests/day
├─ 8-hour active period (9am-5pm)
├─ Average request duration: 30 seconds (streaming)
└─ Peak traffic: 2x average (lunch hour spike)

Peak Concurrent Calculation:
= (requests/min × duration_sec / 60) × spike_factor
= (11 requests/min × 30s / 60) × 2
= 11 concurrent streams at peak
```

### Real-World Concurrent Streams

| Time | Requests/Min | Concurrent Streams |
|------|-------------|-------------------|
| Off-hours | 1-2 | 0-1 |
| Normal | 8-10 | 4-6 |
| **Peak** | **15-20** | **8-12** |
| Absolute spike | 30-40 | 15-25 |

---

## 🎯 Right-Sized Configuration

### **Recommended: Start Small, Scale as Needed**

```hcl
# container/terraform/terraform.tfvars

# Cost-optimized configuration
desired_count = 1      # Start with 1 task
min_capacity  = 1      # Minimum during low traffic
max_capacity  = 3      # Scale up for spikes
task_cpu      = "512"  # 0.5 vCPU (enough for I/O-bound)
task_memory   = "1024" # 1GB (enough for Node.js)
```

**Capacity**: 100-200 concurrent streams per task
**Your peak**: ~12 concurrent
**Overhead**: 8-16x capacity (plenty of headroom)
**Cost**: ~$18/month base

---

## 💰 Cost Optimization Strategies

### **Strategy 1: Single Task (Recommended)**

```hcl
desired_count = 1
min_capacity  = 1
max_capacity  = 3
task_cpu      = "512"
task_memory   = "1024"
```

**Pros:**
- ✅ Saves ~$54/month vs 2-task config
- ✅ Still handles 100+ concurrent
- ✅ Auto-scales to 3 tasks if needed

**Monthly Cost Breakdown:**
```
Base (1 task, 0.5 vCPU, 1GB):
├─ vCPU:    $0.04048 × 0.5 × 730h = $14.77
├─ Memory:  $0.004445 × 1GB × 730h = $3.24
├─ ALB:     $0.0225 × 730h = $16.43
└─ Total:   ~$35/month base
```

Auto-scaling adds:
- Task 2: $18/month when active
- Task 3: $18/month when active

### **Strategy 2: Fargate Spot (Best Value)**

```hcl
# In terraform/ecs.tf, modify capacity_providers:

capacity_providers = ["FARGATE_SPOT", "FARGATE"]

default_capacity_provider_strategy {
  capacity_provider = "FARGATE_SPOT"
  weight           = 100  # Use spot for 100% of tasks
  base             = 1    # Always keep 1 task
}
```

**Savings**: 70% off Fargate pricing

**Monthly Cost:**
```
1 Spot Task (0.5 vCPU, 1GB):
├─ vCPU:    $14.77 × 0.3 = $4.43
├─ Memory:  $3.24 × 0.3 = $0.97
├─ ALB:     $16.43
└─ Total:   ~$22/month
```

**Tradeoff**: Tasks can be interrupted (rare, auto-replaces in seconds)

### **Strategy 3: Hybrid Spot + On-Demand**

```hcl
default_capacity_provider_strategy {
  capacity_provider = "FARGATE"
  weight           = 1
  base             = 1  # 1 guaranteed on-demand task
}

default_capacity_provider_strategy {
  capacity_provider = "FARGATE_SPOT"
  weight           = 4  # Scale-up uses spot
  base             = 0
}
```

**Result**: Base task is reliable, burst capacity is cheap

---

## 🔬 Node.js Concurrency Explained

### **How It Works (Single-Threaded Event Loop)**

```javascript
// Your server.js handles requests like this:

app.post('/chat', async (req, res) => {
    // 1. Parse request (CPU: <1ms)
    const params = await extractParams(event);

    // 2. Call LLM (I/O: 2-30s) - Node.js is FREE during this wait
    //    Can handle 100s of other requests while waiting
    await routeRequest(params, returnResponse, sse);

    // 3. Stream response (I/O: 5-30s) - Also non-blocking
});
```

### **What Each Task Can Handle**

```
1 Fargate Task (0.5 vCPU, 1GB RAM):
├─ Open connections: ~5,000 (OS limit)
├─ Active requests: ~100-200 (memory limit)
├─ CPU-bound tasks: ~50 (CPU limit)
└─ I/O-bound (your case): ~100-200 ✅
```

Your requests are **I/O-bound** (waiting on LLMs), so you hit the high end.

### **Why You Don't Need More CPUs**

```
Your request breakdown:
├─ CPU time: ~50ms (parsing, token counting)
├─ I/O wait: 5-30 seconds (OpenAI, Bedrock, RAG)
└─ CPU usage: <1% per request

During the 30s I/O wait, Node.js can handle 100+ other requests!
```

---

## 📈 Scaling Triggers

Your auto-scaling configuration:

```hcl
# In terraform/ecs.tf

resource "aws_appautoscaling_policy" "ecs_cpu" {
  target_value = 70.0  # Scale when CPU hits 70%
  scale_in_cooldown  = 300  # Wait 5 min before scaling down
  scale_out_cooldown = 60   # Scale up quickly (1 min)
}

resource "aws_appautoscaling_policy" "ecs_memory" {
  target_value = 80.0  # Scale when memory hits 80%
}
```

### **When Scaling Happens**

| Metric | Threshold | Action |
|--------|-----------|--------|
| CPU > 70% | 1 minute | Add task |
| Memory > 80% | 1 minute | Add task |
| CPU < 50% | 5 minutes | Remove task |
| Memory < 60% | 5 minutes | Remove task |

**For your traffic**: You'll rarely scale beyond 1 task

---

## 🧪 Testing Your Capacity

### **Test 1: Baseline (Single User)**

```bash
curl -X POST http://<alb-dns>/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "options": {"model": {"id": "gpt-4o-mini"}}
  }'

# Expected: <2s response time
```

### **Test 2: Concurrent Load**

```bash
cd container/scripts
./load-test.sh http://<alb-dns>/chat $AUTH_TOKEN 10 60

# Tests 10 concurrent requests for 60 seconds
# Expected:
#   - Success rate: 100%
#   - p50 latency: <2s
#   - p95 latency: <5s
```

### **Test 3: Stress Test (Find Breaking Point)**

```bash
# Test increasing concurrent load
./load-test.sh <endpoint> $TOKEN 50 30   # 50 concurrent
./load-test.sh <endpoint> $TOKEN 100 30  # 100 concurrent
./load-test.sh <endpoint> $TOKEN 150 30  # 150 concurrent

# Watch CloudWatch metrics:
# - When CPU hits 70%, auto-scaling triggers
# - When response times spike, you've hit limit
```

### **Test 4: Calculate Your Needs**

```bash
./capacity-calculator.sh

# Enter your actual numbers:
# - Daily users: 1000
# - Requests per user: 5
# - Avg duration: 30
# - Active hours: 8
```

---

## 📊 Monitoring & Alerts

### **Key Metrics to Watch**

```bash
# CloudWatch metrics to monitor:
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ServiceName,Value=<service-name> \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-01T23:59:59Z \
  --period 300 \
  --statistics Average
```

### **Create Alarms**

```bash
# High CPU alarm
aws cloudwatch put-metric-alarm \
  --alarm-name amplify-chat-high-cpu \
  --alarm-description "Alert when CPU > 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/ECS \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold

# High memory alarm
aws cloudwatch put-metric-alarm \
  --alarm-name amplify-chat-high-memory \
  --metric-name MemoryUtilization \
  --threshold 85
```

---

## 🎯 Optimization Recommendations

### **For 1000 Users/Day**

1. **Start Configuration**
   ```hcl
   desired_count = 1
   task_cpu      = "512"
   task_memory   = "1024"
   ```

2. **Monitor for 1 Week**
   - Check CPU utilization (should be <30%)
   - Check memory utilization (should be <50%)
   - Check response times (should be <2s p95)

3. **If All Good**: Stay at 1 task, save $54/month

4. **If Seeing Issues**: Scale up
   ```hcl
   desired_count = 2  # or
   task_cpu      = "1024"  # or
   task_memory   = "2048"
   ```

---

## 📈 Growth Planning

| Daily Users | Peak Concurrent | Recommended Config | Monthly Cost |
|-------------|----------------|-------------------|--------------|
| 1,000 | ~12 | 1 task (0.5 vCPU, 1GB) | $35 |
| 5,000 | ~60 | 1 task (1 vCPU, 2GB) | $36 |
| 10,000 | ~120 | 2 tasks (1 vCPU, 2GB) | $72 |
| 50,000 | ~600 | 3-5 tasks (1 vCPU, 2GB) | $108-180 |

Auto-scaling handles spikes automatically.

---

## 🚨 When to Scale Up

**Scale up if you see:**
- ✅ CPU consistently > 60%
- ✅ Memory consistently > 70%
- ✅ Response times p95 > 5s
- ✅ Error rate > 0.1%
- ✅ Tasks restarting frequently

**You're fine if:**
- ✅ CPU < 50% average
- ✅ Memory < 60% average
- ✅ Response times p95 < 3s
- ✅ No errors
- ✅ Stable task count

---

## 💡 Pro Tips

1. **Use Spot for 70% Savings**
   - Interruptions are rare (<1% of time)
   - Tasks auto-replace in <30 seconds
   - Perfect for dev/staging

2. **Right-Size Resources**
   - Your workload is I/O-bound
   - More CPU ≠ better performance
   - More memory = more concurrent requests

3. **Monitor Real Usage**
   - Start small
   - Let CloudWatch show actual needs
   - Scale based on data, not assumptions

4. **Set Up Alarms**
   - Know when you need to scale
   - Catch issues before users do

---

## 🎉 Bottom Line

**For 1000 users/day:**
- ✅ **1 task** is more than enough
- ✅ Can handle **100+ concurrent** streams
- ✅ Your peak is only **~12 concurrent**
- ✅ Save **$54/month** vs 2-task config
- ✅ Auto-scales if traffic spikes

**Action Items:**
1. Deploy with 1 task configuration
2. Run load test: `./load-test.sh`
3. Monitor for 1 week
4. Optimize based on real data

You're going to be **way** under-utilized with the default 2-task config! 🚀
