const bunyan = require('bunyan');

// Shared logger configuration
const baseLogger = bunyan.createLogger({
    name: 'chat',
    level: process.env.LOG_LEVEL || 'debug'
});

function getLogger(moduleName) {
    return baseLogger.child({ module: moduleName }, true);
}

module.exports = { getLogger };
