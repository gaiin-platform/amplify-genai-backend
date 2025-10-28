/**
 * Python Execution Helper for Lambda
 * Provides safe and optimized Python code execution within Lambda environment
 */

import { promisify } from "node:util";
import { execFile } from "node:child_process";

const execFileAsync = promisify(execFile);

// Use explicit path to Python interpreter from the layer
const PY = "/opt/python/bin/python3.11";

/**
 * Execute Python code safely in Lambda environment
 *
 * @param {string} code - Python code to execute
 * @param {Object} extraEnv - Additional environment variables
 * @returns {Promise<string>} stdout from Python execution
 */
export async function runPython(code, extraEnv = {}) {
  try {
    const { stdout, stderr } = await execFileAsync(PY, ["-c", code], {
      env: {
        ...process.env,
        ...extraEnv,
        PYTHONHOME: "/opt/python",
        PYTHONPATH: "/opt/python/lib/python3.11"
      },
      maxBuffer: 1_000_000
    });

    if (stderr?.trim()) {
      console.error("[Python stderr]:", stderr);
    }

    return stdout;
  } catch (error) {
    console.error("[Python execution error]:", error.message);
    if (error.stderr) {
      console.error("[Python stderr]:", error.stderr);
    }
    throw error;
  }
}

/**
 * Verify Python environment is properly configured
 * This is useful for health checks and debugging
 *
 * @returns {Promise<Object>} Python version and package info
 */
export async function verifyPythonEnvironment() {
  const code = `
import json
import sys

try:
    import litellm
    litellm_version = getattr(litellm, "__version__", "unknown")
except ImportError as e:
    litellm_version = f"error: {str(e)}"

print(json.dumps({
    "python": sys.version.split()[0],
    "litellm": litellm_version,
    "pythonPath": sys.path
}))
`;

  const output = await runPython(code);
  return JSON.parse(output);
}
