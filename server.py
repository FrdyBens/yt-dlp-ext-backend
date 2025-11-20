#!/usr/bin/env python3
import json
import os
import threading
import uuid
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- CONFIG ---------------- #

load_dotenv()


class Config:
    # Where to save everything
    DOWNLOAD_DIR = os.path.expanduser(os.getenv("DOWNLOAD_DIR", "./downloads"))

    # Max concurrent jobs
    MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))

    # Filename template (yt-dlp style)
    FILENAME_TEMPLATE = os.getenv("FILENAME_TEMPLATE", "%(title)s [%(id)s].%(ext)s")

    # Default thumbnail embedding toggle
    EMBED_THUMBNAIL_DEFAULT = os.getenv("EMBED_THUMBNAIL_DEFAULT", "true").lower() == "true"

    # FFmpeg path (None = use system)
    FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION") or None

    # Allowed hostnames
    ALLOWED_HOSTS = [
        "www.youtube.com",
        "youtube.com",
        "music.youtube.com",
        "youtu.be",
        "m.youtube.com"
    ]

    PORT = int(os.getenv("PORT", "5005"))
    DEFAULT_AUDIO_BITRATE = os.getenv("DEFAULT_AUDIO_BITRATE", "320k")
    DEFAULT_VIDEO_FORMAT = os.getenv("DEFAULT_VIDEO_FORMAT", "mp4")
    THUMBNAIL_QUALITY = os.getenv("THUMBNAIL_QUALITY", "max")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
    BACKEND_MODE = os.getenv("BACKEND_MODE", "local")
    REMOTE_BASE_URL = os.getenv("REMOTE_BASE_URL", "")

Path(Config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

# ---------------- JOB MODEL ---------------- #

class Job:
    def __init__(self, url, options):
        self.id = str(uuid.uuid4())
        self.url = url
        self.options = options  # dict from request
        self.state = "QUEUED"  # QUEUED, DOWNLOADING, MERGING, EMBEDDING_THUMBNAIL, CONVERTING, COMPLETED, ERROR, CANCELLED
        self.progress = 0.0
        self.speed = 0.0
        self.eta = None
        self.downloaded_bytes = 0
        self.total_bytes = None
        self.error = None
        self.created_at = time.time()
        self.updated_at = time.time()
        self.output_files = []  # list of file paths (playlist support)
        self.log = []
        self.cancel_requested = False

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "options": self.options,
            "state": self.state,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "output_files": self.output_files,
            "log": self.log[-50:],  # last 50 lines
        }

# ---------------- GLOBAL STATE ---------------- #

app = Flask(__name__)

jobs = {}          # job_id -> Job
job_queue = []     # list of Job objects (FIFO)
active_jobs = 0
jobs_lock = threading.Lock()

# ---------------- UTILS ---------------- #

def is_allowed_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if not parsed.scheme.startswith("http"):
        return False
    host = parsed.netloc.split(":")[0].lower()
    return host in Config.ALLOWED_HOSTS

def humanize_duration(seconds):
    if seconds is None:
        return None
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    else:
        return f"{m:d}:{s:02d}"

def humanize_int(num):
    if num is None:
        return None
    try:
        n = int(num)
    except Exception:
        return None
    for unit in ["", "K", "M", "B"]:
        if abs(n) < 1000:
            return f"{n}{unit}"
        n //= 1000
    return f"{n}T"

# yt-dlp logger that dumps into job.log
class JobLogger:
    def __init__(self, job: Job):
        self.job = job

    def debug(self, msg):
        self._log("DEBUG", msg)

    def info(self, msg):
        self._log("INFO", msg)

    def warning(self, msg):
        self._log("WARN", msg)

    def error(self, msg):
        self._log("ERROR", msg)

    def _log(self, level, msg):
        text = f"[{level}] {msg}"
        with jobs_lock:
            self.job.log.append(text)
            self.job.updated_at = time.time()


def ensure_cache_dir():
    cache_dir = Path(__file__).parent / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir

# ---------------- yt-dlp OPTIONS BUILDER ---------------- #

