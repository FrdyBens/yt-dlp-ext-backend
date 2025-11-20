Local YouTube Downloader
=========================

This zip contains:

- server.py          -> Flask backend using yt-dlp
- extension/         -> Chromium extension (Manifest v3) UI

Quick start
-----------

1. Install dependencies:

   pip install flask yt-dlp mutagen

   Make sure ffmpeg is installed and on PATH.

2. Run backend:

   python server.py

   It will bind to 127.0.0.1:5001 and download to ~/Downloads/yt-downloader by default.

3. Load extension in Chrome/Edge/Brave:

   - Go to chrome://extensions
   - Enable "Developer mode"
   - Click "Load unpacked"
   - Select the "extension" folder inside this zip

4. Use:

   - Open a YouTube video or playlist
   - Click the extension icon
   - Fetch Info, adjust settings, Start Download
   - Monitor progress & queue from the popup
