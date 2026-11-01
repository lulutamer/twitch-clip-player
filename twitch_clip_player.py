#!/usr/bin/env python3
"""
Twitch Clip Player
Paste a twitch.tv clips page link and every clip plays one after another.

Setup:   pip install yt-dlp
Run:     python twitch_clip_player.py
Then:    your browser opens at http://localhost:8765
"""

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install yt-dlp")

PORT = 8765


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def list_clips(url):
    """Return [{url, title}] for a channel clips page (or a single clip)."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "logger": QuietLogger(),
    }
    clips = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return clips
        entries = info.get("entries")
        if entries is None:  # a single clip link
            return [{"url": info.get("webpage_url") or url,
                     "title": info.get("title") or ""}]
        for e in entries:
            if not e:
                continue
            u = e.get("url") or e.get("webpage_url")
            if u:
                clips.append({"url": u, "title": e.get("title") or ""})
    return clips


def resolve_clip(url):
    """Get a direct playable video URL plus metadata for one clip."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "format": "best[ext=mp4]/best",
        "logger": QuietLogger(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    src = info.get("url")
    if not src and info.get("formats"):
        src = info["formats"][-1].get("url")
    if not src:
        raise RuntimeError("No playable video found for this clip")
    return {
        "src": src,
        "title": info.get("title") or "",
        "streamer": info.get("uploader") or info.get("creator") or "",
        "duration": info.get("duration"),
        "page": info.get("webpage_url") or url,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send(200, PAGE, "text/html")

        if parsed.path in ("/api/clips", "/api/resolve"):
            target = parse_qs(parsed.query).get("url", [""])[0].strip()
            host = urlparse(target).netloc.lower()
            if not (host == "twitch.tv" or host.endswith(".twitch.tv")):
                return self._send(400, json.dumps({"error": "Please use a twitch.tv link"}))
            try:
                if parsed.path == "/api/clips":
                    result = {"clips": list_clips(target)}
                else:
                    result = resolve_clip(target)
                return self._send(200, json.dumps(result))
            except Exception as exc:  # noqa: BLE001
                return self._send(500, json.dumps({"error": str(exc)[:300]}))

        self._send(404, "Not found", "text/plain")


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Twitch Clip Player</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #0e0e10; color: #efeff1; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 20px; margin: 0 0 14px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
  input[type=text] { flex: 1 1 360px; padding: 10px 12px; border-radius: 6px; border: 1px solid #3a3a3d;
    background: #18181b; color: inherit; font-size: 15px; }
  button { padding: 10px 16px; border-radius: 6px; border: 0; background: #9146ff; color: #fff;
    font-size: 14px; cursor: pointer; }
  button.secondary { background: #2f2f35; }
  button:disabled { opacity: .5; cursor: default; }
  label { font-size: 14px; color: #adadb8; display: flex; gap: 6px; align-items: center; }
  #stage { background: #000; border-radius: 8px; overflow: hidden; aspect-ratio: 16/9; position: relative; }
  video { width: 100%; height: 100%; display: block; background: #000; }
  #msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    text-align: center; padding: 20px; color: #adadb8; pointer-events: none; }
  #now { margin: 12px 0 4px; font-size: 17px; font-weight: 600; }
  #meta { color: #adadb8; font-size: 14px; }
  #meta a { color: #bf94ff; }
  .hint { color: #adadb8; font-size: 13px; margin-top: 14px; line-height: 1.5; }
  code { background: #1f1f23; padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Twitch Clip Player</h1>
  <div class="row">
    <input id="url" type="text" placeholder="https://www.twitch.tv/channelname/clips?filter=clips&range=all"
           spellcheck="false">
    <button id="go">Load &amp; play</button>
  </div>
  <div class="row">
    <button class="secondary" id="prev" disabled>&#9664; Prev</button>
    <button class="secondary" id="next" disabled>Next &#9654;</button>
    <label><input type="checkbox" id="shuffle"> Shuffle</label>
    <label><input type="checkbox" id="loop"> Loop</label>
    <span id="count" style="color:#adadb8;font-size:14px;margin-left:auto"></span>
  </div>

  <div id="stage">
    <video id="v" controls playsinline></video>
    <div id="msg">Paste a Twitch clips link above and hit play.</div>
  </div>
  <div id="now"></div>
  <div id="meta"></div>

  <div class="hint">
    Tip: the clips page only lists a time window. Add <code>?filter=clips&amp;range=all</code>
    (or <code>24hr</code>, <code>7d</code>, <code>30d</code>) to the link to choose which clips get loaded.
    Keyboard: &larr; previous, &rarr; next.
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const video = $("v");
let clips = [], order = [], pos = -1, failures = 0, cache = new Map();

function setMsg(text) { $("msg").textContent = text || ""; $("msg").style.display = text ? "flex" : "none"; }

async function api(path, url) {
  const r = await fetch(`/api/${path}?url=${encodeURIComponent(url)}`);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "Request failed");
  return j;
}

function resolveCached(clip) {
  if (!cache.has(clip.url)) {
    const p = api("resolve", clip.url);
    p.catch(() => cache.delete(clip.url));
    cache.set(clip.url, p);
  }
  return cache.get(clip.url);
}

function buildOrder(keepCurrent) {
  const current = pos >= 0 ? order[pos] : null;
  order = clips.map((_, i) => i);
  if ($("shuffle").checked) {
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    if (keepCurrent && current !== null) {
      order.splice(order.indexOf(current), 1);
      order.unshift(current);
    }
  }
  pos = keepCurrent && current !== null ? 0 : -1;
  if (!$("shuffle").checked && keepCurrent && current !== null) pos = current;
}

function updateUI() {
  $("count").textContent = clips.length && pos >= 0 ? `Clip ${pos + 1} of ${order.length}` : "";
  $("prev").disabled = !clips.length || pos <= 0;
  $("next").disabled = !clips.length;
}

async function playAt(p) {
  if (p >= order.length) {
    if ($("loop").checked) { buildOrder(false); p = 0; }
    else { setMsg("That was the last clip. Hit Load & play to start over."); return; }
  }
  if (p < 0) p = 0;
  pos = p;
  updateUI();
  const clip = clips[order[pos]];
  setMsg("Loading clip…");
  try {
    const info = await resolveCached(clip);
    if (p !== pos) return; // user skipped while loading
    failures = 0;
    video.src = info.src;
    await video.play().catch(() => {});
    setMsg("");
    $("now").textContent = info.title || clip.title || "Untitled clip";
    $("meta").innerHTML = (info.streamer ? info.streamer + " · " : "") +
      (info.duration ? Math.round(info.duration) + "s · " : "") +
      `<a href="${info.page}" target="_blank" rel="noopener">Open on Twitch</a>`;
    // warm up the next clip's link
    const nextClip = clips[order[pos + 1]];
    if (nextClip) resolveCached(nextClip);
  } catch (err) {
    failures++;
    if (failures >= 5) { setMsg("Several clips failed in a row: " + err.message); return; }
    setMsg("Skipping a clip that failed to load…");
    setTimeout(() => playAt(p + 1), 600);
  }
}

async function start() {
  const url = $("url").value.trim();
  if (!url) return;
  $("go").disabled = true;
  setMsg("Fetching clip list… (large channels can take a bit)");
  video.removeAttribute("src"); video.load();
  $("now").textContent = ""; $("meta").textContent = "";
  cache.clear(); failures = 0;
  try {
    const data = await api("clips", url);
    clips = data.clips;
    if (!clips.length) throw new Error("No clips found at that link.");
    pos = -1;
    buildOrder(false);
    await playAt(0);
  } catch (err) {
    clips = []; order = []; pos = -1;
    setMsg("Error: " + err.message);
  }
  $("go").disabled = false;
  updateUI();
}

$("go").onclick = start;
$("url").addEventListener("keydown", e => { if (e.key === "Enter") start(); });
$("next").onclick = () => playAt(pos + 1);
$("prev").onclick = () => playAt(pos - 1);
$("shuffle").onchange = () => { if (clips.length) { buildOrder(true); updateUI(); } };
video.addEventListener("ended", () => playAt(pos + 1));
video.addEventListener("error", () => { if (video.getAttribute("src")) playAt(pos + 1); });
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" && e.target.type === "text") return;
  if (e.key === "ArrowRight" && clips.length) playAt(pos + 1);
  if (e.key === "ArrowLeft" && pos > 0) playAt(pos - 1);
});
</script>
</body>
</html>
"""


def main():
    import os
    hosted = "PORT" in os.environ  # set automatically by hosting services
    port = int(os.environ.get("PORT", PORT))
    server = ThreadingHTTPServer(("0.0.0.0" if hosted else "127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Twitch Clip Player running on port {port}  (Ctrl+C to stop)")
    if not hosted:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
