//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import bunyan from 'bunyan';

// Check if running locally - multiple ways to detect
const isLocal = process.env.LOCAL_DEVELOPMENT === 'true' || 
                process.argv.some(arg => arg.includes('localServer.js')) ||
                process.env.NODE_ENV === 'local';

if (isLocal) {
    console.log('🔧 Logger: Local development mode - console logging enabled');
}

// Shared logger configuration
const baseLogger = bunyan.createLogger({
    name: 'chat',
    level: process.env.LOG_LEVEL || 'debug'
});

export function getLogger(moduleName) {
    const logger = baseLogger.child({ module: moduleName }, true);
    
    // If running locally, intercept all logger calls and also console.log them
    if (isLocal) {
        const originalInfo = logger.info.bind(logger);
        const originalDebug = logger.debug.bind(logger);
        const originalWarn = logger.warn.bind(logger);
        const originalError = logger.error.bind(logger);
        
        logger.info = (...args) => {
            console.log(`🟦 [INFO] [${moduleName}]`, ...args);
            return originalInfo(...args);
        };
        
        logger.debug = (...args) => {
            console.log(`🔍 [DEBUG] [${moduleName}]`, ...args);
            return originalDebug(...args);
        };
        
        logger.warn = (...args) => {
            console.warn(`⚠️ [WARN] [${moduleName}]`, ...args);
            return originalWarn(...args);
        };
        
        logger.error = (...args) => {
            console.error(`❌ [ERROR] [${moduleName}]`, ...args);
            return originalError(...args);
        };
    }
    
    return logger;
};