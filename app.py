import os
import sys
import time
import json
import uuid
import threading
import shutil
import glob
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_file, Response, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "state.json")
TEMP_DIR = os.path.join(BASE_DIR, "temp_downloads")
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Admin Basic Auth credentials (can be overridden via env vars)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "tinycrap2026")

# In-memory tracking
state_lock = threading.Lock()
info_cache = {}  # url -> (data, timestamp)
client_request_history = {}  # ip -> list of timestamps

# Queue and Worker State
MAX_CONCURRENT_DOWNLOADS = 2
jobs_lock = threading.Lock()
jobs = {}  # job_id -> job dict
job_queue = []  # list of job_ids waiting
active_jobs = set()  # set of job_ids currently downloading


def load_state():
    with state_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "lifetime_downloads": 0,
            "require_api_key": False,
            "api_keys": [
                {"key": "tc_demo_key_789123", "name": "Default Personal Key", "created_at": "2026-10-10 12:00:00", "uses": 0}
            ],
            "update_notice": {
                "version_code": 1,
                "version_name": "1.0.0",
                "apk_url": "https://example.com/tinycrap-latest.apk",
                "changelog": "Initial release with turbo downloads and format selection.",
                "force_update": False
            }
        }


def save_state(state):
    with state_lock:
        with open(DATA_FILE, "w") as f:
            json.dump(state, f, indent=2)


def increment_lifetime_downloads():
    state = load_state()
    state["lifetime_downloads"] = state.get("lifetime_downloads", 0) + 1
    save_state(state)


def verify_api_key(req):
    state = load_state()
    if not state.get("require_api_key", False):
        return True, "open"
    key = req.headers.get("X-API-Key") or req.args.get("api_key")
    if not key:
        return False, "API key required"
    valid = False
    for k in state.get("api_keys", []):
        if k.get("key") == key:
            k["uses"] = k.get("uses", 0) + 1
            save_state(state)
            valid = True
            break
    if valid:
        return True, "valid"
    return False, "Invalid API key"


def rate_limit_check(client_ip):
    now = time.time()
    history = client_request_history.get(client_ip, [])
    # keep last 60 seconds
    history = [t for t in history if now - t < 60]
    if len(history) > 40:
        return False
    history.append(now)
    client_request_history[client_ip] = history
    return True


def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASS


def authenticate():
    return Response(
        "Could not verify your access level for Tiny Crap Admin.\n"
        "You have to enter proper credentials", 401,
        {"WWW-Authenticate": 'Basic realm="Tiny Crap Admin Panel"'}
    )


def requires_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# Background cleaner for temp downloads older than 30 minutes
def cleanup_old_files_worker():
    while True:
        try:
            now = time.time()
            cutoff = now - 1800  # 30 minutes
            for root, dirs, files in os.walk(TEMP_DIR):
                for f in files:
                    filepath = os.path.join(root, f)
                    if os.path.exists(filepath):
                        mtime = os.path.getmtime(filepath)
                        if mtime < cutoff:
                            try:
                                os.remove(filepath)
                            except Exception:
                                pass
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=cleanup_old_files_worker, daemon=True).start()


# yt-dlp downloader worker thread
def download_worker_loop():
    while True:
        job_to_run = None
        with jobs_lock:
            if len(active_jobs) < MAX_CONCURRENT_DOWNLOADS and job_queue:
                job_id = job_queue.pop(0)
                if job_id in jobs:
                    active_jobs.add(job_id)
                    job_to_run = jobs[job_id]

        if job_to_run:
            execute_download_job(job_to_run)
        else:
            time.sleep(1)

threading.Thread(target=download_worker_loop, daemon=True).start()


def execute_download_job(job):
    job_id = job["id"]
    url = job["url"]
    format_id = job.get("format_id")
    media_type = job.get("type", "video")
    requested_ext = job.get("ext", "mp4")

    job["status"] = "downloading"
    job["progress"] = 0.0
    job["speed"] = "Connecting..."
    job["eta"] = "..."

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                job["progress"] = min(99.0, (downloaded / total) * 100.0)
            job["speed"] = d.get("_speed_str") or "..."
            job["eta"] = d.get("_eta_str") or "..."
        elif d.get("status") == "finished":
            job["progress"] = 99.5
            job["speed"] = "Processing..."

    try:
        import yt_dlp
        output_template = os.path.join(TEMP_DIR, f"{job_id}_%(title).50s.%(ext)s")

        ydl_opts = {
            "outtmpl": output_template,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
        }

        if media_type == "audio":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": requested_ext if requested_ext in ["mp3", "m4a", "opus", "wav", "flac"] else "mp3",
                "preferredquality": job.get("bitrate", "320"),
            }]
        else:
            if format_id and format_id != "best":
                ydl_opts["format"] = f"{format_id}+bestaudio/best"
            else:
                ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            ydl_opts["merge_output_format"] = requested_ext if requested_ext in ["mp4", "webm"] else "mp4"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find output file
        matching = glob.glob(os.path.join(TEMP_DIR, f"{job_id}_*"))
        if matching:
            final_path = matching[0]
            job["status"] = "completed"
            job["progress"] = 100.0
            job["file_path"] = final_path
            job["filename"] = os.path.basename(final_path)
            job["file_size"] = os.path.getsize(final_path)
            increment_lifetime_downloads()
        else:
            job["status"] = "failed"
            job["error"] = "Output file not found after processing"

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
    finally:
        with jobs_lock:
            active_jobs.discard(job_id)


