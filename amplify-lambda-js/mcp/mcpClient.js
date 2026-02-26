/**
 * MCP (Model Context Protocol) Client
 *
 * Handles connecting to MCP servers and executing tools.
 * Supports HTTP/SSE transport for remote MCP servers.
 */

import { getLogger } from '../common/logging.js';

const logger = getLogger('mcpClient');

// MCP Protocol constants
const MCP_VERSION = '2024-11-05';
const DEFAULT_TIMEOUT = 30000; // 30 seconds

/**
 * MCP Client class for managing connections to MCP servers
 */
export class MCPClient {
    constructor(config) {
        this.id = config.id;
        this.name = config.name;
        this.url = config.url;
        this.transport = config.transport || 'sse';
        this.timeout = config.timeout || DEFAULT_TIMEOUT;
        this.tools = [];
        this.resources = [];
        this.prompts = [];
        this.connected = false;
        this.serverInfo = null;
    }

    /**
     * Initialize connection to MCP server
     * Performs handshake and discovers capabilities
     */
    async connect() {
        logger.info(`Connecting to MCP server: ${this.name} at ${this.url}`);

        try {
            // Send initialize request
            const initResponse = await this._sendRequest('initialize', {
                protocolVersion: MCP_VERSION,
                capabilities: {
                    tools: {},
                    resources: {},
                    prompts: {}
                },
                clientInfo: {
                    name: 'amplify-genai',
                    version: '1.0.0'
                }
            });

            if (!initResponse || initResponse.error) {
                throw new Error(initResponse?.error?.message || 'Failed to initialize MCP connection');
            }

            this.serverInfo = initResponse.result?.serverInfo;
            logger.info(`MCP server initialized: ${this.serverInfo?.name || 'Unknown'}`);

            // Send initialized notification
            await this._sendNotification('notifications/initialized', {});

            // Discover tools
            await this._discoverTools();

            this.connected = true;
            return {
                success: true,
                serverInfo: this.serverInfo,
                tools: this.tools
            };

        } catch (error) {
            logger.error(`Failed to connect to MCP server ${this.name}: ${error.message}`);
            this.connected = false;
            throw error;
        }
    }

    /**
     * Discover available tools from the MCP server
     */
    async _discoverTools() {
        try {
            const response = await this._sendRequest('tools/list', {});

            if (response.result?.tools) {
                this.tools = response.result.tools.map(tool => ({
                    name: tool.name,
                    description: tool.description,
                    inputSchema: tool.inputSchema,
                    serverId: this.id,
                    serverName: this.name
                }));
                logger.info(`Discovered ${this.tools.length} tools from ${this.name}`);
            }
        } catch (error) {
            logger.warn(`Failed to discover tools from ${this.name}: ${error.message}`);
            this.tools = [];
        }
    }

    /**
     * Execute a tool on the MCP server
     */
    async executeTool(toolName, args) {
        if (!this.connected) {
            throw new Error(`MCP server ${this.name} is not connected`);
        }

        logger.info(`Executing MCP tool: ${toolName} on ${this.name}`);
        logger.debug(`Tool arguments: ${JSON.stringify(args)}`);

        try {
            const response = await this._sendRequest('tools/call', {
                name: toolName,
                arguments: args
            });

            if (response.error) {
                throw new Error(response.error.message || 'Tool execution failed');
            }

            const result = response.result;

            // MCP returns content as array of content items
            let content = '';
            if (result?.content && Array.isArray(result.content)) {
                content = result.content.map(item => {
                    if (item.type === 'text') {
                        return item.text;
                    } else if (item.type === 'image') {
                        return `[Image: ${item.mimeType}]`;
                    } else if (item.type === 'resource') {
                        return `[Resource: ${item.uri}]`;
                    }
                    return JSON.stringify(item);
                }).join('\n');
            }

            return {
                success: true,
                content: content,
                rawResult: result,
                isError: result?.isError || false
            };

        } catch (error) {
            logger.error(`MCP tool execution failed: ${error.message}`);
            return {
                success: false,
                content: `Error executing tool ${toolName}: ${error.message}`,
                isError: true
            };
        }
    }

    /**
     * Send a JSON-RPC request to the MCP server
     */
    async _sendRequest(method, params) {
        const requestId = `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

        const message = {
            jsonrpc: '2.0',
            id: requestId,
            method: method,
            params: params
        };

        return await this._httpRequest(message);
    }

    /**
     * Send a JSON-RPC notification (no response expected)
     */
    async _sendNotification(method, params) {
        const message = {
            jsonrpc: '2.0',
            method: method,
            params: params
        };

        try {
            await this._httpRequest(message, false);
        } catch (error) {
            // Notifications may not always get a response
            logger.debug(`Notification ${method} sent (response: ${error.message})`);
        }
    }

    /**
     * Make HTTP request to MCP server
     */
    async _httpRequest(message, expectResponse = true) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeout);

        try {
            const response = await fetch(this.url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/event-stream'
                },
                body: JSON.stringify(message),
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            if (expectResponse) {
                return await response.json();
            }
            return null;

        } catch (error) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError') {
                throw new Error('Request timeout');
            }
            throw error;
        }
    }

    /**
     * Get tool definitions in OpenAI function format
     */
    getToolDefinitions() {
        return this.tools.map(tool => ({
            type: 'function',
            function: {
                name: `mcp_${this.id}_${tool.name}`,
                description: `[${this.name}] ${tool.description}`,
                parameters: tool.inputSchema || {
                    type: 'object',
                    properties: {},
                    required: []
                }
            },
            _mcpInfo: {
                serverId: this.id,
                serverName: this.name,
                originalName: tool.name
            }
        }));
    }

    /**
     * Disconnect from MCP server
     */
    async disconnect() {
        this.connected = false;
        this.tools = [];
        logger.info(`Disconnected from MCP server: ${this.name}`);
    }
}

/**
 * Parse an MCP tool call name to extract server ID and original tool name
 * Format: mcp_{serverId}_{toolName}
 *
 * Server ID format: mcp_{timestamp}_{random} (e.g., mcp_1767629390657_b1c2812d7)
 * Full tool name: mcp_mcp_{timestamp}_{random}_{toolName}
 */
export function parseMCPToolName(toolName) {
    if (!toolName.startsWith('mcp_')) {
        return null;
    }

    // Remove initial 'mcp_' prefix (the MCP tool marker)
    const remaining = toolName.substring(4);

    // Server ID format: mcp_{timestamp}_{random}
    // e.g., mcp_1767629390657_b1c2812d7
    // The timestamp is 13+ digits (milliseconds), random is alphanumeric
    const serverIdMatch = remaining.match(/^(mcp_\d+_[a-z0-9]+)_(.+)$/);
    if (serverIdMatch) {
        return {
            serverId: serverIdMatch[1],
            originalName: serverIdMatch[2]
        };
    }

    // Fallback for other server ID formats (not starting with mcp_)
    const parts = remaining.split('_');
    if (parts.length < 2) {
        return null;
    }

    return {
        serverId: parts[0],
        originalName: parts.slice(1).join('_')
    };
}

/**
 * Check if a tool name is an MCP tool
 */
export function isMCPTool(toolName) {
    return toolName && toolName.startsWith('mcp_');
}

export default MCPClient;