def build_format_string(options):
    """Map UI options -> yt-dlp format string."""
    fmt = options.get("format", Config.DEFAULT_VIDEO_FORMAT)
    quality = options.get("quality", "best")
    custom_format = options.get("custom_format")

    if fmt == "custom" and custom_format:
        return custom_format

    # Map quality labels to max height
    quality_map = {
        "144p": 144,
        "240p": 240,
        "360p": 360,
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
        "1440p": 1440,
        "2160p": 2160,  # 4K
        "4320p": 4320,  # 8K
    }

    if fmt in ("mp3", "m4a", "wav", "audio", "bestaudio"):
        # Strict audio-only path: never touch video streams or mp4 containers when possible
        return "bestaudio[vcodec=none][ext!=mp4][ext!=m4a]/bestaudio[vcodec=none][ext!=mp4]/bestaudio[vcodec=none]"

    # Video+audio combos
    if quality == "best" or quality not in quality_map:
        # best mp4 or fallback
        if fmt == "mp4":
            return "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best"
        elif fmt == "webm":
            return "bestvideo[ext=webm]+bestaudio/best[ext=webm]/best"
        else:
            return "bestvideo+bestaudio/best"
    else:
        h = quality_map[quality]
        if fmt == "mp4":
            return f"bestvideo[ext=mp4][height<={h}]+bestaudio/best[ext=mp4][height<={h}]/best[height<={h}]"
        elif fmt == "webm":
            return f"bestvideo[ext=webm][height<={h}]+bestaudio/best[ext=webm][height<={h}]/best[height<={h}]"
        else:
            return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best[height<={h}]"

def build_ydl_opts(job: Job):
    opts = job.options
    format_str = build_format_string(opts)

    subtitles = opts.get("subtitles", {})
    subs_enabled = bool(subtitles.get("enabled"))
    embed_subs = bool(subtitles.get("embed"))
    subs_langs_raw = subtitles.get("languages") or ""
    subs_langs = [lang.strip() for lang in subs_langs_raw.split(",") if lang.strip()] or ["en"]

    embed_thumb = bool(opts.get("embed_thumbnail", Config.EMBED_THUMBNAIL_DEFAULT))
    fmt = opts.get("format", "mp4")
    allowed_bitrates = {"128k", "192k", "256k", "320k"}
    audio_bitrate_raw = str(opts.get("audio_bitrate") or Config.DEFAULT_AUDIO_BITRATE)
    if not audio_bitrate_raw.endswith("k"):
        audio_bitrate_raw = f"{audio_bitrate_raw}k" if audio_bitrate_raw.isdigit() else audio_bitrate_raw
    audio_bitrate = audio_bitrate_raw if audio_bitrate_raw in allowed_bitrates else Config.DEFAULT_AUDIO_BITRATE

    # Playlist handling
    playlist = opts.get("playlist", {}) or {}
    playlist_items = None
    mode = playlist.get("mode", "auto")
    if mode == "range":
        start = playlist.get("range_start")
        end = playlist.get("range_end")
        if start and end:
            playlist_items = f"{start}-{end}"
    elif mode == "selection":
        items = playlist.get("items") or []
        if items:
            playlist_items = ",".join(str(i) for i in items)

    postprocessors = []

    # Audio extraction if needed
    if fmt in ("mp3", "m4a", "wav"):
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            "preferredquality": audio_bitrate.replace("k", ""),
        })

    # Thumbnails: write + convert + embed + metadata
    if embed_thumb:
        postprocessors.append({
            "key": "FFmpegThumbnailsConvertor",
            "format": "jpg",  # convert to jpg for compatibility
        })
        postprocessors.append({
            "key": "EmbedThumbnail",
        })

    # Always add metadata tagging
    postprocessors.append({"key": "FFmpegMetadata"})

    ydl_opts = {
        "format": format_str,
        "outtmpl": os.path.join(Config.DOWNLOAD_DIR, Config.FILENAME_TEMPLATE),
        "restrictfilenames": False,
        "noplaylist": False,  # we control via playlist_items
        "playlist_items": playlist_items,
        "writethumbnail": True,
        "postprocessors": postprocessors,
        "logger": JobLogger(job),
        "progress_hooks": [make_progress_hook(job)],
        "ignoreerrors": True,
        "ffmpeg_location": Config.FFMPEG_LOCATION,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
    }

    # Subtitles
    if subs_enabled:
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = True
        ydl_opts["subtitleslangs"] = subs_langs
        ydl_opts["subtitlesformat"] = "best"
        if embed_subs:
            ydl_opts["embedsubtitles"] = True

    return ydl_opts


def collect_metadata(info):
    """Build a metadata dict suitable for MP3 tagging."""
    if not info:
        return {}
    tags = info.get("tags") or []
    upload_date = info.get("upload_date") or ""
    year = upload_date[:4] if upload_date else None
    playlist_count = None
    if info.get("_type") == "playlist":
        playlist_count = len(info.get("entries") or [])
        # prefer first entry for metadata preview
        if info.get("entries"):
            info = info["entries"][0]
    metadata = {
        "title": info.get("title"),
        "artist": info.get("artist") or info.get("uploader") or info.get("channel"),
        "channel": info.get("channel") or info.get("uploader"),
        "album": info.get("album") or info.get("channel") or info.get("uploader"),
        "genre": (tags[0] if tags else None) or "Other",
        "year": year,
        "date": upload_date,
        "description": info.get("description"),
        "track_index": info.get("playlist_index"),
        "total_tracks": playlist_count,
        "comment": info.get("comment") or info.get("description"),
        "thumbnail": info.get("thumbnail"),
    }
    return {k: v for k, v in metadata.items() if v is not None}

