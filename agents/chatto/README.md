# Chatto Agent

An intelligent AI assistant that connects to LM Studio and uses available MCP (Model Context Protocol) tools to answer questions with an agentic approach.

## Overview

Chatto is an AI agent that:

1. **Discovers** available MCP tools from all connected servers
2. **Plans** which tools to use based on the user's question
3. **Executes** the tools to gather information
4. **Composes** a helpful answer using the results

## Architecture

```
User Question → Chatto → LM Studio (with Skills) → Plan Tools → Execute → Process Results → Final Answer
                        ↑
                   chatto_skills.md
```

## How It Works

### 1. Skills Loading

The agent loads its instructions from `chatto_skills.md`. This file contains:

- The agent's role and persona
- The tool call format specification
- Examples of how to call each MCP server

```python
SKILLS_FILE = Path(__file__).parent / "chatto_skills.md"
self.skills = load_skills()  # Loads from markdown file
```

### 2. Tool Discovery

When a user asks a question, Chatto first discovers all available tools:

```python
def get_servers(self):
    # Get list of MCP servers from gateway
    
def get_tools(self, server_name):
    # Execute tools/list on each server
    # Returns: [{"name": "get_stories", "description": "..."}, ...]
```

The tools are formatted as context for the LLM:
```
Server: tinymcp-hackernews-mcp-1, Tool: get_stories, Args: story_type,num_stories
Server: tinymcp-google-flights-1, Tool: get_flights_on_date, Args: from,to,date
```

### 3. Planning with LM Studio

The skills file tells the LLM to respond with XML tool calls:

```
<tool_call>
tool: tinymcp-hackernews-mcp-1
name: get_stories
arguments: {"story_type": "top", "num_stories": 10}
</tool_call>
```

The LLM analyzes the question and available tools, then creates a plan.

### 4. Tool Execution

Chatto parses the tool calls from the LLM response and executes them:

```python
def parse_tool_calls(self, text):
    # Extracts <tool_call>...</tool_call> blocks
    
def execute_tool(self, server_name, tool_name, arguments):
    # Calls MCP Gateway /execute endpoint
    # Returns: {"result": {...}}
```

### 5. Answer Composition

Tool results are passed back to the LLM for final processing:

```python
final_prompt = f"{user_message}\n\nTool results:\n{results_str}\n\nProvide answer"
response = self.query_llm(final_prompt)
```

## Skills File Format

The `chatto_skills.md` file uses Markdown with XML-style tags:

- `<tool_call>` - Start of tool execution request
- `tool:` - Server container name (e.g., `tinymcp-hackernews-mcp-1`)
- `name:` - Tool name to execute
- `arguments:` - JSON object with tool arguments
- `</tool_call>` - End of tool execution request

## Available MCP Servers

| Server | Container | Tools |
|-------|-----------|-------|
| HackerNews | tinymcp-hackernews-mcp-1 | get_stories, get_story_info, search_stories, get_user_info |
| Google Flights | tinymcp-google-flights-1 | get_flights_on_date, get_round_trip_flights, find_all_flights_in_range |
| Desktop Commander | tinymcp-desktop-commander-1 | get_config, set_config_value, read_file, write_file, etc. |

## Running

```bash
cd agents/chatto
source ../../venv/bin/activate
python chatto.py
```

Or as Docker:

```bash
docker build -t chatto .
docker run -it --network host chatto
```

## Environment Variables

- `LMSTUDIO_URL` - LM Studio URL (default: `http://127.0.0.1:1234`)
- `MCP_GATEWAY_URL` - MCP Gateway URL (default: `http://localhost:8000`)

## Example Conversation

```
You: Show me top 10 hackernews stories
Chatto: <tool_call>
tool: tinymcp-hackernews-mcp-1
name: get_stories
arguments: {"story_type": "top", "num_stories": 10}
</tool_call>
[Tool executed]
Chatto: Here are the top 10 Hacker News stories:
1. "The Great..." by user123 (245 points)
2. "Another..." by user456 (189 points)
...
```

## Customizing Skills

Edit `chatto_skills.md` to change:
- The agent's persona
- Tool call format
- Available examples
- Response style