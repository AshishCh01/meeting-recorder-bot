"""
Ask AI Phase 3 - the tool schemas match the tools.

ASK_AI_TOOLS is what the model is told it can call; a parameter the schema
names but the function doesn't take would fail at dispatch, and one the
function takes but the schema hides can never be used.
"""
import inspect

from app.ask_ai import tools
from app.ask_ai.tools import ASK_AI_TOOLS

BOUND_BY_THE_SERVICE = {"user_id", "tz_name"}


def test_every_schema_names_a_tool_and_matches_its_parameters():
    for schema in ASK_AI_TOOLS:
        function = schema["function"]
        tool = getattr(tools, function["name"])
        signature = inspect.signature(tool).parameters

        model_params = set(signature) - BOUND_BY_THE_SERVICE
        required = {name for name, p in signature.items() if p.default is inspect.Parameter.empty} - BOUND_BY_THE_SERVICE

        assert set(function["parameters"]["properties"]) == model_params, function["name"]
        assert set(function["parameters"]["required"]) == required, function["name"]
        assert BOUND_BY_THE_SERVICE <= set(signature), f"{function['name']} must be scoped to the user"


def test_the_model_can_never_pass_a_user_or_timezone():
    for schema in ASK_AI_TOOLS:
        assert not BOUND_BY_THE_SERVICE & set(schema["function"]["parameters"]["properties"])


def test_there_are_five_tools_with_descriptions():
    names = [schema["function"]["name"] for schema in ASK_AI_TOOLS]
    assert names == [
        "list_meetings", "find_meetings_by_topic", "search_across_meetings",
        "get_meeting_details", "get_meeting_action_items",
    ]
    assert all(len(schema["function"]["description"]) > 50 for schema in ASK_AI_TOOLS)


def test_dates_are_described_as_local_iso_dates():
    for schema in ASK_AI_TOOLS:
        for name, prop in schema["function"]["parameters"]["properties"].items():
            if name.endswith("_date"):
                assert "YYYY-MM-DD, in the user's local calendar" in prop["description"]