def make_progress_hook(job: Job):
    def hook(d):
        with jobs_lock:
            if job.cancel_requested and d.get("status") == "downloading":
                raise DownloadError("Download cancelled by user")

            status = d.get("status")
            if status == "downloading":
                job.state = "DOWNLOADING"
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                job.downloaded_bytes = downloaded
                job.total_bytes = total
                if total:
                    job.progress = min(100.0, downloaded * 100.0 / total)
                else:
                    job.progress = 0.0
                job.speed = d.get("speed") or 0.0
                job.eta = d.get("eta")
            elif status == "finished":
                # yt-dlp finished downloading, now does post-processing
                job.state = "MERGING"
                job.progress = 100.0
            job.updated_at = time.time()
    return hook

# ---------------- QUEUE / WORKER ---------------- #

def try_start_next_jobs():
    global active_jobs
    with jobs_lock:
        while active_jobs < Config.MAX_CONCURRENT_JOBS and job_queue:
            job = job_queue.pop(0)
            if job.state != "QUEUED":
                continue
            active_jobs += 1
            t = threading.Thread(target=run_job, args=(job,), daemon=True)
            t.start()

def run_job(job: Job):
    global active_jobs
    try:
        with jobs_lock:
            if job.cancel_requested:
                job.state = "CANCELLED"
                job.updated_at = time.time()
                return
            job.state = "DOWNLOADING"
            job.updated_at = time.time()

        ydl_opts = build_ydl_opts(job)

        def _run():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(job.url, download=True)
                return info

        info = _run()

        output_files = []

        def collect_entries(e):
            if isinstance(e, dict):
                if "requested_downloads" in e:
                    for rd in e["requested_downloads"]:
                        fp = rd.get("filepath") or rd.get("_filename")
                        if fp:
                            output_files.append(fp)
                else:
                    fp = e.get("filepath") or e.get("_filename")
                    if fp:
                        output_files.append(fp)

        if isinstance(info, dict) and info.get("_type") == "playlist":
            for entry in info.get("entries") or []:
                collect_entries(entry)
        else:
            collect_entries(info)

        with jobs_lock:
            job.output_files = list(dict.fromkeys(output_files))  # unique
            if job.cancel_requested:
                job.state = "CANCELLED"
            else:
                # Fake an "EMBEDDING_THUMBNAIL" step briefly for UX
                job.state = "EMBEDDING_THUMBNAIL" if job.options.get("embed_thumbnail", Config.EMBED_THUMBNAIL_DEFAULT) else "MERGING"
            job.updated_at = time.time()

        # Small delay to let UI show EMBEDDING_THUMBNAIL / MERGING
        time.sleep(0.5)

        with jobs_lock:
            if job.state not in ("CANCELLED", "ERROR"):
                job.state = "COMPLETED"
                job.progress = 100.0
                job.updated_at = time.time()

    except DownloadError as e:
        with jobs_lock:
            if job.cancel_requested:
                job.state = "CANCELLED"
                job.error = "Cancelled by user"
            else:
                job.state = "ERROR"
                job.error = str(e)
            job.updated_at = time.time()
    except Exception as e:
        with jobs_lock:
            job.state = "ERROR"
            job.error = str(e)
            job.updated_at = time.time()
    finally:
        with jobs_lock:
            active_jobs = max(0, active_jobs - 1)
        try_start_next_jobs()

# ---------------- API ENDPOINTS ---------------- #

@app.after_request
def add_cors_headers(resp):
    # Local-only; CORS * is acceptable here but you can tighten it to your extension later
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp

