#!/bin/bash
# Calculate required Fargate capacity based on traffic patterns

echo "=== Amplify Chat Capacity Calculator ==="
echo ""

# Get inputs
read -p "Daily active users: " DAILY_USERS
read -p "Average requests per user per day: " REQUESTS_PER_USER
read -p "Average request duration (seconds): " AVG_DURATION
read -p "Active hours per day: " ACTIVE_HOURS

# Calculate
DAILY_REQUESTS=$((DAILY_USERS * REQUESTS_PER_USER))
HOURLY_REQUESTS=$((DAILY_REQUESTS / ACTIVE_HOURS))
MINUTE_REQUESTS=$((HOURLY_REQUESTS / 60))

# Peak with 2x buffer for spikes
PEAK_CONCURRENT=$(echo "scale=2; ($MINUTE_REQUESTS * $AVG_DURATION / 60) * 2" | bc)

echo ""
echo "=== Results ==="
echo "Daily requests: $DAILY_REQUESTS"
echo "Peak hourly: $HOURLY_REQUESTS requests/hour"
echo "Peak per minute: $MINUTE_REQUESTS requests/min"
echo "Estimated peak concurrent: $PEAK_CONCURRENT"
echo ""

# Recommendations
if (( $(echo "$PEAK_CONCURRENT < 50" | bc -l) )); then
    echo "✅ Recommendation: 1 task (0.5 vCPU, 1GB RAM)"
    echo "   Capacity: ~100 concurrent"
    echo "   Cost: ~\$18/month"
elif (( $(echo "$PEAK_CONCURRENT < 100" | bc -l) )); then
    echo "✅ Recommendation: 1 task (1 vCPU, 2GB RAM)"
    echo "   Capacity: ~200 concurrent"
    echo "   Cost: ~\$36/month"
elif (( $(echo "$PEAK_CONCURRENT < 200" | bc -l) )); then
    echo "✅ Recommendation: 2 tasks (1 vCPU, 2GB RAM)"
    echo "   Capacity: ~400 concurrent"
    echo "   Cost: ~\$72/month"
else
    TASKS=$(echo "scale=0; ($PEAK_CONCURRENT / 100) + 1" | bc)
    COST=$(echo "scale=0; $TASKS * 36" | bc)
    echo "✅ Recommendation: $TASKS tasks (1 vCPU, 2GB RAM)"
    echo "   Capacity: ~$((TASKS * 200)) concurrent"
    echo "   Cost: ~\$${COST}/month"
fi

echo ""
echo "Auto-scaling will handle burst traffic beyond these numbers."
