import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "python"))

import json
import datetime
from agent_framework import AgentFramework
import agent_framework
import tools
import web_server

def test_thinking():
    print("Testing Orchestrator on Scout query...")
    
    # Initialize framework
    framework = AgentFramework()
    framework.cache_disabled = True
    
    # Use the live query
    qp_scout_prompt = 'SYSTEM DIRECTIVE: SCOUT INTELLIGENCE SCAN. Using the live DATA_PACKET just injected, evaluate the non-portfolio tickers to identify top entry candidates. Assess risk/reward vs current regime. Output a clear, human-readable summary. DO NOT output a JSON EXECUTION_PAYLOAD.'
    
    # Use available model
    model = "gemini-2.5-flash"
    print(f"Creating chat session with {model}...")
    
    chat = framework.client.chats.create(
        model=model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=web_server.terminal_instruction,
            temperature=1.0,
            tools=web_server.terminal_tools
        )
    )
    
    current_message = qp_scout_prompt
    turn_count = 0
    all_text = []
    
    tool_map = {f.__name__: f for f in web_server.terminal_tools}
    
    while True:
        turn_count += 1
        print(f"\n--- STARTING TURN {turn_count} ---")
        try:
            response = chat.send_message(current_message)
            print("Response text length:", len(response.text) if hasattr(response, 'text') and response.text else 0)
            
            # Print response parts
            if response.candidates:
                parts = response.candidates[0].content.parts
                print(f"Parts count: {len(parts)}")
                for i, p in enumerate(parts):
                    print(f"Part {i}: type(text)={p.text is not None}, type(function_call)={p.function_call is not None}")
                    if p.text:
                        print(f"  Text excerpt: {p.text.strip()[:100]}...")
            
            # Capture any text in this turn
            try:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        txt = part.text.strip()
                        if txt:
                            print(f"Captured {len(txt)} chars of text.")
                            all_text.append(txt)
            except Exception as e:
                print("Text capture warning:", e)
                if hasattr(response, 'text') and response.text:
                    all_text.append(response.text)
            
            # Manual function call handling
            if response.function_calls:
                print(f"Turn {turn_count} requested {len(response.function_calls)} tools.")
                tool_responses = []
                for call in response.function_calls:
                    name = call.name
                    args = call.args or {}
                    print(f"Executing Tool: {name} with args: {args}")
                    
                    if name in tool_map:
                        try:
                            result = tool_map[name](**args)
                            print(f"  Tool success: {str(result)[:100]}...")
                            tool_responses.append(agent_framework.types.Part.from_function_response(
                                name=name, response={'result': result}
                            ))
                        except Exception as te:
                            print(f"  Tool failed: {te}")
                            tool_responses.append(agent_framework.types.Part.from_function_response(
                                name=name, response={'error': str(te)}
                            ))
                    else:
                        print(f"  Tool not found: {name}")
                        tool_responses.append(agent_framework.types.Part.from_function_response(
                            name=name, response={'error': 'Tool not found'}
                        ))
                
                current_message = tool_responses
                continue
            
            break
        except Exception as e:
            print("Error during turn:", e)
            break
            
    print("\n--- TEST COMPLETE ---")
    print("Total captured texts:", len(all_text))
    if all_text:
        print("Final output:", "\n\n---\n\n".join(all_text)[:200] + "...")
    else:
        print("WARNING: No text captured!")

if __name__ == "__main__":
    test_thinking()
