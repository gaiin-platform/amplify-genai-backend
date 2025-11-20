//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import bunyan from 'bunyan';

// Shared logger configuration
const baseLogger = bunyan.createLogger({
    name: 'chat',
    level: process.env.LOG_LEVEL || 'debug'
});

export function getLogger(moduleName) {
    return baseLogger.child({ module: moduleName }, true);
};