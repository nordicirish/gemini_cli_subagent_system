import sys
import os

if __name__ == "__main__":
    _ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    _PY_DIR = os.path.join(_ROOT_DIR, "python")
    if _PY_DIR not in sys.path:
        sys.path.insert(0, _PY_DIR)

    import web_server as _app_server

    try:
        sys.exit(_app_server.main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
