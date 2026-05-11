import json
import os

def strip_scrutiny(obj):
    if isinstance(obj, list):
        for i in obj:
            strip_scrutiny(i)
    elif isinstance(obj, dict):
        obj.pop("scrutiny_audit", None)
        for v in obj.values():
            strip_scrutiny(v)

filename = 'local_ssot_shadow.json'
if os.path.exists(filename):
    with open(filename, 'r') as f:
        data = json.load(f)
    
    strip_scrutiny(data)
    
    # Remove EXECUTION_PAYLOAD
    data.pop("EXECUTION_PAYLOAD", None)
    if "mutable_state" in data:
        data["mutable_state"].pop("EXECUTION_PAYLOAD", None)
    
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)
    print("Successfully cleaned local_ssot_shadow.json")
else:
    print("File not found")
