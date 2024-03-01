import bunyan from 'bunyan';

// Shared logger configuration
const baseLogger = bunyan.createLogger({
    name: 'chat',
    level: process.env.LOG_LEVEL || 'debug'
});

export function getLogger(moduleName) {
    return baseLogger.child({ module: moduleName }, true);
};