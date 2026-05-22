import os
import time
import hashlib
import json
import threading
from google import genai
from google.genai import types

class CloudSyncDaemon:
    def __init__(self, client: genai.Client, manifest_path="cloud_sync_manifest.json"):
        self.client = client
        self.manifest_path = manifest_path
        self.manifest = self._load_manifest()
        self._running = False

    def _load_manifest(self) -> dict:
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_manifest(self):
        try:
            with open(self.manifest_path, "w") as f:
                json.dump(self.manifest, f, indent=2)
        except Exception as e:
            print(f"[CloudSync] Error saving manifest: {e}")

    def _get_file_hash(self, file_path: str) -> str:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()

    def sync_file_to_cloud(self, file_path: str, mime_type="text/plain"):
        """
        Synchronizes a local file to Gemini Files API.
        Only uploads if:
        1. File has never been uploaded.
        2. Local file content hash has changed.
        3. The uploaded file is close to its 48-hour expiration.
        """
        if not os.path.exists(file_path):
            print(f"[CloudSync] Warning: Local file {file_path} not found.")
            return None

        current_hash = self._get_file_hash(file_path)
        file_meta = self.manifest.get(file_path)
        
        should_upload = True
        if file_meta:
            # Check hash parity and expiration (leave 4-hour buffer before 48h limit)
            # 44 hours = 158400 seconds
            time_since_upload = time.time() - file_meta.get("uploaded_timestamp", 0)
            if file_meta.get("local_hash") == current_hash and time_since_upload < 158400:
                should_upload = False

        if should_upload:
            print(f"[CloudSync] Uploading {file_path} to Gemini Files API...")
            # If a stale file exists, clean it up first
            if file_meta and file_meta.get("cloud_name"):
                try:
                    self.client.files.delete(name=file_meta["cloud_name"])
                except Exception as e:
                    pass # It might have already expired

            try:
                uploaded = self.client.files.upload(file=file_path, config={"mime_type": mime_type})
                self.manifest[file_path] = {
                    "local_hash": current_hash,
                    "cloud_uri": uploaded.uri,
                    "cloud_name": uploaded.name,
                    "uploaded_timestamp": time.time(),
                    "expires_at": time.time() + 172800 # 48 hours
                }
                self._save_manifest()
                print(f"[CloudSync] Success: {file_path} -> {uploaded.name}")
                return uploaded
            except Exception as e:
                # E.g. Quota exceeded or network error
                print(f"[CloudSync] Upload Failed for {file_path}: {e}")
                return None
        else:
            # Reconstruct the file reference from existing cloud name
            try:
                return self.client.files.get(name=file_meta["cloud_name"])
            except Exception as e:
                # If get fails (e.g., unexpectedly deleted), remove from manifest and retry
                print(f"[CloudSync] Cloud file lost, re-uploading {file_path}...")
                del self.manifest[file_path]
                return self.sync_file_to_cloud(file_path, mime_type)

    def get_cloud_file(self, file_path: str):
        """Helper to get a synced file object without blocking for upload loop"""
        return self.sync_file_to_cloud(file_path)

    def start_background_sync(self, files_to_sync: list, interval_seconds: int = 3600):
        """
        Runs continuously in a background thread, syncing files periodically.
        """
        self._running = True
        print(f"[CloudSync] Daemon started. Tracking {len(files_to_sync)} files...")
        
        while self._running:
            for f in files_to_sync:
                try:
                    self.sync_file_to_cloud(f)
                except Exception as e:
                    print(f"[CloudSync] Background sync error on {f}: {e}")
            
            # Sleep in small increments to allow clean shutdown if needed
            for _ in range(interval_seconds):
                if not self._running:
                    break
                time.sleep(1)

    def stop(self):
        self._running = False
