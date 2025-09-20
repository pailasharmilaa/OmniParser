"""
loop.py -Agentic sampling loop that calls the Anthropic API and local implenmentation of anthropic-defined computer use tools.
"""
from collections.abc import Callable
from enum import StrEnum
import asyncio
from anthropic import APIResponse
from anthropic.types import (
    TextBlock,
)
from anthropic.types.beta import (
    BetaContentBlock,
    BetaMessage,
    BetaMessageParam
)
from tools import ToolResult

from agent.llm_utils.omniparserclient import OmniParserClient
from agent.anthropic_agent import AnthropicActor
from agent.vlm_agent import VLMAgent
from executor.anthropic_executor import AnthropicExecutor
from twisted.internet.defer import inlineCallbacks, returnValue
import traceback
BETA_FLAG = "computer-use-2024-10-22"

class APIProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    OPENAI = "openai"
    AZURE = "azure"


PROVIDER_TO_DEFAULT_MODEL_NAME: dict[APIProvider, str] = {
    APIProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
    APIProvider.BEDROCK: "anthropic.claude-3-5-sonnet-20241022-v2:0",
    APIProvider.VERTEX: "claude-3-5-sonnet-v2@20241022",
    APIProvider.OPENAI: "gpt-4o",
    APIProvider.AZURE: "gpt-4o"
}

