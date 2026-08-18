import inspect
import asyncio
from collections import OrderedDict
import httpx
from google import genai
from google.genai import types, errors

from app.config import settings
from app.rag.agent import INSTRUCTION
from app.rag.tools import get_meeting_summary, get_action_items, search_by_speaker, search_transcript

class LRUSessionCache:
    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        # Maps key -> (asyncio.Lock(), list[types.Content])
        self.cache: OrderedDict[str, tuple[asyncio.Lock, list[types.Content]]] = OrderedDict()
        self.lock = asyncio.Lock()

    async def get_session(self, user_id: str, meeting_id: str, session_id: str) -> tuple[asyncio.Lock, list[types.Content]]:
        key = f"{user_id}:{meeting_id}:{session_id}"
        async with self.lock:
            if key not in self.cache:
                if len(self.cache) >= self.capacity:
                    self.cache.popitem(last=False)  # Remove oldest (FIFO/LRU)
                self.cache[key] = (asyncio.Lock(), [])
            else:
                self.cache.move_to_end(key)
            return self.cache[key]

# Global session cache instance
_session_cache = LRUSessionCache(capacity=500)

# We create the genai client here
client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=120_000))

class DummyToolContext:
    def __init__(self, meeting_id: str, user_id: str):
        self.state = {"meeting_id": meeting_id, "user_id": user_id}

def _session_id(meeting_id: str, session_id: str | None) -> str:
    return session_id or f"meeting-{meeting_id}"

async def ask_question(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None) -> dict:
    if not user_id:
        raise ValueError("user_id must be provided to scope the agent's context.")
        
    sid = _session_id(meeting_id, session_id)
    session_lock, history = await _session_cache.get_session(user_id, meeting_id, sid)
    
    async with session_lock:
        # Truncate history to preserve context window limits
        if len(history) > 20:
            # Modify the list in-place to retain the reference inside the cache
            history[:] = history[-20:]

        # Append the user's question
        history.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
    
        # Define tools without ToolContext for Gemini
        def _get_meeting_summary() -> dict:
            """
            Returns the high-level summary of the meeting, key points discussed, 
            and the final conclusion/resolution.
    
            Use this FIRST for broad questions like "what was this meeting about",
            "what were the main topics", or "how did it conclude".
            """
            return get_meeting_summary(DummyToolContext(meeting_id, user_id))
    
        def _get_action_items() -> list[dict]:
            """
            Returns the concrete tasks, decisions, or follow-ups mentioned in the meeting,
            including the owner and timestamp if available.
    
            Use this specifically when asked about action items, tasks, or follow-ups.
            """
            return get_action_items(DummyToolContext(meeting_id, user_id))
    
        def _search_by_speaker(speaker_name: str) -> dict:
            """
            Searches the transcript for everything said by a specific person.
    
            Use this when the user asks "what did Alice say", "find quotes by Bob",
            or "did John mention anything?".
            
            Args:
                speaker_name: The name of the speaker to search for.
            """
            return search_by_speaker(speaker_name, DummyToolContext(meeting_id, user_id))
    
        def _search_transcript(query: str) -> dict:
            """
            Semantically searches this meeting's full transcript for passages
            relevant to `query`, and returns the matching passages with their
            speakers and MM:SS timestamps.
    
            Use this for specific questions the summary can't answer: exact wording,
            or anything tied to a specific moment or topic in the conversation.
            Call it more than once with reworded queries if the first results don't
            fully answer the question.
    
            Args:
                query: A focused natural-language description of what to find,
                    e.g. "budget concerns raised about the Q3 launch".
            """
            return search_transcript(query, DummyToolContext(meeting_id, user_id))
            
        available_tools = [_get_meeting_summary, _get_action_items, _search_by_speaker, _search_transcript]
        tools_used = []
    
        config = types.GenerateContentConfig(
            tools=available_tools,
            system_instruction=INSTRUCTION,
            temperature=0.0
        )
    
        MAX_TOOL_ITERATIONS = 6
        loop_count = 0

        while True:
            if loop_count >= MAX_TOOL_ITERATIONS:
                return {
                    "session_id": sid,
                    "answer": "I'm having trouble finding the exact information you requested. Could you try rephrasing your question?",
                    "tools_used": tools_used,
                }
            loop_count += 1

            chat_max_retries = 3
            for attempt in range(chat_max_retries):
                try:
                    response = await client.aio.models.generate_content(
                        model=settings.rag_agent_model,
                        contents=history,
                        config=config,
                    )
                    break # Success!
                except (errors.APIError, httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                    if attempt == chat_max_retries - 1:
                        return {
                            "session_id": sid,
                            "answer": "The AI service is currently experiencing high load or rate limits. Please try again in a few moments.",
                            "tools_used": tools_used,
                        }
                    await asyncio.sleep((2 ** attempt) + 0.5)
    
            if not response.candidates:
                raise ValueError("No candidates returned from the model.")
                
            candidate = response.candidates[0]
            # Append the model's response to history
            history.append(candidate.content)
    
            function_calls = []
            for part in candidate.content.parts:
                if part.function_call:
                    function_calls.append(part.function_call)
    
            # If there are function calls, execute them and re-prompt
            if function_calls:
                responses = []
                for fc in function_calls:
                    func_name = fc.name
                    func_args = fc.args or {}
                    # Map the wrapped tool name to the original tool name to show cleanly in the UI
                    clean_name = func_name.lstrip("_")
                    tools_used.append(clean_name)
                    
                    # Execute matching tool
                    tool_result = {}
                    try:
                        if func_name == "_get_meeting_summary":
                            tool_result = _get_meeting_summary()
                        elif func_name == "_get_action_items":
                            tool_result = _get_action_items()
                        elif func_name == "_search_by_speaker":
                            tool_result = _search_by_speaker(func_args.get("speaker_name", ""))
                        elif func_name == "_search_transcript":
                            tool_result = _search_transcript(func_args.get("query", ""))
                        else:
                            tool_result = {"error": f"Unknown tool {func_name}"}
                    except Exception as e:
                        tool_result = {"error": str(e)}
    
                    # Build the response part
                    responses.append(types.Part.from_function_response(
                        name=func_name,
                        response={"result": tool_result}
                    ))
                
                # Append the function responses to history and loop to generate content again
                history.append(types.Content(role="user", parts=responses))
                continue
                
            # No function calls, meaning we have the final answer
            answer = ""
            for part in candidate.content.parts:
                if getattr(part, "text", None):
                    answer += part.text
    
            return {
                "session_id": sid,
                "answer": answer,
                "tools_used": tools_used,
            }
