"""
MCPToolRegistry: Tool discovery and caching with performance tracking

This module manages MCP tool discovery, caching, and intelligent categorization
for optimal performance and usability.
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ToolMetadata:
    """Metadata for an MCP tool."""
    name: str
    server: str
    qualified_name: str
    description: str
    input_schema: Dict[str, Any]
    category: str
    last_used: Optional[datetime] = None
    usage_count: int = 0
    average_execution_time: float = 0.0
    success_rate: float = 1.0
    discovered_at: datetime = None

    def __post_init__(self):
        if self.discovered_at is None:
            self.discovered_at = datetime.utcnow()


@dataclass
class ToolExecutionResult:
    """Result of a tool execution for tracking performance."""
    tool_name: str
    server: str
    success: bool
    execution_time: float
    error_message: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class MCPToolRegistry:
    """
    Manages MCP tool discovery, caching, and performance tracking.

    Features:
    - Intelligent caching with TTL
    - Tool categorization and organization
    - Performance tracking and metrics
    - Usage analytics and optimization
    - Function call format conversion for AI models
    """

    def __init__(self, cache_ttl: int = 300, max_cache_size: int = 1000):
        self.cache_ttl = cache_ttl  # 5 minutes default
        self.max_cache_size = max_cache_size

        # Tool registry and caching
        self.tools: Dict[str, ToolMetadata] = {}
        self.tool_cache_timestamp = None
        self.server_capabilities: Dict[str, Dict[str, Any]] = {}

        # Performance tracking
        self.execution_history: List[ToolExecutionResult] = []
        self.performance_metrics: Dict[str, Dict[str, float]] = defaultdict(dict)

        # Categories for tool organization
        self.tool_categories = {
            "filesystem": ["read", "write", "create", "delete", "list", "search", "file", "directory"],
            "memory": ["create_entities", "store", "retrieve", "remember", "knowledge"],
            "github": ["repo", "repository", "commit", "branch", "pull", "issue", "code"],
            "database": ["query", "select", "insert", "update", "delete", "sql", "db"],
            "search": ["search", "find", "query", "web", "browse", "lookup"],
            "analysis": ["analyze", "parse", "extract", "process", "compute"],
            "communication": ["send", "message", "email", "notify", "webhook"],
            "utility": ["convert", "format", "transform", "validate", "check"]
        }

    async def discover_tools(self, client_manager) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discover all available tools from all connected MCP servers.

        Args:
            client_manager: MCPClientManager instance

        Returns:
            Dict mapping server names to their discovered tools
        """
        # Check if cache is still valid
        if self._is_cache_valid():
            logger.debug("Using cached tool discovery results")
            return self._get_cached_tools_by_server()

        logger.info("Discovering tools from all MCP servers")
        start_time = time.time()

        try:
            # Discover tools from all servers
            all_tools = await client_manager.discover_all_tools()

            # Process and cache the discovered tools
            self._process_discovered_tools(all_tools)

            # Update cache timestamp
            self.tool_cache_timestamp = datetime.utcnow()

            discovery_time = time.time() - start_time
            logger.info(f"Tool discovery completed in {discovery_time:.2f}s. Found {len(self.tools)} tools.")

            return all_tools

        except Exception as e:
            logger.error(f"Tool discovery failed: {e}")
            return {}

    def _process_discovered_tools(self, all_tools: Dict[str, List[Dict[str, Any]]]):
        """Process and categorize discovered tools."""
        new_tools = {}

        for server_name, tools in all_tools.items():
            for tool in tools:
                tool_name = tool.get('name', 'unknown')
                qualified_name = f"{server_name}.{tool_name}"

                # Determine category
                category = self._categorize_tool(tool_name, tool.get('description', ''))

                # Create tool metadata
                tool_metadata = ToolMetadata(
                    name=tool_name,
                    server=server_name,
                    qualified_name=qualified_name,
                    description=tool.get('description', ''),
                    input_schema=tool.get('inputSchema', {}),
                    category=category
                )

                # Preserve existing usage statistics if tool was already known
                if qualified_name in self.tools:
                    existing_tool = self.tools[qualified_name]
                    tool_metadata.last_used = existing_tool.last_used
                    tool_metadata.usage_count = existing_tool.usage_count
                    tool_metadata.average_execution_time = existing_tool.average_execution_time
                    tool_metadata.success_rate = existing_tool.success_rate

                new_tools[qualified_name] = tool_metadata

        # Update tools registry
        self.tools = new_tools

        # Clean up old execution history (keep last 1000 entries)
        if len(self.execution_history) > 1000:
            self.execution_history = self.execution_history[-1000:]

    def _categorize_tool(self, tool_name: str, description: str) -> str:
        """Categorize a tool based on its name and description."""
        name_lower = tool_name.lower()
        desc_lower = description.lower()

        for category, keywords in self.tool_categories.items():
            if any(keyword in name_lower or keyword in desc_lower for keyword in keywords):
                return category

        return "utility"  # Default category

    def _is_cache_valid(self) -> bool:
        """Check if the tool cache is still valid."""
        if self.tool_cache_timestamp is None:
            return False

        age = datetime.utcnow() - self.tool_cache_timestamp
        return age < timedelta(seconds=self.cache_ttl)

    def _get_cached_tools_by_server(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get cached tools organized by server."""
        tools_by_server = defaultdict(list)

        for tool_metadata in self.tools.values():
            tool_dict = {
                'name': tool_metadata.name,
                'description': tool_metadata.description,
                'inputSchema': tool_metadata.input_schema
            }
            tools_by_server[tool_metadata.server].append(tool_dict)

        return dict(tools_by_server)

    def get_tools_for_ai_model(self, limit: Optional[int] = None,
                              categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Get tools formatted for AI model function calls.

        Args:
            limit: Maximum number of tools to return (most relevant first)
            categories: Filter tools by categories

        Returns:
            List of tools in OpenAI function call format
        """
        # Filter tools by categories if specified
        filtered_tools = []
        for tool in self.tools.values():
            if categories is None or tool.category in categories:
                filtered_tools.append(tool)

        # Sort by relevance (usage count, success rate, recency)
        filtered_tools.sort(key=lambda t: (
            t.usage_count * t.success_rate,  # Primary: usage * success rate
            -t.average_execution_time if t.average_execution_time > 0 else 0,  # Secondary: speed
            t.last_used.timestamp() if t.last_used else 0  # Tertiary: recency
        ), reverse=True)

        # Apply limit
        if limit:
            filtered_tools = filtered_tools[:limit]

        # Convert to OpenAI function call format
        function_definitions = []
        for tool in filtered_tools:
            function_def = {
                "type": "function",
                "function": {
                    "name": tool.qualified_name,
                    "description": f"[{tool.server}] {tool.description}",
                    "parameters": tool.input_schema
                }
            }
            function_definitions.append(function_def)

        return function_definitions

    def get_tool_by_qualified_name(self, qualified_name: str) -> Optional[ToolMetadata]:
        """Get tool metadata by qualified name."""
        return self.tools.get(qualified_name)

    def parse_qualified_tool_name(self, qualified_name: str) -> tuple[str, str]:
        """Parse qualified tool name into server and tool name."""
        if '.' in qualified_name:
            server, tool_name = qualified_name.split('.', 1)
            return server, tool_name
        else:
            # If no server prefix, try to find the tool in any server
            for tool in self.tools.values():
                if tool.name == qualified_name:
                    return tool.server, tool.name
            raise ValueError(f"Tool {qualified_name} not found")

    def record_tool_execution(self, result: ToolExecutionResult):
        """Record the result of a tool execution for performance tracking."""
        self.execution_history.append(result)

        qualified_name = f"{result.server}.{result.tool_name}"

        if qualified_name in self.tools:
            tool = self.tools[qualified_name]

            # Update usage statistics
            tool.usage_count += 1
            tool.last_used = result.timestamp

            # Update average execution time
            if tool.average_execution_time == 0:
                tool.average_execution_time = result.execution_time
            else:
                # Moving average
                tool.average_execution_time = (
                    (tool.average_execution_time * (tool.usage_count - 1) + result.execution_time)
                    / tool.usage_count
                )

            # Update success rate
            total_executions = sum(1 for r in self.execution_history
                                 if r.tool_name == result.tool_name and r.server == result.server)
            successful_executions = sum(1 for r in self.execution_history
                                      if r.tool_name == result.tool_name and r.server == result.server and r.success)

            if total_executions > 0:
                tool.success_rate = successful_executions / total_executions

    def get_tool_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about tool usage and performance."""
        if not self.tools:
            return {"error": "No tools discovered yet"}

        stats = {
            "total_tools": len(self.tools),
            "tools_by_server": {},
            "tools_by_category": {},
            "most_used_tools": [],
            "fastest_tools": [],
            "most_reliable_tools": [],
            "total_executions": len(self.execution_history)
        }

        # Group by server
        server_counts = defaultdict(int)
        category_counts = defaultdict(int)

        for tool in self.tools.values():
            server_counts[tool.server] += 1
            category_counts[tool.category] += 1

        stats["tools_by_server"] = dict(server_counts)
        stats["tools_by_category"] = dict(category_counts)

        # Most used tools (top 5)
        most_used = sorted(self.tools.values(), key=lambda t: t.usage_count, reverse=True)[:5]
        stats["most_used_tools"] = [
            {"name": t.qualified_name, "usage_count": t.usage_count}
            for t in most_used if t.usage_count > 0
        ]

        # Fastest tools (top 5, minimum 1 execution)
        fastest = sorted(
            [t for t in self.tools.values() if t.average_execution_time > 0],
            key=lambda t: t.average_execution_time
        )[:5]
        stats["fastest_tools"] = [
            {"name": t.qualified_name, "avg_time": round(t.average_execution_time, 3)}
            for t in fastest
        ]

        # Most reliable tools (top 5, minimum 2 executions)
        reliable = sorted(
            [t for t in self.tools.values() if t.usage_count >= 2],
            key=lambda t: t.success_rate,
            reverse=True
        )[:5]
        stats["most_reliable_tools"] = [
            {"name": t.qualified_name, "success_rate": round(t.success_rate, 3)}
            for t in reliable
        ]

        return stats

    def get_tools_by_category(self, category: str) -> List[ToolMetadata]:
        """Get all tools in a specific category."""
        return [tool for tool in self.tools.values() if tool.category == category]

    def search_tools(self, query: str) -> List[ToolMetadata]:
        """Search tools by name or description."""
        query_lower = query.lower()
        matching_tools = []

        for tool in self.tools.values():
            if (query_lower in tool.name.lower() or
                query_lower in tool.description.lower() or
                query_lower in tool.qualified_name.lower()):
                matching_tools.append(tool)

        # Sort by relevance (exact name match first, then description match)
        matching_tools.sort(key=lambda t: (
            t.name.lower() != query_lower,  # Exact name matches first
            query_lower not in t.name.lower(),  # Name contains query
            t.usage_count  # Then by usage count
        ), reverse=False)

        return matching_tools

    def clear_cache(self):
        """Clear the tool cache to force re-discovery."""
        self.tool_cache_timestamp = None
        logger.info("Tool cache cleared")

    def export_tools(self) -> Dict[str, Any]:
        """Export all tools and their metadata for backup/analysis."""
        return {
            "tools": {name: asdict(tool) for name, tool in self.tools.items()},
            "cache_timestamp": self.tool_cache_timestamp.isoformat() if self.tool_cache_timestamp else None,
            "execution_history": [asdict(result) for result in self.execution_history[-100:]],  # Last 100 executions
            "statistics": self.get_tool_statistics()
        }

    def get_all_tools(self) -> List[ToolMetadata]:
        """Get all tools in the registry."""
        return list(self.tools.values())

    def get_tools_by_server(self, server_name: str) -> List[ToolMetadata]:
        """Get all tools from a specific server."""
        return [tool for tool in self.tools.values() if tool.server == server_name]

    def parse_qualified_tool_name(self, qualified_name: str) -> tuple[str, str]:
        """Parse a qualified tool name into server and tool components."""
        if '.' not in qualified_name:
            raise ValueError(f"Invalid qualified tool name: {qualified_name}")

        parts = qualified_name.split('.', 1)
        return parts[0], parts[1]