@inlineCallbacks
def sampling_loop_sync(
    *,
    model: str,
    provider: APIProvider | None,
    messages: list[BetaMessageParam],
    output_callback: Callable[[BetaContentBlock], None],
    tool_output_callback: Callable[[ToolResult, str], None],
    api_response_callback: Callable[[APIResponse[BetaMessage]], None],
    api_key: str,
    only_n_most_recent_images: int | None = 2,
    max_tokens: int = 4096,
    omniparser_url: str,
    azure_resource_name: str = None,
    user_id: str = None,
    prompt_id: str = None,
    cancellation_token: dict = None
):
    """
    Synchronous agentic sampling loop for the assistant/tool interaction of computer use.
    """
    print('in sampling_loop_sync, model:', model)

    # Check if we should stop before doing anything
    # Add this check to each cancellation point in sampling_loop_sync
    task_key = f"{user_id}_{prompt_id}"
    task_tokens = cancellation_token.get("task_tokens", {})
    if (cancellation_token and cancellation_token.get("stop")) or \
    (task_key in task_tokens and task_tokens[task_key].get("stop")):
        print(f"Cancellation requested for task {task_key}")
        returnValue(None)

    omniparser_client = OmniParserClient(url=f"http://{omniparser_url}/parse/")

    if user_id and prompt_id:
        omniparser_client.configure(user_id = user_id, prompt_id = prompt_id)

    if model == "claude-3-5-sonnet-20241022":
        # Register Actor and Executor
        actor = AnthropicActor(
            model=model, 
            provider=provider,
            api_key=api_key, 
            api_response_callback=api_response_callback,
            max_tokens=max_tokens,
            only_n_most_recent_images=only_n_most_recent_images,
            user_id=user_id,
            prompt_id = prompt_id
        )
    elif model in set(["omniparser + gpt-4o", "omniparser + o1", "omniparser + o3-mini", "omniparser + R1", "omniparser + qwen2.5vl"]):
        actor = VLMAgent(
            model=model,
            provider=provider,
            api_key=api_key,
            api_response_callback=api_response_callback,
            output_callback=output_callback,
            max_tokens=max_tokens,
            only_n_most_recent_images=only_n_most_recent_images,
            azure_resource_name=azure_resource_name if provider == APIProvider.AZURE else None,
            user_id = user_id,
            prompt_id = prompt_id
        )
    else:
        raise ValueError(f"Model {model} not supported")
    executor = AnthropicExecutor(
        output_callback=output_callback,
        tool_output_callback=tool_output_callback,
        user_id = user_id,
        prompt_id = prompt_id
    )
    print(f"Model Inited: {model}, Provider: {provider}")
    
    tool_result_content = None
    
    print(f"Start the message loop. User messages: {messages}")

    # Check if we should stop again
    task_key = f"{user_id}_{prompt_id}"
    task_tokens = cancellation_token.get("task_tokens", {})
    if (cancellation_token and cancellation_token.get("stop")) or \
    (task_key in task_tokens and task_tokens[task_key].get("stop")):
        print(f"Cancellation requested for task {task_key}")
        returnValue(None)
    
    if model == "claude-3-5-sonnet-20241022": # Anthropic loop
        while True:
            # Check cancellation before getting screen info
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None)
            parsed_screen = yield omniparser_client() # parsed_screen: {"som_image_base64": dino_labled_img, "parsed_content_list": parsed_content_list, "screen_info"}

            # Check cancellation after getting screen info
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None) 

            screen_info_block = TextBlock(text='Below is the structured accessibility information of the current UI screen, which includes text and icons you can operate on, take these information into account when you are making the prediction for the next action. Note you will still need to take screenshot to get the image: \n' + parsed_screen['screen_info'], type='text')
            screen_info_dict = {"role": "user", "content": [screen_info_block]}
            messages.append(screen_info_dict)

            # Check cancellation before calling model
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None)

            tools_use_needed = actor(messages=messages)

            # Check cancellation after model response
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None)

            for (message, _), tool_result_content in executor(tools_use_needed, messages):
                # Check cancellation during execution
                task_tokens = cancellation_token.get("task_tokens", {})
                if (cancellation_token and cancellation_token.get("stop")) or \
                (task_key in task_tokens and task_tokens[task_key].get("stop")):
                    print(f"Cancellation requested for task {task_key}")
                    returnValue(None)
                yield message
        
            if not tool_result_content:
                return messages

            messages.append({"content": tool_result_content, "role": "user"})
    
    elif model in set(["omniparser + gpt-4o", "omniparser + o1", "omniparser + o3-mini", "omniparser + R1", "omniparser + qwen2.5vl"]):
        while True:

            # Check cancellation before getting screen info
            task_key = f"{user_id}_{prompt_id}"
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None)

            parsed_screen = yield omniparser_client()

            # Check cancellation after getting screen info
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None) 

            result = yield actor(messages=messages, parsed_screen=parsed_screen)

            # Check cancellation after model response
            task_tokens = cancellation_token.get("task_tokens", {})
            if (cancellation_token and cancellation_token.get("stop")) or \
            (task_key in task_tokens and task_tokens[task_key].get("stop")):
                print(f"Cancellation requested for task {task_key}")
                returnValue(None)

            tools_use_needed, vlm_response_json = result

            # Add task completion detection
            if vlm_response_json and isinstance(vlm_response_json, dict):
                # Check if the response detection indicates task completion
                next_action = vlm_response_json.get("Next Action")
                if next_action == "None" or not next_action:
                    print("Task Completed. Stopping loop.")
                    returnValue(messages)

            # Modified executor handling to properly work with Deferreds
            try:
                # Pass the executor call to the inlineCallbacks decorator
                # Which will handle yielding and resuming around Deferreds
                executor_generator = executor(tools_use_needed, messages)

                # Process items from the generator
                while True:

                    # Check cancellation during execution
                    task_tokens = cancellation_token.get("task_tokens", {})
                    if (cancellation_token and cancellation_token.get("stop")) or \
                    (task_key in task_tokens and task_tokens[task_key].get("stop")):
                        print(f"Cancellation requested for task {task_key}")
                        returnValue(None) 
                    try:
                        # Get the next item, potentially a Deferred
                        item = yield next(executor_generator)

                        # If it's a tuple with ([None, None], tool_result_content) structure
                        if isinstance(item, tuple) and len(item) == 2:
                            _, tool_result_content = item
                    except StopIteration:
                        break
                    except Exception as e:
                        print(f"Error processing executor result: {e}")
                        print(f"{traceback.format_exc()}")
                        break
            except Exception as e:
                print(f"Error executing tools: {e}")  

            if not tool_result_content:
                returnValue(messages) 