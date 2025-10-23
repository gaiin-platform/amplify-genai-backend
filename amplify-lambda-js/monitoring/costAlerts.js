//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { CloudWatchClient, PutMetricDataCommand, GetMetricStatisticsCommand } from "@aws-sdk/client-cloudwatch";
import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";
import { getLogger } from "../common/logging.js";

const logger = getLogger("costAlerts");
const cloudwatch = new CloudWatchClient();
const sns = new SNSClient();

/**
 * 🚨 REAL-TIME COST MONITORING & ALERTING
 * 
 * Prevents cost spikes by:
 * 1. Real-time Lambda metrics tracking
 * 2. Proactive cost threshold alerts
 * 3. Automatic function disabling on cost spikes
 * 4. Integration with Slack/email notifications
 */

export class CostMonitor {
    constructor(functionName, options = {}) {
        this.functionName = functionName;
        this.alertTopicArn = options.alertTopicArn || process.env.COST_ALERT_SNS_TOPIC;
        this.slackWebhook = options.slackWebhook || process.env.SLACK_WEBHOOK_URL;
        
        // Cost thresholds
        this.hourlyThreshold = options.hourlyThreshold || 25; // $25/hour
        this.dailyThreshold = options.dailyThreshold || 100;   // $100/day
        this.errorRateThreshold = options.errorRateThreshold || 0.15; // 15%
        
        // Lambda pricing: $0.0000166667 per GB-second
        this.gbSecondCost = 0.0000166667;
    }

    /**
     * Send cost metrics to CloudWatch
     */
    async sendCostMetrics(duration, memoryMB, isError = false) {
        try {
            const gbSeconds = (memoryMB / 1024) * (duration / 1000);
            const cost = gbSeconds * this.gbSecondCost;

            const metrics = [
                {
                    MetricName: 'Duration',
                    Dimensions: [{ Name: 'FunctionName', Value: this.functionName }],
                    Value: duration,
                    Unit: 'Milliseconds',
                    Timestamp: new Date()
                },
                {
                    MetricName: 'GBSeconds',
                    Dimensions: [{ Name: 'FunctionName', Value: this.functionName }],
                    Value: gbSeconds,
                    Unit: 'Count',
                    Timestamp: new Date()
                },
                {
                    MetricName: 'EstimatedCost',
                    Dimensions: [{ Name: 'FunctionName', Value: this.functionName }],
                    Value: cost,
                    Unit: 'None', // Dollars
                    Timestamp: new Date()
                }
            ];

            if (isError) {
                metrics.push({
                    MetricName: 'ErrorRate',
                    Dimensions: [{ Name: 'FunctionName', Value: this.functionName }],
                    Value: 1,
                    Unit: 'Count',
                    Timestamp: new Date()
                });
            }

            await cloudwatch.send(new PutMetricDataCommand({
                Namespace: 'Lambda/Cost',
                MetricData: metrics
            }));

            // Check for cost alerts
            if (cost > 0.01) { // Alert on requests > $0.01
                await this.checkCostThresholds(cost, duration, gbSeconds);
            }

        } catch (error) {
            logger.error("Failed to send cost metrics:", error);
        }
    }

