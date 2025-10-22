// Copyright (c) 2024 Vanderbilt University
// Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import express from 'express';
import cors from 'cors';
import { routeRequest } from "../amplify-lambda-js/router.js";
import { extractParams } from "../amplify-lambda-js/common/handlers.js";
import { getLogger } from "../amplify-lambda-js/common/logging.js";

const logger = getLogger("container-server");
const app = express();
const port = process.env.PORT || 8080;

// Middleware
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ limit: '50mb', extended: true }));

// CORS configuration
const allowedOrigins = process.env.ALLOWED_ORIGINS?.split(',') || ['*'];
app.use(cors({
    origin: allowedOrigins,
    credentials: true,
    methods: ['GET', 'POST', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization', 'X-Request-ID']
}));

// Request logging middleware
app.use((req, res, next) => {
    const requestId = req.headers['x-request-id'] || `req-${Date.now()}-${Math.random().toString(36).substring(7)}`;
    req.requestId = requestId;
    logger.info(`[${requestId}] ${req.method} ${req.path}`);
    next();
});

// Health check endpoint
app.get('/health', (req, res) => {
    res.status(200).json({
        status: 'healthy',
        timestamp: new Date().toISOString(),
        uptime: process.uptime(),
        memory: process.memoryUsage()
    });
});

// Readiness check endpoint
app.get('/ready', async (req, res) => {
    // Basic readiness - can be enhanced with dependency checks
    try {
        res.status(200).json({
            status: 'ready',
            timestamp: new Date().toISOString()
        });
    } catch (error) {
        logger.error('Readiness check failed:', error);
        res.status(503).json({
            status: 'not_ready',
            error: error.message
        });
    }
});

// SSE Response Stream wrapper for Express
class SSEResponseStream {
    constructor(res) {
        this.res = res;
        this.initialized = false;
        this.writable = true;
        this.writableEnded = false;
    }

    write(data) {
        if (!this.initialized) {
            this.initialize();
        }
        if (this.writable && !this.writableEnded) {
            try {
                this.res.write(data);
            } catch (error) {
                logger.error('Error writing to stream:', error);
                this.writable = false;
            }
        }
    }

    initialize() {
        if (!this.initialized) {
            this.res.writeHead(200, {
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache, no-transform',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no', // Disable nginx buffering
                'Access-Control-Allow-Origin': '*'
            });
            this.initialized = true;
        }
    }

    end() {
        if (!this.writableEnded) {
            this.writableEnded = true;
            this.writable = false;
            try {
                this.res.end();
            } catch (error) {
                logger.error('Error ending stream:', error);
            }
        }
    }

    returnResponse(response) {
        if (!this.initialized) {
            this.res.writeHead(response.statusCode || 200, {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            });
            this.initialized = true;
        }
        if (!this.writableEnded) {
            try {
                this.res.write(JSON.stringify(response.body));
                this.end();
            } catch (error) {
                logger.error('Error returning response:', error);
                this.end();
            }
        }
    }
}

// Main chat endpoint
app.post('/chat', async (req, res) => {
    const requestId = req.requestId;
    logger.info(`[${requestId}] Chat request started`);

    const sse = new SSEResponseStream(res);

    // Convert Express request to Lambda-like event format
    const event = {
        headers: req.headers,
        body: JSON.stringify(req.body),
        requestContext: {
            requestId: requestId
        }
    };

    try {
        const params = await extractParams(event);

        const returnResponse = (responseStream, response) => {
            sse.returnResponse(response);
        };

        await routeRequest(params, returnResponse, sse);
        logger.info(`[${requestId}] Chat request completed successfully`);
    } catch (error) {
        logger.error(`[${requestId}] Chat request failed:`, error);
        sse.returnResponse({
            statusCode: 500,
            body: { error: "Internal server error", requestId }
        });
    }
});

// Error handling middleware
app.use((err, req, res, next) => {
    logger.error(`Unhandled error for request ${req.requestId}:`, err);
    res.status(500).json({
        error: 'Internal server error',
        requestId: req.requestId
    });
});

// 404 handler
app.use((req, res) => {
    res.status(404).json({
        error: 'Not found',
        path: req.path
    });
});

// Start server
const server = app.listen(port, '0.0.0.0', () => {
    logger.info(`Amplify Chat Container Service listening on port ${port}`);
    logger.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
    logger.info(`Allowed origins: ${allowedOrigins.join(', ')}`);
});

// Graceful shutdown handlers
const gracefulShutdown = (signal) => {
    logger.info(`${signal} signal received: closing HTTP server gracefully`);

    server.close(() => {
        logger.info('HTTP server closed');

        // Give time for in-flight requests to complete
        setTimeout(() => {
            logger.info('Process exiting');
            process.exit(0);
        }, 5000);
    });

    // Force close after 30 seconds
    setTimeout(() => {
        logger.error('Could not close connections in time, forcefully shutting down');
        process.exit(1);
    }, 30000);
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));

// Handle uncaught exceptions
process.on('uncaughtException', (error) => {
    logger.error('Uncaught exception:', error);
    gracefulShutdown('UNCAUGHT_EXCEPTION');
});

process.on('unhandledRejection', (reason, promise) => {
    logger.error('Unhandled rejection at:', promise, 'reason:', reason);
});

export default app;
