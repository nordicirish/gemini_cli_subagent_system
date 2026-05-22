import os
import sys

from agent_framework import AgentFramework
import cloud_sync

# Ensure files exist
with open("decision_log.json", "w") as f:
    f.write('{"test": "data"}')

try:
    framework = AgentFramework()
    daemon = cloud_sync.CloudSyncDaemon(framework.client)
    res = daemon.sync_file_to_cloud("decision_log.json")
    if res:
        print(f"File URI: {res.uri}")
        print(f"File Name: {res.name}")
    else:
        print("Sync returned None.")
    
    # Check manifest
    print(daemon.manifest)
except Exception as e:
    print(f"Error testing cloud sync: {e}")
