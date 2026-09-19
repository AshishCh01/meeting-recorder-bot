"""
The five Ask AI tools as OpenAI-style function schemas - what the Groq
fallback is offered (run_groq_chat_stream's tool_schemas), mirroring
GROQ_TOOLS for the per-meeting chat.

The descriptions are what the model reasons over when it picks a tool, so
this is the one place they are written. user_id and the timezone are not
parameters: the service binds them, and the model can't see or change them.
"""

_DATE = "YYYY-MM-DD, in the user's local calendar."


def _date(which: str) -> dict:
    return {"type": "string", "description": f"{which} ({_DATE})"}


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_MEETING_ID = {
    "type": "string",
    "description": "The meeting's id, exactly as another tool returned it.",
}

ASK_AI_TOOLS = [
    _function(
        "list_meetings",
        (
            "Lists the user's completed meetings, newest first, optionally within a date range "
            "and/or with a title containing some text. Call this FIRST for any question about a "
            "date or period ('what was discussed on 15 Sep', 'meetings last week', 'in August'). "
            "The level of detail depends on how many match: 'detailed' includes a short summary "
            "of each meeting, 'compact' its first few key points, 'titles' only title and date. "
            "If truncated is true, only the most recent meetings were returned. Omit both dates "
            "to list across all time. For one day, pass the same date as start_date and end_date."
        ),
        {
            "start_date": _date("First day of the range, inclusive"),
            "end_date": _date("Last day of the range, inclusive"),
            "title_query": {
                "type": "string",
                "description": "Only meetings whose title contains this text (case-insensitive).",
            },
        },
        [],
    ),
    _function(
        "find_meetings_by_topic",
        (
            "Ranks the user's meetings by how well their title, summary and key points match a "
            "topic, best first. Use this for 'which meeting discussed X?' or 'when did we talk "
            "about X?'. Returns up to 10 meetings with a short summary each; follow up with "
            "get_meeting_details or search_across_meetings for specifics."
        ),
        {
            "query": {
                "type": "string",
                "description": "The topic to match, in natural language, e.g. 'pricing for the enterprise plan'.",
            },
            "start_date": _date("Optional: only meetings on or after this day"),
            "end_date": _date("Optional: only meetings on or before this day"),
        },
        ["query"],
    ),
    _function(
        "search_across_meetings",
        (
            "Semantically searches the transcripts of the user's meetings and returns the best "
            "matching passages with speaker names, MM:SS timestamps and the meeting each came "
            "from. Use this for exact wording or specific details ('what exactly did Rahul say "
            "about the budget?'). Without a date range, only the user's most recent meetings are "
            "searched, so pass a range when the question implies one."
        ),
        {
            "query": {
                "type": "string",
                "description": "A focused description of what to find, e.g. 'Rahul on the Q3 budget'.",
            },
            "start_date": _date("Optional: only meetings on or after this day"),
            "end_date": _date("Optional: only meetings on or before this day"),
        },
        ["query"],
    ),
    _function(
        "get_meeting_details",
        (
            "Returns one meeting's full summary, key points and conclusion, with its title, date, "
            "platform and duration. Use it when the user asks about a specific meeting that "
            "list_meetings or find_meetings_by_topic has already returned."
        ),
        {"meeting_id": _MEETING_ID},
        ["meeting_id"],
    ),
    _function(
        "get_meeting_action_items",
        (
            "Returns one meeting's action items: each task with its owner, due date and timestamp "
            "if known. Use it for tasks, decisions and follow-ups from a specific meeting."
        ),
        {"meeting_id": _MEETING_ID},
        ["meeting_id"],
    ),
]
