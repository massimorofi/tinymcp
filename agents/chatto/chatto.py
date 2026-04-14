import os
import requests
import json
import re
from pathlib import Path

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://127.0.0.1:1234")
MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://localhost:8000")

SKILLS_FILE = Path(__file__).parent / "chatto_skills.md"


def load_skills():
    """Load Chatto base skills from markdown file."""
    if SKILLS_FILE.exists():
        return SKILLS_FILE.read_text()
    return ""


def generate_tool_format(servers_tools):
    """Dynamically generate tool call format from available tools."""
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
        ""
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
                    args_list.append(f"- `{prop_name}` ({prop_type}){required_mark}: {prop_desc}")
                
                format_sections.append("**Arguments:**")
                format_sections.extend(args_list)
            
            format_sections.append("")
            
            example_args = {}
            for prop_name, prop_info in properties.items():
                example_args[prop_name] = prop_info.get("default") or f"<{prop_name}>"
            
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


class ChattoAgent:
    def __init__(self, lmstudio_url=None, mcp_gateway_url=None):
        self.lmstudio_url = lmstudio_url or LMSTUDIO_URL
        self.mcp_gateway_url = mcp_gateway_url or MCP_GATEWAY_URL
        self.session_id = None
        self.session = requests.Session()
        self.base_skills = load_skills()
        self.tool_format = ""
        self.servers_tools = {}
        self.conversation_history = []
        self.max_history_messages = 10  # Keep last N messages
        
    def init_session(self):
        """Initialize a session with the MCP Gateway."""
        try:
            response = self.session.post(f"{self.mcp_gateway_url}/sessions")
            if response.ok:
                data = response.json()
                self.session_id = data["sessionId"]
                return True
        except Exception as e:
            print(f"Error initializing session: {e}")
        return False
    
    def discover_tools(self):
        """Connect to MCP Gateway and discover all available tools."""
        if not self.session_id:
            self.init_session()
        
        try:
            response = self.session.get(f"{self.mcp_gateway_url}/registry/servers")
            if not response.ok:
                return {}
            
            servers = response.json()
            
            for server in servers:
                server_id = server.get("id")
                try:
                    tool_response = self.session.post(
                        f"{self.mcp_gateway_url}/execute?server={server_id}",
                        headers={"X-Session-ID": self.session_id},
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/list",
                            "params": {},
                            "id": 1
                        }
                    )
                    if tool_response.ok:
                        data = tool_response.json()
                        tools = data.get("result", {}).get("result", {}).get("tools", [])
                        self.servers_tools[server_id] = tools
                except Exception as e:
                    print(f"Error getting tools for {server_id}: {e}")
                    self.servers_tools[server_id] = []
            
            self.tool_format = generate_tool_format(self.servers_tools)
            return self.servers_tools
        except Exception as e:
            print(f"Error discovering tools: {e}")
        return {}
    
    def get_servers(self):
        """Get list of MCP servers."""
        try:
            response = self.session.get(f"{self.mcp_gateway_url}/registry/servers")
            if response.ok:
                return response.json()
        except:
            pass
        return []
    
    def execute_tool(self, server_name, tool_name, arguments=None):
        """Execute an MCP tool."""
        if not self.session_id:
            self.init_session()
        
        if arguments is None:
            arguments = {}
        
        try:
            response = self.session.post(
                f"{self.mcp_gateway_url}/execute?server={server_name}",
                headers={
                    "X-Session-ID": self.session_id,
                    "Content-Type": "application/json"
                },
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments
                    },
                    "id": 2
                }
            )
            if response.ok:
                data = response.json()
                
                result = data.get("result", {}).get("result", {})
                content = result.get("content", [])
                
                if content and isinstance(content, list):
                    text_content = content[0].get("text", "")
                    return text_content
                
                return str(data)
            return {"error": response.text}
        except Exception as e:
            return {"error": str(e)}
    
    def parse_tool_calls(self, text):
        """Parse tool calls from LLM response."""
        tool_calls = []
        
        text = text.replace('&quot;', '"').replace('&#34;', '"')
        
        # Handle <|tool_call> format - convert to standard <tool_call> format
        # Also remove any <environment_details> tags that may be in the response
        text = re.sub(r'<\|tool_call>', '<tool_call>', text)
        text = re.sub(r'</tool_call\|>', '</tool_call>', text)
        text = re.sub(r'<environment_details>.*</environment_details>', '', text, flags=re.DOTALL)
        text = text.strip()
        
        # Pattern 1: <tool_call>tool: server name: tool arguments: {...}</tool_call>
        pattern1 = r'<tool_call>\s*tool:\s*(\S+)\s*name:\s*(\S+)\s*arguments:\s*(\{.*?\})\s*</tool_call>'
        
        # Pattern 2: <tool_call>call\ntool: server\nname: tool\narguments: {...}</tool_call>
        pattern2 = r'<tool_call>\s+call\s*\n\s*tool:\s*(\S+)\s*\n\s*name:\s*(\S+)\s*\n\s*arguments:\s*(\{.*?\})\s*</tool_call>'
        
        # Pattern 3: <tool_call>call: server\nname: tool\narguments: {...}</tool_call>
        pattern3 = r'<tool_call>\s+call:\s*(\S+)\s*\n\s*name:\s*(\S+)\s*\n\s*arguments:\s*(\{.*?\})\s*</tool_call>'
        
        # Pattern 4: <tool_call>call tool: server name: tool arguments: {...}</tool_call> (no newline)
        pattern4 = r'<tool_call>\s*call\s+tool:\s*(\S+)\s*name:\s*(\S+)\s*arguments:\s*(\{.*?\})\s*</tool_call>'
        
        for pattern in [pattern1, pattern2, pattern3, pattern4]:
            matches = re.finditer(pattern, text, re.DOTALL)
            for match in matches:
                server = match.group(1).strip()
                tool_name = match.group(2).strip()
                try:
                    args = json.loads(match.group(3))
                except:
                    args = {}
                tool_calls.append({"server": server, "name": tool_name, "arguments": args})
        
        seen_calls = set()
        unique_tool_calls = []
        for tc in tool_calls:
            call_key = f"{tc.get('server')}:{tc.get('name')}:{json.dumps(tc.get('arguments', {}), sort_keys=True)}"
            if call_key not in seen_calls:
                seen_calls.add(call_key)
                unique_tool_calls.append(tc)
        tool_calls = unique_tool_calls
        
        if not tool_calls:
            text_fixed = text.replace('<tool_call>', '').replace('</tool_call>', '')
            
            tool_call_pattern = r'call:([^<{]+)\{(.*?)\}'
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
                            if clean_value.startswith('{') and clean_value.endswith('}'):
                                inner = clean_value[1:-1]
                                inner_args = {}
                                for inner_kv in re.finditer(r'(\w+):(?:"([^"]*)"|\'([^\']*)\'|([^,}]+))', inner):
                                    k = inner_kv.group(1)
                                    v = inner_kv.group(2) or inner_kv.group(3) or inner_kv.group(4) or ""
                                    inner_args[k] = v
                                args[key] = inner_args
                            else:
                                if ':' in clean_value:
                                    parts = clean_value.split(':', 1)
                                    args[key] = parts[1].strip().strip('"').strip("'")
                                else:
                                    args[key] = clean_value.strip().strip('"').strip("'")
                    
                    print(f"[PARSE] Parsed args: {args}")
                    
                    if args:
                        if 'arguments' in args and isinstance(args['arguments'], dict):
                            args = args['arguments']
                        tool_calls.append({"server": server, "name": tool_name, "arguments": args})
        
        resolved_calls = []
        for tc in tool_calls:
            server = tc.get("server", "")
            tool_name = tc.get("name", "")
            
            resolved_server = self._resolve_server(server, tool_name)
            if resolved_server:
                resolved_calls.append({
                    "server": resolved_server,
                    "name": tool_name,
                    "arguments": tc.get("arguments", {})
                })
        
        return resolved_calls
    
    def _resolve_server(self, partial_name, tool_name):
        """Resolve partial server name to full server name from discovered servers."""
        if not partial_name or not self.servers_tools:
            return partial_name
        
        partial_lower = partial_name.lower()
        
        for server_name in self.servers_tools.keys():
            if partial_lower in server_name.lower():
                return server_name
        
        return partial_name
    
    def query_llm(self, prompt, include_history=True):
        """Query LM Studio with conversation history."""
        messages = [
            {"role": "system", "content": self.base_skills},
            {"role": "system", "content": self.tool_format}
        ]
        
        if include_history and self.conversation_history:
            messages.extend(self.conversation_history)
        
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "messages": messages,
            "model": "local-model",
            "temperature": 0.7,
            "max_tokens": -1,
            "stream": False
        }
        
        try:
            response = self.session.post(
                f"{self.lmstudio_url}/v1/chat/completions",
                json=payload,
                timeout=120
            )
            if response.ok:
                data = response.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return f"Error: {response.text}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def chat(self, user_message):
        """Process a chat message with agentic agentic tool execution with iteration."""
        if not self.session_id:
            self.discover_tools()
        
        max_iterations = 5
        iteration = 0
        current_prompt = user_message
        response = ""
        results_str = ""
        tool_calls = []
        
        while iteration < max_iterations:
            iteration += 1
            
            response = self.query_llm(current_prompt)
            
            print(f"[DEBUG] LLM response (iteration {iteration}): {response[:200]}...")
            
            tool_calls = self.parse_tool_calls(response)
            
            print(f"[DEBUG] Parsed tool calls: {tool_calls}")
            
            if not tool_calls:
                break
            
            results = []
            for tc in tool_calls:
                server = tc.get("server")
                tool_name = tc.get("name")
                args = tc.get("arguments", {})
                
                print(f"[DEBUG] Executing {server}.{tool_name} with {args}")
                result = self.execute_tool(server, tool_name, args)
                print(f"[DEBUG] Result: {result[:200] if isinstance(result, str) else result}...")
                results.append(result)
            
            results_str = "\n\n".join(results)
            current_prompt = (
                f"User question: {user_message}\n\n"
                f"Tool execution results from iteration {iteration}:\n{results_str}\n\n"
                f"If you need more information, make another tool call. "
                f"Otherwise, provide a complete and helpful answer."
            )
        
        if tool_calls:
            response = self.query_llm(
                f"User question: {user_message}\n\nTool execution results:\n{results_str}\n\n"
                f"Please provide a helpful answer based on these results."
            )
        
        if not response:
            response = "I wasn't able to find any information. Please try again."
        
        # Maintain conversation history
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": response})
        
        # Keep only last N messages
        if len(self.conversation_history) > self.max_history_messages * 2:
            self.conversation_history = self.conversation_history[-(self.max_history_messages * 2):]
        
        return response


def run_chatto():
    """Run Chatto agent interactively."""
    agent = ChattoAgent()
    
    print("Chatto Agent initializing...")
    print(f"LM Studio: {agent.lmstudio_url}")
    print(f"MCP Gateway: {agent.mcp_gateway_url}")
    
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
            print(f"Error: {e}\n")


if __name__ == "__main__":
    run_chatto()