@app.route("/api/info", methods=["POST", "OPTIONS"])
def api_info():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url or not is_allowed_youtube_url(url):
        return jsonify({"error": "Invalid or unsupported URL. Only YouTube URLs are allowed."}), 400

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch info: {e}"}), 500

    def build_video_summary(e):
        if not e:
            return None
        return {
            "id": e.get("id"),
            "title": e.get("title"),
            "url": e.get("webpage_url") or e.get("url"),
            "thumbnail": e.get("thumbnail"),
            "duration": e.get("duration"),
            "duration_text": humanize_duration(e.get("duration")),
            "channel": e.get("channel") or e.get("uploader"),
            "view_count": e.get("view_count"),
            "view_count_text": humanize_int(e.get("view_count")),
        }

    response = {
        "is_playlist": info.get("_type") == "playlist",
        "title": info.get("title"),
        "url": info.get("webpage_url") or url,
        "thumbnail": info.get("thumbnail"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "duration_text": humanize_duration(info.get("duration")),
        "view_count": info.get("view_count"),
        "view_count_text": humanize_int(info.get("view_count")),
        "entries": [],
    }

    if info.get("_type") == "playlist":
        entries = []
        for idx, e in enumerate(info.get("entries") or [], start=1):
            v = build_video_summary(e)
            if not v:
                continue
            v["index"] = idx
            entries.append(v)
        response["entries"] = entries
    else:
        response["entries"] = [build_video_summary(info)]

    return jsonify(response)

@app.route("/api/download", methods=["POST", "OPTIONS"])
def api_download():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url or not is_allowed_youtube_url(url):
        return jsonify({"error": "Invalid or unsupported URL. Only YouTube URLs are allowed."}), 400

    options = {
        "format": data.get("format", "mp4"),
        "quality": data.get("quality", "best"),
        "audio_bitrate": data.get("audio_bitrate", "192k"),
        "subtitles": data.get("subtitles") or {},
        "embed_thumbnail": bool(data.get("embed_thumbnail", Config.EMBED_THUMBNAIL_DEFAULT)),
        "playlist": data.get("playlist") or {},
        "custom_format": data.get("custom_format"),
    }

    job = Job(url, options)

    with jobs_lock:
        jobs[job.id] = job
        job_queue.append(job)
    try_start_next_jobs()

    return jsonify({"job_id": job.id})

@app.route("/api/status/<job_id>", methods=["GET"])
def api_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(job.to_dict())

@app.route("/api/queue", methods=["GET"])
def api_queue():
    with jobs_lock:
        all_jobs = [j.to_dict() for j in jobs.values()]
        # sort by created_at
        all_jobs.sort(key=lambda j: j["created_at"])
    return jsonify({"jobs": all_jobs})

@app.route("/api/cancel/<job_id>", methods=["POST", "OPTIONS"])
def api_cancel(job_id):
    if request.method == "OPTIONS":
        return ("", 204)

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.state in ("COMPLETED", "ERROR", "CANCELLED"):
            return jsonify({"error": f"Cannot cancel job in state {job.state}"}), 400
        job.cancel_requested = True
        job.state = "CANCELLED"
        job.updated_at = time.time()
    return jsonify({"status": "cancelled", "job_id": job_id})

@app.route("/api/config", methods=["GET"])
def api_config():
    # For UI to show download directory etc.
    return jsonify({
        "download_dir": Config.DOWNLOAD_DIR,
        "max_concurrent_jobs": Config.MAX_CONCURRENT_JOBS,
        "filename_template": Config.FILENAME_TEMPLATE,
        "embed_thumbnail_default": Config.EMBED_THUMBNAIL_DEFAULT,
    })


@app.route("/api/settings", methods=["GET"])
def api_settings():
    return jsonify({
        "download_dir": Config.DOWNLOAD_DIR,
        "max_concurrent_jobs": Config.MAX_CONCURRENT_JOBS,
        "filename_template": Config.FILENAME_TEMPLATE,
        "default_audio_bitrate": Config.DEFAULT_AUDIO_BITRATE,
        "default_video_format": Config.DEFAULT_VIDEO_FORMAT,
        "thumbnail_quality": Config.THUMBNAIL_QUALITY,
        "log_level": Config.LOG_LEVEL,
        "backend_mode": Config.BACKEND_MODE,
        "remote_base_url": Config.REMOTE_BASE_URL,
    })


@app.route("/api/metadata/generate", methods=["POST", "OPTIONS"])
def api_metadata_generate():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch metadata: {exc}"}), 500
    metadata = collect_metadata(info)
    cache_dir = ensure_cache_dir()
    (cache_dir / f"{uuid.uuid5(uuid.NAMESPACE_URL, url)}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify({"metadata": metadata})


@app.route("/api/metadata/save", methods=["POST", "OPTIONS"])
def api_metadata_save():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    metadata = data.get("metadata") or {}
    if not url:
        return jsonify({"error": "URL is required"}), 400
    cache_dir = ensure_cache_dir()
    (cache_dir / f"{uuid.uuid5(uuid.NAMESPACE_URL, url)}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify({"status": "saved", "metadata": metadata})


@app.route("/api/formats/<path:url>", methods=["GET"])
def api_formats(url):
    if not is_allowed_youtube_url(url):
        return jsonify({"error": "Invalid or unsupported URL. Only YouTube URLs are allowed."}), 400
    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch formats: {exc}"}), 500
    formats = []
    for f in info.get("formats") or []:
        formats.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "format_note": f.get("format_note"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "tbr": f.get("tbr"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
        })
    return jsonify({"formats": formats})

if __name__ == "__main__":
    # Bind only to localhost for safety
    app.run(host="127.0.0.1", port=Config.PORT, debug=False)
