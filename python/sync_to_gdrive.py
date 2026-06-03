import os
import sys
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Scopes required to manage files in Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def get_drive_service():
    """Authenticates the user and returns the Google Drive API service client."""
    creds = None
    token_path = os.path.join(BASE_DIR, 'token.json')
    creds_path = os.path.join(BASE_DIR, 'credentials.json')

    # Load cached tokens if available
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"Warning: Failed to load cached token: {e}")

    # If credentials are not valid or missing, perform the OAuth web flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Warning: Token refresh failed: {e}")
                creds = None

        if not creds:
            if not os.path.exists(creds_path):
                print(f"\n[ERROR] Missing 'credentials.json' in root directory: {BASE_DIR}")
                print("Please follow these steps to obtain credentials.json:")
                print("1. Go to Google Cloud Console (https://console.cloud.google.com).")
                print("2. Create a project, enable the 'Google Drive API'.")
                print("3. Go to 'Credentials' -> 'Create Credentials' -> 'OAuth client ID'.")
                print("4. Select Application Type: 'Desktop App', name it, and click Create.")
                print("5. Download the JSON, rename it to 'credentials.json', and save it to the root of this repository.")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Cache the credentials for future runs
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

def find_or_create_folder(service, folder_name, parent_id=None):
    """Finds a folder in Google Drive by name, creating it if it doesn't exist."""
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])

    if files:
        return files[0]['id']
    else:
        print(f"Folder '{folder_name}' not found. Creating it...")
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')

def upsert_file(service, local_file_path, filename, parent_folder_id):
    """Uploads a file to Google Drive and converts it into a native Google Doc."""
    if not os.path.exists(local_file_path):
        print(f"Local file not found: {local_file_path}")
        return

    # Google Docs do not use file extensions in their titles
    drive_filename = filename.replace('.md', '')

    # Check if the file already exists in this folder
    query = f"name = '{drive_filename}' and '{parent_folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])

    # Upload as plain text; Google Drive will convert it to a Doc on the fly
    media = MediaFileUpload(local_file_path, mimetype='text/plain', resumable=True)

    if files:
        # Update existing Google Doc
        file_id = files[0]['id']
        print(f"Updating existing Google Doc: {drive_filename} (ID: {file_id})")
        service.files().update(
            fileId=file_id,
            media_body=media
        ).execute()
    else:
        # Create new file and convert to Google Doc
        print(f"Uploading and converting to Google Doc: {drive_filename}")
        file_metadata = {
            'name': drive_filename,
            'parents': [parent_folder_id],
            'mimeType': 'application/vnd.google-apps.document'  # Force native Google Doc conversion
        }
        service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

def main():
    # Load configuration
    config_path = os.path.join(BASE_DIR, 'config.json')
    folder_name = "GeminiTradingSSoT"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)
                folder_name = config_data.get("GDRIVE_FOLDER_NAME", "GeminiTradingSSoT")
        except Exception as e:
            print(f"Warning: Failed to load config.json: {e}")

    print(f"--- Initializing Google Drive Rules Sync (Folder: {folder_name}) ---")
    try:
        service = get_drive_service()
    except Exception as e:
        print(f"[FATAL] Authentication failed: {e}")
        sys.exit(1)

    # 1. Resolve root folder ID: Using configured folder name
    root_folder_id = find_or_create_folder(service, folder_name)
    print(f"Root Folder ID: {root_folder_id}")

    # 2. Aggregate all instructions into a single master document
    master_doc_path = os.path.join(BASE_DIR, 'scratch', 'master_trading_knowledge.md')
    
    # Ensure scratch directory exists
    os.makedirs(os.path.dirname(master_doc_path), exist_ok=True)
    
    print("\nAggregating all instructions into a single Master Document...")
    with open(master_doc_path, 'w', encoding='utf-8') as master_file:
        master_file.write("# Master Trading Knowledge Document\n\n")
        master_file.write("This document contains the Single Source of Truth (SSoT) rules and all engine instructions.\n\n")
        
        # Append rules.md
        rules_local_path = os.path.join(BASE_DIR, 'gem_trading_rules', 'rules.md')
        if os.path.exists(rules_local_path):
            master_file.write("## 1. TRADING RULES (SSoT)\n\n")
            with open(rules_local_path, 'r', encoding='utf-8') as rules_file:
                content = rules_file.read()
                # Demote headers by prepending '##' to '#' lines to nest them under '## 1. TRADING RULES (SSoT)'
                demoted_lines = [('##' + line) if line.startswith('#') else line for line in content.splitlines()]
                master_file.write('\n'.join(demoted_lines))
            master_file.write("\n\n---\n\n")
            
        # Append engine instructions
        master_file.write("## 2. ENGINE INSTRUCTIONS\n\n")
        engine_dir = os.path.join(BASE_DIR, 'engine_instructions')
        if os.path.exists(engine_dir):
            for filename in sorted(os.listdir(engine_dir)):
                if filename.endswith('.md'):
                    local_path = os.path.join(engine_dir, filename)
                    master_file.write(f"### Component: {filename}\n\n")
                    with open(local_path, 'r', encoding='utf-8') as engine_file:
                        content = engine_file.read()
                        # Demote headers by prepending '###' to '#' lines to nest them under '### Component: {filename}'
                        demoted_lines = [('###' + line) if line.startswith('#') else line for line in content.splitlines()]
                        master_file.write('\n'.join(demoted_lines))
                    master_file.write("\n\n---\n\n")

    # 3. Synchronize the master document as a native Google Doc named "master_trading_knowledge"
    print("\nSyncing master document to Google Drive...")
    upsert_file(service, master_doc_path, "master_trading_knowledge.md", root_folder_id)

    print("\n--- Synchronization Sync Complete! ---")

if __name__ == "__main__":
    main()
