import sys
import subprocess
import os

if __name__ == "__main__":
    # Seamlessly forward the execution to the actual script in the python/ folder
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python", "web_server.py")
    
    try:
        # Run the target script and pass along any command line arguments
        sys.exit(subprocess.call([sys.executable, script_path] + sys.argv[1:]))
    except KeyboardInterrupt:
        # Suppress the traceback if the user presses Ctrl+C
        sys.exit(0)
