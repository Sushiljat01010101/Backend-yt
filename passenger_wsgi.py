import sys
import os

# Add application directory to python path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from app import app as application

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Tiny Crap server on port {port}...")
    serve(application, host="0.0.0.0", port=port, threads=8)