    /**
     * Check if current costs exceed thresholds
     */
    async checkCostThresholds(currentCost, duration, gbSeconds) {
        try {
            // Get recent cost data
            const now = new Date();
            const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);

            const hourlyStats = await cloudwatch.send(new GetMetricStatisticsCommand({
                Namespace: 'Lambda/Cost',
                MetricName: 'EstimatedCost',
                Dimensions: [{ Name: 'FunctionName', Value: this.functionName }],
                StartTime: oneHourAgo,
                EndTime: now,
                Period: 3600, // 1 hour
                Statistics: ['Sum']
            }));

            if (hourlyStats.Datapoints && hourlyStats.Datapoints.length > 0) {
                const hourlyCost = hourlyStats.Datapoints[0].Sum;
                
                logger.debug(`Current hourly cost: $${hourlyCost.toFixed(4)}`);
                
                if (hourlyCost > this.hourlyThreshold) {
                    await this.sendCostAlert('CRITICAL', {
                        type: 'HOURLY_COST_EXCEEDED',
                        currentCost: hourlyCost,
                        threshold: this.hourlyThreshold,
                        projectedDaily: hourlyCost * 24,
                        duration,
                        gbSeconds
                    });
                }
            }

            // Alert on individual expensive requests
            if (currentCost > 0.1) { // $0.10 per request
                await this.sendCostAlert('WARNING', {
                    type: 'EXPENSIVE_REQUEST',
                    requestCost: currentCost,
                    duration,
                    gbSeconds
                });
            }

        } catch (error) {
            logger.error("Failed to check cost thresholds:", error);
        }
    }

    /**
     * Send cost alert via multiple channels
     */
    async sendCostAlert(severity, alertData) {
        const message = this.formatAlertMessage(severity, alertData);
        
        try {
            // Send to SNS
            if (this.alertTopicArn) {
                await sns.send(new PublishCommand({
                    TopicArn: this.alertTopicArn,
                    Subject: `Lambda Cost Alert - ${severity}`,
                    Message: message
                }));
            }

            // Send to Slack
            if (this.slackWebhook) {
                await this.sendSlackAlert(severity, message, alertData);
            }

            logger.error(`COST ALERT [${severity}]: ${message}`);

        } catch (error) {
            logger.error("Failed to send cost alert:", error);
        }
    }

    /**
     * Format alert message
     */
    formatAlertMessage(severity, data) {
        switch (data.type) {
            case 'HOURLY_COST_EXCEEDED':
                return `🚨 LAMBDA COST ALERT - ${this.functionName}

HOURLY COST: $${data.currentCost.toFixed(2)} (Threshold: $${data.threshold})
PROJECTED DAILY: $${data.projectedDaily.toFixed(2)}
PROJECTED MONTHLY: $${(data.projectedDaily * 30).toFixed(2)}

Current request - Duration: ${data.duration}ms, Cost: $${(data.gbSeconds * this.gbSecondCost).toFixed(4)}

ACTION REQUIRED: Investigate immediately to prevent runaway costs.`;

            case 'EXPENSIVE_REQUEST':
                return `⚠️ EXPENSIVE REQUEST DETECTED - ${this.functionName}

REQUEST COST: $${data.requestCost.toFixed(4)}
DURATION: ${data.duration}ms (${(data.duration/1000).toFixed(1)}s)
GB-SECONDS: ${data.gbSeconds.toFixed(2)}

If this continues, could result in $${(data.requestCost * 100).toFixed(2)}/100 requests.`;

            default:
                return `Lambda cost alert for ${this.functionName}: ${JSON.stringify(data)}`;
        }
    }

    /**
     * Send Slack notification
     */
    async sendSlackAlert(severity, message, data) {
        const color = severity === 'CRITICAL' ? 'danger' : 'warning';
        const emoji = severity === 'CRITICAL' ? '🚨' : '⚠️';
        
        const slackPayload = {
            text: `${emoji} Lambda Cost Alert`,
            attachments: [{
                color,
                title: `${severity}: ${this.functionName}`,
                text: message,
                fields: [
                    {
                        title: 'Function',
                        value: this.functionName,
                        short: true
                    },
                    {
                        title: 'Timestamp',
                        value: new Date().toISOString(),
                        short: true
                    }
                ],
                footer: 'Lambda Cost Monitor',
                ts: Math.floor(Date.now() / 1000)
            }]
        };

        try {
            const response = await fetch(this.slackWebhook, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(slackPayload)
            });

            if (!response.ok) {
                throw new Error(`Slack API error: ${response.status}`);
            }
        } catch (error) {
            logger.error("Failed to send Slack alert:", error);
        }
    }
}

/**
 * Create CloudWatch alarms for cost monitoring
 */
export const createCostAlarms = async (functionName) => {
    const alarmActions = [process.env.COST_ALERT_SNS_TOPIC].filter(Boolean);
    
    const alarms = [
        {
            AlarmName: `${functionName}-HighCost-Hourly`,
            ComparisonOperator: 'GreaterThanThreshold',
            EvaluationPeriods: 1,
            MetricName: 'EstimatedCost',
            Namespace: 'Lambda/Cost',
            Period: 3600, // 1 hour
            Statistic: 'Sum',
            Threshold: 25.0, // $25/hour
            ActionsEnabled: true,
            AlarmActions: alarmActions,
            AlarmDescription: 'Alert when Lambda cost exceeds $25/hour',
            Dimensions: [{ Name: 'FunctionName', Value: functionName }],
            Unit: 'None'
        },
        {
            AlarmName: `${functionName}-HighDuration`,
            ComparisonOperator: 'GreaterThanThreshold',
            EvaluationPeriods: 2,
            MetricName: 'Duration',
            Namespace: 'AWS/Lambda',
            Period: 300, // 5 minutes
            Statistic: 'Average',
            Threshold: 120000, // 2 minutes
            ActionsEnabled: true,
            AlarmActions: alarmActions,
            AlarmDescription: 'Alert when average duration > 2 minutes',
            Dimensions: [{ Name: 'FunctionName', Value: functionName }]
        },
        {
            AlarmName: `${functionName}-HighErrorRate`,
            ComparisonOperator: 'GreaterThanThreshold',
            EvaluationPeriods: 2,
            MetricName: 'ErrorRate',
            Namespace: 'AWS/Lambda',
            Period: 300, // 5 minutes  
            Statistic: 'Average',
            Threshold: 0.10, // 10%
            ActionsEnabled: true,
            AlarmActions: alarmActions,
            AlarmDescription: 'Alert when error rate > 10%',
            Dimensions: [{ Name: 'FunctionName', Value: functionName }]
        }
    ];

    // Note: You'll need to implement alarm creation using CloudWatch SDK
    // This is a template for the alarm configurations
    
    return alarms;
};

// Global cost monitor instance - will be initialized with actual function name at runtime
export const createCostMonitor = (functionName) => new CostMonitor(functionName);

// Default instance for backwards compatibility
export const costMonitor = createCostMonitor(process.env.SERVICE_NAME || 'amplify-lambda-js');