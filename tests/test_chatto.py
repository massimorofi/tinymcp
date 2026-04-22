import json

from agents.chatto.chatto import ChattoAgent


def test_parse_tool_call_variants():
    agent = ChattoAgent()
    response = (
        '<|tool_call>call: mcp-cluster-wikipedia-mcp-1\n'
        'name: wikipedia_get_summary\n'
        'arguments: {"title": "Italy"}<tool_call|>'
    )

    tool_calls = agent._parse_tool_calls(response)

    assert len(tool_calls) == 1
    assert tool_calls[0]["server"] == "mcp-cluster-wikipedia-mcp-1"
    assert tool_calls[0]["name"] == "wikipedia_get_summary"
    assert tool_calls[0]["arguments"] == {"title": "Italy"}
