"""
Core Agent Logic for Chatto.

Orchestrates the interaction between the LLM and the MCP Gateway tools.

This module contains the main agent loop, tool parsing logic, and state management.
It acts as the brain, coordinating calls to the client and the LLM interface.
"""

import os
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .chatto_client import MCPClient
    from .llm_interface import LLMInterface, load_skills
except ImportError:
    from chatto_client import MCPClient
    from llm_interface import LLMInterface, load_skills

# Default endpoints
LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://127.0.0.1:1234")
MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://localhost:8000")


class ChattoAgent:
    """
    The main agent orchestrator. Manages the conversational state and executes
    the agentic loop (LLM -> Tool Call -> Result -> LLM).
    """

    def __init__(
        self,
        lmstudio_url: Optional[str] = None,
        mcp_gateway_url: Optional[str] = None,
    ):
        self.llm_interface = LLMInterface(lmstudio_url or LMSTUDIO_URL)
        self.mcp_client = MCPClient(mcp_gateway_url or MCP_GATEWAY_URL)

        # State management
        self.session_id: Optional[str] = None
        self.servers_tools: Dict[str, List[Any]] = {}
        self.tool_format: str = ""
        self.base_skills: str = load_skills()
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history_messages = 10

    def discover_tools(self) -> Dict[str, List[Any]]:
        """Discover and cache available MCP tools."""
        self.servers_tools = self.mcp_client.discover_tools()
        self.tool_format = self._generate_tool_format(self.servers_tools)
        return self.servers_tools

    def _generate_tool_format(self, servers_tools: Dict[str, List[Any]]) -> str:
        """Generates the structured tool format prompt for the LLM."""
        if not servers_tools:
            return "No tools available."

        format_sections = [
            "## Available Tool Call Format",
            "",
            "When you need to use a tool, respond with these exact XML tags:",
            "",
            "```",
            "<tool_call>",
            "tool: <server_container_name>",
            "name: <tool_name>",
            "arguments: {<json_arguments>}",
            "</tool_call>",
            "```",
            "",
            "## Available Tools",
            "",
        ]

        for server_name, tools in servers_tools.items():
            format_sections.append(f"### {server_name}")
            format_sections.append("")

            if not tools:
                format_sections.append("*No tools available*")
                format_sections.append("")
                continue

            for tool in tools:
                tool_name = tool.get("name", "unknown")
                description = tool.get("description", "No description")
                input_schema = tool.get("inputSchema", {})
                properties = input_schema.get("properties", {})

                format_sections.append(f"**Tool:** `{tool_name}`")
                format_sections.append(f"**Description:** {description}")

                if properties:
                    required = input_schema.get("required", [])
                    args_list = []
                    for prop_name, prop_info in properties.items():
                        prop_type = prop_info.get("type", "any")
                        prop_desc = prop_info.get("description", "")
                        default = prop_info.get("default")
                        required_mark = "*" if prop_name in required else ""
                        args_list.append(
                            f"- `{prop_name}` ({prop_type}){required_mark}: {prop_desc}"
                        )

                    format_sections.append("**Arguments:**")
                    format_sections.extend(args_list)

                format_sections.append("")

                example_args = {}
                for prop_name, prop_info in properties.items():
                    example_args[prop_name] = (
                        prop_info.get("default") or f"<{prop_name}>"
                    )

                if example_args:
                    format_sections.append(f"**Example call:**")
                    format_sections.append("```")
                    format_sections.append("<tool_call>")
                    format_sections.append(f"tool: {server_name}")
                    format_sections.append(f"name: {tool_name}")
                    format_sections.append(f"arguments: {json.dumps(example_args)}")
                    format_sections.append("</tool_call>")
                    format_sections.append("```")
                    format_sections.append("")

        return "\n".join(format_sections)

    def _parse_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse tool calls from LLM response text using regex patterns.
        Returns a list of structured tool call dictionaries.
        """
        tool_calls = []

        text = text.replace("&quot;", '"').replace("&#34;", '"')

        text = re.sub(r"<\|tool_call>", "<tool_call>", text)
        text = re.sub(r"</tool_call\|>", "</tool_call>", text)
        text = re.sub(
            r"<environment_details>.*</environment_details>", "", text, flags=re.DOTALL
        )
        text = text.strip()

        pattern1 = r"<tool_call>\s*tool:\s*(\S+)\s*name:\s*(\S+)\s*arguments:\s*(\{.*?\})\s*</tool_call>"
        pattern2 = r"<tool_call>\s+call\s*\n\s*tool:\s*(\S+)\s*\n\s*name:\s*(\S+)\s*\n\s*arguments:\s*(\{.*?\})\s*</tool_call>"
        pattern3 = r"<tool_call>\s+call:\s*(\S+)\s*\n\s*name:\s*(\S+)\s*\n\s*arguments:\s*(\{.*?\})\s*</tool_call>"
        pattern4 = r"<tool_call>\s*call\s+tool:\s*(\S+)\s*name:\s*(\S+)\s*arguments:\s*(\{.*?\})\s*</tool_call>"

        for pattern in [pattern1, pattern2, pattern3, pattern4]:
            matches = re.finditer(pattern, text, re.DOTALL)
            for match in matches:
                server = match.group(1).strip()
                tool_name = match.group(2).strip()
                try:
                    args = json.loads(match.group(3))
                except:
                    args = {}
                tool_calls.append(
                    {"server": server, "name": tool_name, "arguments": args}
                )

        seen_calls = set()
        unique_tool_calls = []
        for tc in tool_calls:
            call_key = f"{tc.get('server')}:{tc.get('name')}:{json.dumps(tc.get('arguments', {}), sort_keys=True)}"
            if call_key not in seen_calls:
                seen_calls.add(call_key)
                unique_tool_calls.append(tc)
        tool_calls = unique_tool_calls

        if not tool_calls:
            text_fixed = text.replace("<tool_call>", "").replace("</tool_call>", "")

            tool_call_pattern = r"call:([^<{]+)\{(.*?)\}"
            matches = list(re.finditer(tool_call_pattern, text_fixed))

            for match in matches:
                server = match.group(1).strip()
                raw_content = match.group(2)

                args = {}
                tool_name = "search_tools"

                kv_pattern = r'(\w+):(?:"([^"]*)"|\{([^}]+)\}|([^,}]+))'
                for kv in re.finditer(kv_pattern, raw_content):
                    key = kv.group(1)
                    value = kv.group(2) or kv.group(3) or kv.group(4) or ""

                    if key == "name":
                        tool_name = value
                    else:
                        clean_value = value.strip()
                        if clean_value.startswith("{") and clean_value.endswith("}"):
                            inner = clean_value[1:-1]
                            inner_args = {}
                            for inner_kv in re.finditer(
                                r'(\w+):(?:"([^"]*)"|\'([^\']*)\'|([^,}]+))', inner
                            ):
                                k = inner_kv.group(1)
                                v = (
                                    inner_kv.group(2)
                                    or inner_kv.group(3)
                                    or inner_kv.group(4)
                                    or ""
                                )
                                inner_args[k] = v
                            args[key] = inner_args
                        else:
                            if ":" in clean_value:
                                parts = clean_value.split(":", 1)
                                args[key] = parts[1].strip().strip('"').strip("'")
                            else:
                                args[key] = clean_value.strip().strip('"').strip("'")

                if args:
                    if "arguments" in args and isinstance(args["arguments"], dict):
                        args = args["arguments"]
                    tool_calls.append(
                        {"server": server, "name": tool_name, "arguments": args}
                    )

        resolved_calls = []
        for tc in tool_calls:
            server = tc.get("server", "")
            tool_name = tc.get("name", "")

            resolved_server = self._resolve_server(server, tool_name)
            if resolved_server:
                resolved_calls.append(
                    {
                        "server": resolved_server,
                        "name": tool_name,
                        "arguments": tc.get("arguments", {}),
                    }
                )

        return resolved_calls

    def _resolve_server(self, partial_name: str, tool_name: str) -> str:
        """Resolve partial server name to full server name from discovered servers."""
        if not partial_name or not self.servers_tools:
            return partial_name

        partial_lower = partial_name.lower()

        for server_name in self.servers_tools.keys():
            if partial_lower in server_name.lower():
                return server_name

        return partial_name

    def chat(self, user_message: str) -> str:
        """
        Process a chat message with agentic tool execution and iterative refinement.
        """
        if not self.session_id or not self.servers_tools:
            self.discover_tools()

        max_iterations = 5
        iteration = 0
        current_prompt = user_message
        response = ""
        results_str = ""
        tool_calls: List[Dict[str, Any]] = []

        while iteration < max_iterations:
            iteration += 1

            response = self.llm_interface.query_llm(
                current_prompt,
                base_skills=self.base_skills,
                tool_format=self.tool_format,
                conversation_history=self.conversation_history,
            )

            print(f"[DEBUG] LLM response (iteration {iteration}): {response[:200]}...")

            tool_calls = self._parse_tool_calls(response)

            if not tool_calls:
                break

            results = []
            for tc in tool_calls:
                server = tc.get("server")
                tool_name = tc.get("name")
                args = tc.get("arguments", {})

                if not server or not tool_name:
                    continue

                print(f"[DEBUG] Executing {server}.{tool_name} with {args}")
                result = self.mcp_client.execute_tool(server, tool_name, args)
                print(f"[DEBUG] Result: {result}")
                results.append(result)

            results_str = "\n\n".join([json.dumps(r) for r in results])
            current_prompt = (
                f"User question: {user_message}\n\n"
                f"Tool execution results from iteration {iteration}:\n{results_str}\n\n"
                f"If you need more information, make another tool call. "
                f"Otherwise, provide a complete and helpful answer."
            )

        if tool_calls:
            response = self.llm_interface.query_llm(
                f"User question: {user_message}\n\nTool execution results:\n{results_str}\n\n"
                f"Please provide a helpful answer based on these results.",
                base_skills=self.base_skills,
                tool_format=self.tool_format,
            )

        if not response:
            response = "I wasn't able to find any information. Please try again."

        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": response})

        if len(self.conversation_history) > self.max_history_messages * 2:
            self.conversation_history = self.conversation_history[
                -(self.max_history_messages * 2) :
            ]

        return response


def run_chatto():
    """Run Chatto agent interactively in terminal."""
    agent = ChattoAgent()

    print("Chatto Agent initializing...")
    print(f"LM Studio: {agent.llm_interface.lmstudio_url}")
    print(f"MCP Gateway: {agent.mcp_client.mcp_gateway_url}")

    print("\nDiscovering available tools...")
    agent.discover_tools()

    print(f"\nDiscovered tools from {len(agent.servers_tools)} servers:")
    for server, tools in agent.servers_tools.items():
        print(f"  - {server}: {len(tools)} tools")

    print("\nType 'quit' to exit\n")

    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() == "quit":
                break

            response = agent.chat(user_input)
            print(f"Chatto: {response}\n")
        except EOFError:
            break
        except Exception as e:
            print(f"Fatal Error during chat loop: {e}")
            break


if __name__ == "__main__":
    run_chatto()
