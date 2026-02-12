import json

def get_leaf_paths(obj, path=''):
    """Recursively find all leaf paths and values in a JSON object."""
    paths = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            paths.update(get_leaf_paths(value, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            paths.update(get_leaf_paths(item, f"{path}[{i}]"))
    else:
        paths[path] = obj
    return paths

with open('c:/github/gem_python/Instructions.json', 'r') as f:
    new_data = json.load(f)

with open('c:/github/gem_python/instructions_original.json', 'r') as f:
    original_data = json.load(f)

new_paths = get_leaf_paths(new_data)
original_paths = get_leaf_paths(original_data)

# Invert the dictionaries to map values to paths
new_values_to_paths = {str(v): k for k, v in new_paths.items()}
original_values_to_paths = {str(v): k for k, v in original_paths.items()}

new_values = set(new_values_to_paths.keys())
original_values = set(original_values_to_paths.keys())

missing_values = original_values - new_values
added_values = new_values - original_values

if not missing_values and not added_values:
    print("All values from the original file are present in the new file.")
else:
    if missing_values:
        print("Missing values:")
        for value in sorted(list(missing_values)):
            print(f"- Path: {original_values_to_paths.get(value, 'N/A')}, Value: {value}")
    if added_values:
        print("\nAdded values:")
        for value in sorted(list(added_values)):
            print(f"- Path: {new_values_to_paths.get(value, 'N/A')}, Value: {value}")