'use strict';

// serverless@3.x (@serverless/dashboard-plugin) does `const simpleGit = require('simple-git'); simpleGit();`
// simple-git 3.x exported a callable function. Patched simple-git 4.x exports a plain object
// ({ simpleGit, gitP, ... }), so restore the callable CJS shape on top of it.
const patched = require('simple-git-patched');

const simpleGit = (...args) => patched.simpleGit(...args);
module.exports = Object.assign(simpleGit, patched, { simpleGit, default: simpleGit });