# API: Health Check
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Tiny Crap Downloader",
        "timestamp": datetime.now().isoformat()
    })


# API: Metadata & Formats Inspector
@app.route("/api/info", methods=["POST"])
def get_info():
    auth_ok, reason = verify_api_key(request)
    if not auth_ok:
        return jsonify({"error": reason}), 401

    client_ip = request.remote_addr or "127.0.0.1"
    if not rate_limit_check(client_ip):
        return jsonify({"error": "Rate limit exceeded. Please wait a moment."}), 429

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Cache lookup (5 min TTL)
    now = time.time()
    if url in info_cache:
        cached_data, cached_time = info_cache[url]
        if now - cached_time < 300:
            return jsonify(cached_data)

    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=False)

        title = meta.get("title", "Social Media Video")
        thumbnail = meta.get("thumbnail") or (meta.get("thumbnails", [{}])[-1].get("url") if meta.get("thumbnails") else "")
        duration = meta.get("duration")
        uploader = meta.get("uploader") or meta.get("channel") or "Unknown Creator"

        # Determine platform
        extractor = meta.get("extractor_key", "").lower()
        if "instagram" in extractor or "instagram.com" in url:
            platform = "Instagram"
        elif "youtube" in extractor or "youtube.com" in url or "youtu.be" in url:
            platform = "YouTube"
        elif "facebook" in extractor or "facebook.com" in url or "fb.watch" in url:
            platform = "Facebook"
        else:
            platform = "Web Media"

        # Extract formats
        raw_formats = meta.get("formats", [])
        video_qualities = []
        seen_res = set()

        for f in raw_formats:
            vcodec = f.get("vcodec", "none")
            height = f.get("height")
            if vcodec != "none" and height and height not in seen_res:
                seen_res.add(height)
                ext = f.get("ext", "mp4")
                filesize = f.get("filesize") or f.get("filesize_approx") or 0
                video_qualities.append({
                    "format_id": str(f.get("format_id", "")),
                    "resolution": f"{height}p",
                    "height": height,
                    "ext": ext if ext in ["mp4", "webm"] else "mp4",
                    "filesize": filesize,
                    "note": f.get("format_note", "")
                })

        # Sort descending by resolution
        video_qualities.sort(key=lambda x: x["height"], reverse=True)
        if not video_qualities:
            video_qualities.append({
                "format_id": "best",
                "resolution": "Best Available",
                "height": 1080,
                "ext": "mp4",
                "filesize": 0,
                "note": "Auto best"
            })

        audio_formats = [
            {"format_id": "bestaudio", "ext": "mp3", "bitrate": "320", "label": "MP3 &middot; 320 kbps (High Quality)"},
            {"format_id": "bestaudio", "ext": "mp3", "bitrate": "256", "label": "MP3 &middot; 256 kbps (Standard)"},
            {"format_id": "bestaudio", "ext": "m4a", "bitrate": "256", "label": "M4A &middot; 256 kbps (Apple AAC)"},
            {"format_id": "bestaudio", "ext": "opus", "bitrate": "192", "label": "Opus &middot; 192 kbps (High Efficiency)"},
            {"format_id": "bestaudio", "ext": "flac", "bitrate": "0", "label": "FLAC &middot; Lossless Audio"},
            {"format_id": "bestaudio", "ext": "wav", "bitrate": "0", "label": "WAV &middot; Uncompressed"},
        ]

        result = {
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "thumbnail": thumbnail,
            "platform": platform,
            "url": url,
            "video_qualities": video_qualities,
            "audio_formats": audio_formats
        }

        info_cache[url] = (result, now)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": f"Failed to inspect media: {str(e)}"}), 400


