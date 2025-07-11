from rocket.graph import build_graph, AgentState
from langchain_core.messages import AIMessageChunk, HumanMessage
from typing import AsyncGenerator, Any
from langgraph.graph.state import CompiledStateGraph
from colorama import Fore, Style


async def stream_graph_responses(
        input: AgentState,
        graph: CompiledStateGraph,
        **kwargs
        ) -> AsyncGenerator[str, None]:
    """Asynchronously stream the result of the graph run.

    Args:
        input: The input to the graph.
        graph: The compiled graph.
        **kwargs: Additional keyword arguments.

    Returns:
        str: The final LLM or tool call response
    """
    async for message_chunk, metadata in graph.astream(
        input=input,
        stream_mode="messages",
        **kwargs
        ):
        tool_call_str = ""
        if isinstance(message_chunk, AIMessageChunk):
            if message_chunk.response_metadata:
                finish_reason = message_chunk.response_metadata.get("finish_reason", "")
                if finish_reason == "tool_calls":
                    yield "\n\n"

            if message_chunk.tool_call_chunks:
                tool_chunk = message_chunk.tool_call_chunks[0]

                tool_name = tool_chunk.get("name", "")
                args = tool_chunk.get("args", "")

                if tool_name:
                    tool_call_str = f"\n\n< TOOL CALL: {tool_name} >\n\n"
                if args:
                    tool_call_str = args

                yield tool_call_str
            else:
                if isinstance(message_chunk.content, str):
                    yield message_chunk.content
                else:
                    yield str(message_chunk.content)
            continue


async def main():
    try:
        graph = await build_graph()

        config = {
            "configurable": {
                "thread_id": "1"
            }
        }

        graph_input = AgentState(
            messages=[
                HumanMessage(content="Briefly introduce yourself and offer to help me.")
            ]
        )

        while True:
            print(f"\n ---- 🚀 Rocket ---- \n")
            async for response in stream_graph_responses(graph_input, graph, config=config):
                print(Fore.CYAN + response + Style.RESET_ALL, end="", flush=True)

            user_input = input("\n\nUser ('exit' to quit): ")
            if user_input.lower() in ["exit", "quit"]:
                print("\n\nExit command received. Exiting...\n\n")
                break
            graph_input = AgentState(
                messages=[
                    HumanMessage(content=user_input)
                ]
            )

            print(f"\n\n ----- 🥷 Human ----- \n\n{user_input}\n")

    except Exception as e:
        print(f"Error: {type(e).__name__}: {str(e)}")
        raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
