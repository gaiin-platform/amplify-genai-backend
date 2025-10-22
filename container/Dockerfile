# Multi-stage Dockerfile for Amplify Chat Service
FROM node:22-alpine AS base

# Install build dependencies
RUN apk add --no-cache \
    python3 \
    make \
    g++ \
    ca-certificates

WORKDIR /app

# Copy root package files for shared dependencies
COPY package*.json ./

# Copy amplify-lambda-js package files
COPY amplify-lambda-js/package*.json ./amplify-lambda-js/

# Install all dependencies
RUN npm ci --only=production && \
    cd amplify-lambda-js && \
    npm ci --only=production

# Production stage
FROM node:22-alpine AS production

# Install runtime dependencies only
RUN apk add --no-cache \
    ca-certificates \
    tini

WORKDIR /app

# Copy node_modules from base stage
COPY --from=base /app/node_modules ./node_modules
COPY --from=base /app/amplify-lambda-js/node_modules ./amplify-lambda-js/node_modules

# Copy application code
COPY amplify-lambda-js ./amplify-lambda-js
COPY container ./container

# Create non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001 && \
    chown -R nodejs:nodejs /app

USER nodejs

EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD node -e "require('http').get('http://localhost:8080/health', (r) => { process.exit(r.statusCode === 200 ? 0 : 1); }).on('error', () => process.exit(1));"

# Use tini as init system for proper signal handling
ENTRYPOINT ["/sbin/tini", "--"]

CMD ["node", "container/server.js"]
