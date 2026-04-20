import os
import json
import time
from google import genai
from google.genai import types

# Model definitions based on Terminal mapping
MODEL_MAPPING = {
    "PRO": ["gemini-pro-latest", "gemini-2.5-pro"],
    "THINKING":  ["gemini-pro-latest", "gemini-2.5-pro"],
    "FAST": ["gemini-flash-latest", "gemini-2.5-flash"]
}

class AgentFramework:
    def __init__(self):
        # We rely on GEMINI_API_KEY environment variable being set
        self.client = genai.Client()
        self.agents = {}

    def load_system_instruction(self, json_file_path):
        """Loads a JSON file and formats it as a string for the system prompt."""
        if not os.path.exists(json_file_path):
            raise FileNotFoundError(f"Missing instruction file: {json_file_path}")
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2)

    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Attempts to generate a response using the primary model, falling back if rate limited."""
        models = MODEL_MAPPING.get(mode, MODEL_MAPPING["PRO"])
        
        last_error = None
        for model_name in models:
            print(f"[System] Attempting with model: {model_name}...")
            try:
                # Add thinking config if requested
                thinking_config = None
                if mode == "THINKING":
                    # Thinking feature might need specific config depending on SDK version
                    # We will try standard config first, some models have thinking built-in by name
                    pass
                
                config_args = {
                    "system_instruction": instruction,
                    "temperature": 1.0,  # Defined in terminal matrix
                }
                
                if tools:
                    config_args["tools"] = tools

                config = types.GenerateContentConfig(**config_args)
                
                # We're using standard generate_content for isolated queries
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                return response
            except Exception as e:
                error_msg = str(e)
                last_error = e
                
                # Check for rate limits and back off
                if "429" in error_msg or "quota" in error_msg.lower():
                    # The error usually says "Please retry in X.Xs"
                    print(f"[System] API Rate limit hit for {model_name}. Waiting 32 seconds before retrying...")
                    time.sleep(32)
                    try:
                        print(f"[System] Retrying model {model_name} after backoff...")
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=config
                        )
                        return response
                    except Exception as retry_e:
                        print(f"[System] Retry failed for {model_name}: {retry_e}")
                        last_error = retry_e
                        continue
                        
                elif "not found" in error_msg.lower():
                    print(f"[System] Model {model_name} not found. Skipping...")
                    continue
                else:
                    print(f"[System] Warning: {model_name} failed with error: {e}")
                    raise e
                    
        raise RuntimeError(f"All models for mode {mode} failed. Last error: {last_error}")

    def create_agent_tool(self, name, json_file, mode="PRO", agent_tools=None):
        """
        Creates a callable function that acts as a sub-agent tool.
        This function can be passed to the Terminal orchestrator as a tool.
        """
        instruction = self.load_system_instruction(json_file)
        
        def call_subagent(query: str) -> str:
            print(f"\n[Orchestrator -> {name}] Delegating query: {query[:100]}...")
            response = self.generate_response_with_fallback(
                prompt=query, 
                instruction=instruction, 
                mode=mode,
                tools=agent_tools
            )
            print(f"[{name}] Responded.")
            if response.text:
                return response.text
            return "No response generated."
            
        # Naming the function explicitly for tool discovery
        call_subagent.__name__ = f"ask_{name.lower().replace(' ', '_')}"
        call_subagent.__doc__ = f"Use this tool to ask the {name} sub-agent a query. Pass your request as a string."
        
        return call_subagent