# API: Submit Download Request
@app.route("/api/download", methods=["POST"])
def submit_download():
    auth_ok, reason = verify_api_key(request)
    if not auth_ok:
        return jsonify({"error": reason}), 401

    client_ip = request.remote_addr or "127.0.0.1"
    if not rate_limit_check(client_ip):
        return jsonify({"error": "Rate limit exceeded"}), 429

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "url": url,
        "title": data.get("title", ""),
        "type": data.get("type", "video"),
        "format_id": data.get("format_id", "best"),
        "quality": data.get("quality", "Best"),
        "ext": data.get("ext", "mp4"),
        "bitrate": data.get("bitrate", "320"),
        "status": "queued",
        "progress": 0.0,
        "speed": "In queue...",
        "eta": "...",
        "created_at": datetime.now().strftime("%H:%M:%S"),
        "file_path": None,
        "filename": None,
        "file_size": 0,
        "error": None
    }

    with jobs_lock:
        jobs[job_id] = job
        job_queue.append(job_id)
        queue_pos = len(job_queue)

    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "queue_position": queue_pos
    })


# API: Job Status Polling
@app.route("/api/status/<job_id>", methods=["GET"])
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        queue_pos = (job_queue.index(job_id) + 1) if job_id in job_queue else 0

        return jsonify({
            "job_id": job["id"],
            "status": job["status"],
            "progress": job.get("progress", 0.0),
            "speed": job.get("speed", ""),
            "eta": job.get("eta", ""),
            "queue_position": queue_pos,
            "filename": job.get("filename"),
            "file_size": job.get("file_size", 0),
            "error": job.get("error")
        })


# API: Download Finished Media File
@app.route("/api/file/<job_id>", methods=["GET"])
def get_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "completed" or not job.get("file_path"):
            return jsonify({"error": "File not ready or expired"}), 404
        filepath = job["file_path"]

    if not os.path.exists(filepath):
        return jsonify({"error": "File has expired or was removed"}), 410

    filename = os.path.basename(filepath)
    # Remove the uuid prefix from downloaded filename if desired
    clean_name = filename
    if "_" in filename:
        clean_name = filename.split("_", 1)[1]

    return send_file(
        filepath,
        as_attachment=True,
        download_name=clean_name
    )


# API: Version & Update Check
@app.route("/api/check_update", methods=["GET"])
def check_update():
    state = load_state()
    update_notice = state.get("update_notice", {})
    return jsonify(update_notice)


# ADMIN: Panel
@app.route("/admin", methods=["GET"])
@requires_admin
def admin_panel():
    state = load_state()
    # Check ffmpeg availability
    ffmpeg_status = "Available" if shutil.which("ffmpeg") else "Not in PATH"
    try:
        import yt_dlp
        ytdlp_ver = yt_dlp.version.__version__
    except Exception:
        ytdlp_ver = "Unknown"

    with jobs_lock:
        running = [jobs[jid] for jid in active_jobs if jid in jobs]
        waiting = [jobs[jid] for jid in job_queue if jid in jobs]

    return render_template(
        "admin.html",
        lifetime_downloads=state.get("lifetime_downloads", 0),
        require_api_key=state.get("require_api_key", False),
        api_keys=state.get("api_keys", []),
        update_notice=state.get("update_notice", {}),
        ffmpeg_status=ffmpeg_status,
        ytdlp_version=ytdlp_ver,
        running_jobs=running,
        queued_jobs=waiting,
        message=request.args.get("msg")
    )


@app.route("/admin/toggle_key_requirement", methods=["POST"])
@requires_admin
def toggle_key_req():
    state = load_state()
    state["require_api_key"] = not state.get("require_api_key", False)
    save_state(state)
    return admin_panel()


@app.route("/admin/create_key", methods=["POST"])
@requires_admin
def create_key():
    name = (request.form.get("name") or "New Key").strip()
    new_key = f"tc_{uuid.uuid4().hex[:12]}"
    state = load_state()
    state.setdefault("api_keys", []).append({
        "key": new_key,
        "name": name,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uses": 0
    })
    save_state(state)
    return admin_panel()


@app.route("/admin/revoke_key", methods=["POST"])
@requires_admin
def revoke_key():
    key_to_del = request.form.get("key")
    state = load_state()
    state["api_keys"] = [k for k in state.get("api_keys", []) if k.get("key") != key_to_del]
    save_state(state)
    return admin_panel()


@app.route("/admin/publish_update", methods=["POST"])
@requires_admin
def publish_update():
    version_code = int(request.form.get("version_code", 1))
    version_name = request.form.get("version_name", "1.0.0")
    apk_url = request.form.get("apk_url", "")
    changelog = request.form.get("changelog", "")
    force_update = bool(request.form.get("force_update"))

    state = load_state()
    state["update_notice"] = {
        "version_code": version_code,
        "version_name": version_name,
        "apk_url": apk_url,
        "changelog": changelog,
        "force_update": force_update
    }
    save_state(state)
    return admin_panel()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
