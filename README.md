# Tiny Crap - Python / Flask Backend

A personal-use social media video and audio download backend powered by `yt-dlp` and `FFmpeg`.

## Features
- **URL Metadata & Formats Inspector**: Extracts Instagram Reels/Posts, YouTube Videos/Shorts, and Facebook videos into video resolutions (1080p, 720p, 480p, 360p in MP4/WebM) and audio formats (MP3 320/256k, M4A, Opus, WAV, FLAC).
- **Concurrency & Queue System**: Server-wide max 2 simultaneous downloads. Additional requests line up in queue ("In queue · #N") and automatically progress.
- **Auto-Cleanup**: Temporary media files are cleaned up after 30 minutes.
- **Admin Dashboard (`/admin`)**: Material Design Lite dashboard displaying health, lifetime downloads, active & queued jobs, API key manager, and Update Wall publisher.
- **Mandatory Update Wall**: Publish update notices; if `force_update` is enabled, connected apps display an unskippable update screen.
- **Runs on**: cPanel (CloudLinux Passenger), Waitress, or standard WSGI servers.

## Quick Start (Local / VPS)
```bash
cd backend
pip install -r requirements.txt
python app.py
```
Default port is 5000 (`http://localhost:5000` or `http://10.0.2.2:5000` in Android emulator).

## Admin Dashboard
- URL: `http://YOUR_SERVER_IP:5000/admin`
- Default Username: `admin`
- Default Password: `tinycrap2026`
*(Can be customized via `ADMIN_USER` and `ADMIN_PASS` environment variables).*

## cPanel / Passenger Deployment
1. Upload `/backend` contents to your cPanel application root.
2. In cPanel "Setup Python App", select Python 3.9+ and set Application startup file to `passenger_wsgi.py`.
3. Install dependencies from `requirements.txt`.
4. Ensure `ffmpeg` is available on the system.
