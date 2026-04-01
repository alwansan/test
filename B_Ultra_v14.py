import os, sys, subprocess, threading, time, json, re, traceback
from pathlib import Path
from datetime import datetime

# ══════════════════════════════════════════════
#  مسار الحفظ
# ══════════════════════════════════════════════
def get_save_path():
    for p in [
        "/storage/emulated/0/Download/B-Ultra",
        os.path.expanduser("~/storage/downloads/B-Ultra"),
        os.path.join(os.path.expanduser("~"), "Downloads", "B-Ultra"),
    ]:
        try:
            os.makedirs(p, exist_ok=True)
            t = os.path.join(p, "._t")
            open(t, "w").close(); os.remove(t)
            return p
        except: continue
    fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Downloads")
    os.makedirs(fb, exist_ok=True)
    return fb

SAVE_PATH    = get_save_path()
HISTORY_FILE = os.path.join(SAVE_PATH, ".history.json")
LOG_FILE     = os.path.join(SAVE_PATH, "log.txt")

# ══════════════════════════════════════════════
#  نظام Logging — يحذف القديم ويبدأ جديد
# ══════════════════════════════════════════════
try:
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
except: pass

_log_fh = open(LOG_FILE, "w", encoding="utf-8", buffering=1)

class FullTee:
    """يكتب في stdout الأصلي + ملف Log"""
    def __init__(self, original, fh):
        self._o = original
        self._f = fh
    def write(self, data):
        try: self._o.write(data); self._o.flush()
        except: pass
        try: self._f.write(data); self._f.flush()
        except: pass
    def flush(self):
        try: self._o.flush()
        except: pass
        try: self._f.flush()
        except: pass
    def fileno(self):
        try: return self._o.fileno()
        except: return -1
    def __getattr__(self, n):
        return getattr(self._o, n)

_orig_out = sys.stdout
_orig_err = sys.stderr
sys.stdout = FullTee(_orig_out, _log_fh)
sys.stderr = FullTee(_orig_err, _log_fh)

def LOG(msg, level="INFO"):
    ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}][{level}] {msg}"
    print(line)

LOG("=" * 60)
LOG(f"B-Ultra v14 — بدء التشغيل")
LOG(f"Python: {sys.version.split()[0]}")
LOG(f"Platform: {sys.platform}")
LOG(f"CWD: {os.getcwd()}")
LOG(f"Save: {SAVE_PATH}")
LOG(f"Log:  {LOG_FILE}")
LOG("=" * 60)

# ══════════════════════════════════════════════
#  تثبيت المكتبات
# ══════════════════════════════════════════════
def safe_pip(pkg, upgrade=False):
    LOG(f"pip install {pkg}{'  --upgrade' if upgrade else ''}...")
    flags = ["--quiet", "--break-system-packages"]
    for cmd in [
        [sys.executable, "-m", "pip", "install"] + (["--upgrade"] if upgrade else []) + [pkg] + flags,
        [sys.executable, "-m", "pip", "install"] + (["--upgrade"] if upgrade else []) + [pkg, "--quiet"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode == 0:
                LOG(f"  ✅ {pkg} جاهز")
                return True
            LOG(f"  ⚠️  stderr: {r.stderr.decode(errors='replace')[:200]}", "WARN")
        except Exception as e:
            LOG(f"  ⚠️  {e}", "WARN")
    LOG(f"  ❌ تعذّر تثبيت {pkg}", "ERROR")
    return False

try:
    from flask import Flask, render_template_string, request, jsonify
    LOG("✅ Flask محمّل")
except ImportError:
    safe_pip("flask")
    from flask import Flask, render_template_string, request, jsonify

try:
    import yt_dlp
    LOG(f"✅ yt-dlp v{yt_dlp.version.__version__}")
except ImportError:
    safe_pip("yt-dlp", upgrade=True)
    import yt_dlp

UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

def fmt_size(n):
    if not n or n <= 0: return ""
    if n >= 1_073_741_824: return f"{n/1_073_741_824:.1f}GB"
    if n >= 1_048_576:     return f"{n/1_048_576:.0f}MB"
    return f"{n/1024:.0f}KB"

# ══ حالة التحميل ══
state = {"phase":"idle","percent":0,"speed":"","eta":"","filename":"","error":"","step":""}
playlist_state = {
    "phase":"idle","total":0,"current_index":0,"current_title":"",
    "current_percent":0,"current_speed":"","current_eta":"",
    "items":[],"failed":[],"done_count":0,"step":"",
}
stop_flag     = threading.Event()
pl_stop_flag  = threading.Event()
download_lock = threading.Lock()
playlist_lock = threading.Lock()

def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            return json.load(open(HISTORY_FILE, encoding="utf-8"))
    except: pass
    return []

def save_history(entry):
    h = load_history(); h.insert(0, entry); h = h[:50]
    try: json.dump(h, open(HISTORY_FILE,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    except: pass

# ══ Logger لـ yt-dlp ══
class YtLogger:
    def debug(self, m):   LOG(m, "YT-DBG")
    def warning(self, m): LOG(m, "YT-WARN")
    def error(self, m):   LOG(m, "YT-ERR")

def hook(d):
    if stop_flag.is_set(): raise Exception("Cancelled")
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        done  = d.get("downloaded_bytes", 0); spd = d.get("speed") or 0; eta = d.get("eta") or 0
        state.update({"phase":"downloading","percent":round(done/total*100,1),
                      "speed":f"{spd/1024/1024:.2f} MB/s" if spd else "...",
                      "eta":f"{int(eta//60):02d}:{int(eta%60):02d}"})
    elif d["status"] == "finished":
        state.update({"phase":"merging","step":"🔧 دمج..."})

def pl_hook(d):
    if pl_stop_flag.is_set(): raise Exception("Cancelled")
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        done  = d.get("downloaded_bytes", 0); spd = d.get("speed") or 0; eta = d.get("eta") or 0
        playlist_state["current_percent"] = round(done/total*100, 1)
        playlist_state["current_speed"]   = f"{spd/1024/1024:.2f} MB/s" if spd else "..."
        playlist_state["current_eta"]     = f"{int(eta//60):02d}:{int(eta%60):02d}"
    elif d["status"] == "finished":
        playlist_state["step"] = "🔧 دمج..."

def opts_base():
    return {
        "quiet": False, "no_warnings": False, "verbose": True,
        "user_agent": UA, "logger": YtLogger(),
        "nocheckcertificate": True, "socket_timeout": 30, "retries": 5,
    }

def is_playlist_url(url):
    return ("playlist?list=" in url or
            ("list=" in url and "youtube" in url and "watch" not in url) or
            "/playlist" in url)

def get_smart_size(video_fmt, all_formats):
    v_size    = video_fmt.get("filesize") or video_fmt.get("filesize_approx") or 0
    has_audio = video_fmt.get("acodec","none") != "none"
    if has_audio: return v_size
    best_audio = None; best_abr = 0
    for f in all_formats:
        if f.get("vcodec","none") != "none": continue
        if f.get("acodec","none") == "none": continue
        abr = f.get("abr") or f.get("tbr") or 0
        a_sz = f.get("filesize") or f.get("filesize_approx") or 0
        if a_sz > 0 and abr >= best_abr:
            best_abr = abr; best_audio = f
    if best_audio:
        a_sz = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
        return (v_size + a_sz) if (v_size + a_sz) > 0 else 0
    return v_size

def extract_video_formats(formats):
    seen = {}
    for f in formats:
        h = f.get("height"); vc = f.get("vcodec","none")
        if vc == "none" or not h: continue
        fid = f.get("format_id",""); ext = f.get("ext","?"); fps = f.get("fps") or 0
        url_ = f.get("url","") or ""
        is_dash = "m3u8" not in url_ and url_.startswith("http")
        size = f.get("filesize") or f.get("filesize_approx") or 0
        ex = seen.get(h)
        if not ex: seen[h] = {"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
        elif is_dash and not ex["dash"]: seen[h] = {"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
        elif is_dash == ex["dash"]:
            if ext == "mp4" and ex["ext"] != "mp4": seen[h] = {"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
            elif ext == ex["ext"] and fps > ex["fps"]: seen[h] = {"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
    result = []
    for h, v in seen.items():
        smart = get_smart_size(v["_fmt"], formats)
        entry = dict(v); entry["size"] = smart; del entry["_fmt"]
        result.append(entry)
    return sorted(result, key=lambda x: x["res"])

def analyze_url(url):
    LOG(f"═ تحليل: {url}")
    title = ""; duration = 0; thumb = ""; all_formats = []
    try:
        with yt_dlp.YoutubeDL(opts_base()) as ydl:
            info = ydl.extract_info(url, download=False)
        all_formats = list(info.get("formats", []))
        title    = info.get("title", "")
        duration = info.get("duration", 0)
        thumb    = info.get("thumbnail", "")
        LOG(f"✅ عنوان: {title[:60]} | {len(all_formats)} صيغة")
    except Exception as e:
        LOG(f"❌ تحليل فشل: {e}", "ERROR")
        LOG(traceback.format_exc(), "TRACE")
    video_fmts = extract_video_formats(all_formats)
    return {"title":title,"duration":duration,"thumb":thumb,
            "formats":video_fmts,"all_formats":all_formats}

def analyze_playlist(url):
    LOG(f"═ تحليل قائمة: {url}")
    opts = opts_base()
    opts.update({"extract_flat":"in_playlist","playlistend":500,"ignoreerrors":True})
    entries = []; pl_title = ""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        pl_title = info.get("title","قائمة تشغيل")
        raw = info.get("entries",[]) or []
        for i, e in enumerate(raw):
            if not e: continue
            vid_id  = e.get("id","")
            vid_url = e.get("url","") or f"https://www.youtube.com/watch?v={vid_id}"
            if not vid_url.startswith("http"):
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
            thumb = e.get("thumbnail","")
            if not thumb and vid_id:
                thumb = f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg"
            entries.append({"index":i+1,"id":vid_id,"url":vid_url,
                "title":e.get("title","فيديو "+str(i+1)),"duration":e.get("duration",0) or 0,"thumb":thumb})
        LOG(f"📋 {len(entries)} فيديو: {pl_title[:50]}")
    except Exception as e:
        LOG(f"❌ قائمة فشلت: {e}", "ERROR")
        LOG(traceback.format_exc(), "TRACE")
        raise
    formats = []
    if entries:
        try:
            first = analyze_url(entries[0]["url"])
            formats = first["formats"]
            if not entries[0]["duration"] and first["duration"]:
                entries[0]["duration"] = first["duration"]
        except Exception as e:
            LOG(f"⚠️ أول فيديو فشل: {e}", "WARN")
    return {"pl_title":pl_title,"entries":entries,"formats":formats}

def pick_format(format_id, all_formats, mode):
    if mode == "audio": return "bestaudio/best","mp3"
    if format_id == "best": return "bestvideo+bestaudio/best",None
    sel = next((f for f in all_formats if f["format_id"]==format_id), None)
    if not sel: return f"{format_id}+bestaudio/best",None
    v_ext = sel.get("ext","mp4"); has_audio = sel.get("acodec","none") != "none"
    if has_audio: return format_id, None
    if v_ext == "webm":
        return f"{format_id}+bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio","webm"
    return f"{format_id}+bestaudio[ext=m4a]/bestaudio[acodec=aac]/bestaudio","mp4"

def quality_label(format_id, all_formats, mode):
    if mode == "audio": return "MP3"
    if format_id == "best": return "best"
    sel = next((f for f in all_formats if f["format_id"]==format_id), None)
    if sel:
        h = sel.get("height")
        if h: return f"{h}p"
    return format_id

def run_download(url, format_id, mode):
    with download_lock:
        stop_flag.clear()
        state.update({"phase":"downloading","percent":0,"speed":"جارٍ...","eta":"--:--",
                      "filename":"","error":"","step":"🔍 تحليل..."})
        LOG(f"═ تحميل: url={url[:60]} format={format_id} mode={mode}")
        try:
            data = analyze_url(url)
            safe_title = re.sub(r'[\\/*?:"<>|]',"_", data["title"])
            all_fmts   = data["all_formats"]
            req_fmt, merge_ext = pick_format(format_id, all_fmts, mode)
            qlabel   = quality_label(format_id, all_fmts, mode)
            out_name = f"{safe_title} [{qlabel}]"
            state["step"] = "⬇️ جارٍ التحميل..."
            LOG(f"  req={req_fmt} merge={merge_ext}")
            dl_opts = opts_base()
            dl_opts.update({"format":req_fmt,
                "outtmpl":os.path.join(SAVE_PATH, f"{out_name}.%(ext)s"),
                "progress_hooks":[hook],"noprogress":True,"overwrites":True,"continuedl":True})
            if merge_ext: dl_opts["merge_output_format"] = merge_ext
            if mode == "audio":
                dl_opts["postprocessors"] = [{"key":"FFmpegExtractAudio",
                                               "preferredcodec":"mp3","preferredquality":"192"}]
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])
            ext   = "mp3" if mode == "audio" else (merge_ext or "mp4")
            fname = f"{out_name}.{ext}"
            if not os.path.exists(os.path.join(SAVE_PATH, fname)):
                newer = sorted([f for f in Path(SAVE_PATH).glob(f"{out_name}*") if f.is_file()],
                               key=lambda x: x.stat().st_mtime, reverse=True)
                fname = newer[0].name if newer else fname
            state.update({"phase":"finished","percent":100,"filename":fname,"step":"✅ اكتمل!"})
            LOG(f"✅ حُفظ: {fname}")
            save_history({"title":data["title"],"url":url,"file":fname,"mode":mode,
                          "quality":qlabel,"date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                          "path":SAVE_PATH})
        except Exception as e:
            msg = str(e)
            LOG(f"❌ تحميل فشل: {msg}", "ERROR")
            LOG(traceback.format_exc(), "TRACE")
            if "Cancelled" in msg: state["phase"] = "idle"
            else: state.update({"phase":"error","error":msg[:300]})

def run_playlist_download(entries, format_id, mode):
    with playlist_lock:
        pl_stop_flag.clear()
        total = len(entries)
        playlist_state.update({
            "phase":"downloading","total":total,"current_index":0,"current_title":"",
            "current_percent":0,"current_speed":"","current_eta":"",
            "done_count":0,"step":"",
            "items":[{"index":e["index"],"title":e["title"],"thumb":e["thumb"],
                       "status":"pending","filename":"","error":""} for e in entries],
            "failed":[],
        })
        LOG(f"═ قائمة: {total} فيديو | format={format_id} | mode={mode}")
        for i, entry in enumerate(entries):
            if pl_stop_flag.is_set():
                LOG("⛔ إيقاف القائمة"); break
            idx = entry["index"]; url = entry["url"]; title = entry["title"]
            playlist_state["current_index"] = i+1
            playlist_state["current_title"] = title
            playlist_state["current_percent"] = 0
            playlist_state["items"][i]["status"] = "downloading"
            playlist_state["step"] = f"⬇️ [{i+1}/{total}] {title[:40]}"
            LOG(f"[{i+1}/{total}] {title[:60]}")
            try:
                data = analyze_url(url)
                safe_title = re.sub(r'[\\/*?:"<>|]',"_", data["title"] or title)
                all_fmts   = data["all_formats"]
                actual_fid = format_id
                if format_id not in ("best","bestaudio/best"):
                    if not any(f.get("format_id")==format_id for f in all_fmts):
                        LOG(f"  ⚠️ format {format_id} غير موجود → fallback best", "WARN")
                        actual_fid = "best"
                req_fmt, merge_ext = pick_format(actual_fid, all_fmts, mode)
                qlabel   = quality_label(actual_fid, all_fmts, mode)
                out_name = f"{safe_title} [{qlabel}]"
                dl_opts  = opts_base()
                dl_opts.update({"format":req_fmt,
                    "outtmpl":os.path.join(SAVE_PATH, f"{out_name}.%(ext)s"),
                    "progress_hooks":[pl_hook],"noprogress":True,
                    "overwrites":False,"continuedl":True,"ignoreerrors":False})
                if merge_ext: dl_opts["merge_output_format"] = merge_ext
                if mode == "audio":
                    dl_opts["postprocessors"] = [{"key":"FFmpegExtractAudio",
                                                   "preferredcodec":"mp3","preferredquality":"192"}]
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.download([url])
                ext   = "mp3" if mode == "audio" else (merge_ext or "mp4")
                fname = f"{out_name}.{ext}"
                if not os.path.exists(os.path.join(SAVE_PATH, fname)):
                    newer = sorted([f for f in Path(SAVE_PATH).glob(f"{out_name}*") if f.is_file()],
                                   key=lambda x: x.stat().st_mtime, reverse=True)
                    fname = newer[0].name if newer else fname
                playlist_state["items"][i]["status"]   = "done"
                playlist_state["items"][i]["filename"] = fname
                playlist_state["done_count"] += 1
                LOG(f"  ✅ {fname}")
                save_history({"title":data["title"],"url":url,"file":fname,"mode":mode,
                              "quality":qlabel,"date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "path":SAVE_PATH})
            except Exception as e:
                if pl_stop_flag.is_set(): break
                err_msg = str(e)[:250]
                LOG(f"  ❌ فشل [{title[:40]}]: {err_msg}", "ERROR")
                LOG(traceback.format_exc(), "TRACE")
                playlist_state["items"][i]["status"] = "failed"
                playlist_state["items"][i]["error"]  = err_msg
                playlist_state["failed"].append({"index":idx,"title":title,
                    "thumb":entry.get("thumb",""),"url":url,"error":err_msg})
        playlist_state["phase"] = "done"
        done  = playlist_state["done_count"]
        fails = len(playlist_state["failed"])
        playlist_state["step"] = f"✅ اكتمل: {done}" + (f" — ❌ {fails} فاشل" if fails else "")
        LOG(f"🏁 القائمة: {done} نجح | {fails} فشل")

# ══════════════════════════════════════════════
#  HTML
# ══════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>B-Ultra</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#080b12;--surface:#0d1018;--surface2:#12151f;--surface3:#171b28;
  --border:rgba(255,255,255,.07);--border-active:rgba(99,179,237,.38);
  --accent:#63b3ed;--accent2:#68d391;--gold:#f6c90e;--red:#fc8181;--pu:#c084fc;
  --txt:#dde6f0;--muted:#48556a;--r:16px;--font:'Cairo',sans-serif;--mono:'JetBrains Mono',monospace;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--txt);font-family:var(--font);min-height:100vh;
  display:flex;flex-direction:column;align-items:center;padding-bottom:80px;overflow-x:hidden}
#bgCanvas{position:fixed;inset:0;z-index:0;pointer-events:none;width:100%;height:100%}
#bgOverlay{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(ellipse 90% 55% at 15% -5%,rgba(99,179,237,.09) 0%,transparent 60%),
             radial-gradient(ellipse 70% 45% at 85% 105%,rgba(104,211,145,.06) 0%,transparent 60%)}
.w{width:100%;max-width:660px;padding:20px 16px;position:relative;z-index:2}
.header{text-align:center;padding:36px 0 28px}
.logo-wrap{display:inline-flex;align-items:center;gap:12px;background:rgba(13,16,24,.8);
  border:1px solid var(--border);border-radius:60px;padding:10px 22px 10px 16px;
  margin-bottom:18px;backdrop-filter:blur(16px)}
.logo-icon{width:36px;height:36px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:16px;font-weight:900;color:#080b12;flex-shrink:0}
.logo-text{font-size:17px;font-weight:700;background:linear-gradient(90deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-ver{font-size:11px;-webkit-text-fill-color:var(--muted);font-family:var(--mono)}
.tagline{font-size:13px;color:var(--muted)}
.card{background:rgba(13,16,24,.78);border:1px solid var(--border);border-radius:var(--r);
  overflow:hidden;margin-bottom:12px;transition:border-color .3s;backdrop-filter:blur(12px)}
.card:focus-within{border-color:var(--border-active)}
.input-card{padding:16px}
.mode-tabs{display:flex;background:var(--surface2);border-radius:12px;padding:4px;margin-bottom:14px;gap:4px}
.tab{flex:1;text-align:center;padding:9px 12px;border-radius:9px;font-size:13px;font-weight:600;
  border:none;cursor:pointer;color:var(--muted);background:transparent;transition:all .2s;font-family:var(--font)}
.tab.on{background:var(--surface3);color:var(--txt);box-shadow:0 1px 4px rgba(0,0,0,.5)}
.tab.on.vid{color:var(--accent)}.tab.on.aud{color:var(--accent2)}
.url-row{display:flex;gap:8px;align-items:center}
.url-input{flex:1;background:var(--surface2);border:1px solid var(--border);border-radius:12px;
  padding:12px 14px;font-size:14px;color:var(--txt);outline:none;direction:ltr;text-align:left;
  font-family:var(--font);transition:border-color .2s,box-shadow .2s}
.input-wrapper{position:relative;flex:1;display:flex;}
.url-input{flex:1;width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:12px;
  padding:12px 35px 12px 14px;font-size:14px;color:var(--txt);outline:none;direction:ltr;text-align:left;
  font-family:var(--font);transition:border-color .2s,box-shadow .2s}
.url-input::placeholder{color:var(--muted);direction:rtl;text-align:right;font-size:13px}
.url-input:focus{border-color:var(--border-active);box-shadow:0 0 0 3px rgba(99,179,237,.08)}
.clear-btn{position:absolute;right:10px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:14px;cursor:pointer;display:none;padding:5px;transition:color .2s;z-index:5;}
.clear-btn:hover{color:var(--red);}
.url-input::placeholder{color:var(--muted);direction:rtl;text-align:right;font-size:13px}
.url-input:focus{border-color:var(--border-active);box-shadow:0 0 0 3px rgba(99,179,237,.08)}
.paste-btn{background:rgba(99,179,237,.1);border:1px solid rgba(99,179,237,.2);border-radius:12px;
  color:var(--accent);padding:12px 16px;cursor:pointer;font-size:13px;font-weight:700;
  transition:all .2s;white-space:nowrap;font-family:var(--font);flex-shrink:0}
.paste-btn:active{transform:scale(.97)}
.spinner-wrap{display:none;padding:32px;text-align:center}.spinner-wrap.on{display:block}
.spinner{width:36px;height:36px;margin:0 auto 12px;border:3px solid rgba(99,179,237,.1);
  border-top:3px solid var(--accent);border-radius:50%;animation:rot .8s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
.spinner-txt{font-size:13px;color:var(--muted)}
.vc{display:none;animation:fadeUp .35s ease}.vc.on{display:block}
@keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.thumb-wrap{position:relative;width:100%;height:200px;background:var(--surface2);overflow:hidden}
.thumb-wrap img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000;opacity:.85}
.thumb-wrap::after{content:'';position:absolute;inset:0;background:linear-gradient(to bottom,transparent 40%,rgba(13,16,24,.95) 100%)}
.thumb-overlay{position:absolute;bottom:0;left:0;right:0;padding:16px;z-index:1}
.vid-title{font-size:14px;font-weight:700;line-height:1.4;margin-bottom:4px}
.vid-meta{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);font-family:var(--mono)}
.quality-wrap{padding:14px 16px 16px}
.q-label{font-size:10px;font-weight:700;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;font-family:var(--mono)}
.q-grid{display:flex;flex-wrap:wrap;gap:7px}
.qbtn{background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--txt);
  padding:9px 14px;cursor:pointer;font-size:12px;font-weight:600;transition:all .18s;
  display:flex;align-items:center;gap:7px;font-family:var(--font)}
.qbtn:hover{border-color:rgba(99,179,237,.3);color:var(--accent);background:rgba(99,179,237,.05);transform:translateY(-1px)}
.qbtn.best{flex-basis:100%;background:linear-gradient(135deg,rgba(99,179,237,.08),rgba(104,211,145,.05));
  border-color:rgba(99,179,237,.25);color:var(--accent);font-size:13px;justify-content:space-between}
.qbtn.aud-best{flex-basis:100%;background:linear-gradient(135deg,rgba(104,211,145,.08),rgba(99,179,237,.05));
  border-color:rgba(104,211,145,.25);color:var(--accent2);font-size:13px;justify-content:space-between}
.q-size{font-size:11px;font-weight:600;font-family:var(--mono);color:var(--accent2);opacity:.85;margin-right:auto}
.q-size.approx{color:var(--gold)}
.best-arrow{font-size:14px;opacity:.5}
.pg{display:none;padding:20px;animation:fadeUp .3s ease}.pg.on{display:block}
.pg-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.pg-status{font-size:13px;font-weight:600}
.pg-speed{font-size:11px;color:var(--muted);font-family:var(--mono);display:flex;align-items:center;gap:6px}
.speed-dot{width:6px;height:6px;border-radius:50%;background:var(--accent2);animation:pulse 1.2s ease infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}
.track-bg{height:4px;background:rgba(255,255,255,.05);border-radius:99px;overflow:hidden;margin-bottom:8px}
.track-fill{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),var(--accent2));
  border-radius:99px;transition:width .4s ease;box-shadow:0 0 8px rgba(99,179,237,.4)}
.pg-row{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:14px;font-family:var(--mono)}
.pg-pct{color:var(--accent);font-weight:700}
.cancel-btn{width:100%;background:rgba(252,129,129,.06);border:1px solid rgba(252,129,129,.15);
  color:var(--red);border-radius:10px;padding:10px;cursor:pointer;font-size:13px;font-weight:700;font-family:var(--font)}
.dn{display:none;padding:28px 20px;text-align:center;animation:fadeUp .4s ease}.dn.on{display:block}
.dn-ring{width:64px;height:64px;margin:0 auto 14px;background:linear-gradient(135deg,rgba(104,211,145,.12),rgba(99,179,237,.06));
  border:2px solid rgba(104,211,145,.25);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px}
.dn-title{font-size:18px;font-weight:800;color:var(--accent2);margin-bottom:6px}
.dn-file{font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:8px;word-break:break-all;
  padding:8px 12px;background:var(--surface2);border-radius:8px;border:1px solid var(--border)}
.new-btn{margin-top:18px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;
  border-radius:50px;padding:12px 32px;color:#080b12;font-weight:800;font-size:14px;cursor:pointer;font-family:var(--font)}
.err{display:none;padding:16px;margin-top:12px;background:rgba(252,129,129,.04);
  border:1px solid rgba(252,129,129,.15);border-radius:12px;color:var(--red);font-size:13px;line-height:1.6}.err.on{display:block}
.save-path{display:flex;align-items:center;gap:8px;padding:10px 14px;background:rgba(13,16,24,.7);
  border:1px solid var(--border);border-radius:12px;font-size:11px;color:var(--muted);margin-bottom:12px;font-family:var(--mono)}
.save-path span{flex:1;word-break:break-all;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.history{margin-top:8px}
.hist-header{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;font-family:var(--mono)}
.hist-item{display:flex;align-items:center;gap:10px;padding:11px 14px;background:rgba(13,16,24,.7);
  border:1px solid var(--border);border-radius:12px;margin-bottom:7px;font-size:12px}
.hist-icon{width:34px;height:34px;flex-shrink:0;background:var(--surface2);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:14px}
.hist-body{flex:1;min-width:0}
.hist-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.hist-meta{font-size:10px;color:var(--muted);font-family:var(--mono)}
.hist-q{font-size:10px;padding:2px 8px;border-radius:6px;background:rgba(99,179,237,.08);border:1px solid rgba(99,179,237,.15);color:var(--accent);font-family:var(--mono);flex-shrink:0}
.pl-card{display:none;animation:fadeUp .35s ease}.pl-card.on{display:block}
.pl-header{padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.pl-title-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.pl-name{font-size:15px;font-weight:800;line-height:1.3}
.pl-count{font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:2px}
.pl-sel-row{display:flex;gap:8px}
.sel-btn{padding:7px 14px;border-radius:9px;font-size:12px;font-weight:700;cursor:pointer;border:1px solid;transition:all .18s;font-family:var(--font)}
.sel-btn.all{background:rgba(99,179,237,.1);border-color:rgba(99,179,237,.25);color:var(--accent)}
.sel-btn.none{background:rgba(252,129,129,.07);border-color:rgba(252,129,129,.2);color:var(--red)}
.pl-list{max-height:420px;overflow-y:auto;padding:8px 0}
.pl-list::-webkit-scrollbar{width:4px}
.pl-list::-webkit-scrollbar-thumb{background:var(--muted);border-radius:2px}
.pl-item{display:flex;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid rgba(255,255,255,.03);cursor:pointer;transition:background .18s}
.pl-item:hover{background:rgba(99,179,237,.04)}
.pl-item.selected{background:rgba(99,179,237,.05)}
.pl-item.selected .pl-cb{border-color:var(--accent);background:var(--accent)}
.pl-item.selected .pl-cb::after{opacity:1}
.pl-thumb{width:80px;height:52px;border-radius:8px;overflow:hidden;flex-shrink:0;background:var(--surface2);position:relative}
.pl-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.pl-thumb .pl-dur{position:absolute;bottom:3px;left:3px;background:rgba(0,0,0,.75);color:#fff;font-size:9px;padding:1px 4px;border-radius:4px;font-family:var(--mono)}
.pl-meta{flex:1;min-width:0}
.pl-vtitle{font-size:13px;font-weight:600;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.pl-vdur{font-size:11px;color:var(--muted);font-family:var(--mono)}
.pl-cb{width:20px;height:20px;border-radius:6px;border:2px solid var(--muted);flex-shrink:0;transition:all .18s;position:relative;background:transparent}
.pl-cb::after{content:'✓';position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#080b12;opacity:0;transition:opacity .15s}
.pl-quality-wrap{padding:14px 16px 16px;border-top:1px solid var(--border)}
.pl-q-label{font-size:10px;font-weight:700;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;font-family:var(--mono)}
.pl-total-size{font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:10px;padding:8px 12px;background:var(--surface2);border-radius:8px}
.pl-total-size b{color:var(--gold)}
.pl-start-btn{width:100%;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:12px;padding:13px;color:#080b12;font-weight:800;font-size:14px;cursor:pointer;font-family:var(--font);margin-top:4px}
.pl-start-btn:disabled{opacity:.4;cursor:not-allowed}
.pl-pg{display:none;padding:18px;animation:fadeUp .3s ease}.pl-pg.on{display:block}
.pl-pg-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px}
.pl-pg-title{font-size:13px;font-weight:700;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:8px}
.pl-pg-count{font-size:11px;font-family:var(--mono);color:var(--accent);flex-shrink:0;font-weight:700}
.pl-pg-step{font-size:11px;color:var(--muted);margin-bottom:10px}
.pl-overall-bg{height:5px;background:rgba(255,255,255,.05);border-radius:99px;overflow:hidden;margin-bottom:6px}
.pl-overall-fill{height:100%;background:linear-gradient(90deg,var(--pu),var(--accent));border-radius:99px;transition:width .5s ease}
.pl-cur-bg{height:3px;background:rgba(255,255,255,.04);border-radius:99px;overflow:hidden;margin-bottom:6px}
.pl-cur-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:99px;transition:width .35s ease}
.pl-pg-row{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-bottom:12px;font-family:var(--mono)}
.pl-cancel-btn{width:100%;background:rgba(252,129,129,.06);border:1px solid rgba(252,129,129,.15);color:var(--red);border-radius:10px;padding:9px;cursor:pointer;font-size:13px;font-weight:700;font-family:var(--font)}
.pl-done{display:none;padding:20px;animation:fadeUp .4s ease}.pl-done.on{display:block}
.pl-done-header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.pl-done-icon{font-size:32px}
.pl-done-title{font-size:16px;font-weight:800;color:var(--accent2)}
.pl-done-sub{font-size:12px;color:var(--muted);font-family:var(--mono);margin-top:3px}
.failed-section{margin-top:14px}
.failed-title{font-size:11px;font-weight:700;color:var(--red);letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;font-family:var(--mono)}
.failed-item{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;background:rgba(252,129,129,.04);border:1px solid rgba(252,129,129,.15);border-radius:10px;margin-bottom:7px}
.failed-thumb{width:60px;height:40px;border-radius:6px;overflow:hidden;flex-shrink:0}
.failed-thumb img{width:100%;height:100%;object-fit:cover}
.failed-body{flex:1;min-width:0}
.failed-name{font-size:12px;font-weight:600;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.failed-err{font-size:10px;color:var(--red);opacity:.75;line-height:1.4}
.retry-btn{padding:6px 12px;background:rgba(99,179,237,.1);border:1px solid rgba(99,179,237,.25);border-radius:8px;color:var(--accent);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--font);white-space:nowrap;flex-shrink:0}
.pl-new-btn{margin-top:16px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;border-radius:50px;padding:11px 28px;color:#080b12;font-weight:800;font-size:14px;cursor:pointer;font-family:var(--font)}
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas><div id="bgOverlay"></div>
<div class="w">
  <div class="header">
    <div class="logo-wrap"><div class="logo-icon">B</div><div><div class="logo-text">B-Ultra</div></div><div class="logo-ver">v14</div></div>
    <div class="tagline">محمّل فيديو سريع وخفيف</div>
  </div>
  <div class="card input-card">
    <div class="mode-tabs">
      <button class="tab vid on" id="mV" onclick="setMode('video')">🎬 فيديو</button>
      <button class="tab aud" id="mA" onclick="setMode('audio')">🎵 MP3</button>
    </div>
    <div class="url-row">
      <div class="input-wrapper">
        <input id="uI" class="url-input" type="text" placeholder="رابط فيديو أو قائمة تشغيل...">
        <span id="clearBtn" class="clear-btn" onclick="clearInput()">✖</span>
      </div>
      <button class="paste-btn" onclick="doPaste()">📋 لصق</button>
    </div>
  </div>
  <div class="spinner-wrap" id="sp"><div class="spinner"></div><div class="spinner-txt" id="sp-txt">جارٍ التحليل...</div></div>
  <div class="card vc" id="vc">
    <div class="thumb-wrap"><img id="vth" src="" alt="">
      <div class="thumb-overlay"><div class="vid-title" id="vt"></div><div class="vid-meta"><span id="vm"></span></div></div>
    </div>
    <div class="quality-wrap"><div class="q-label">اختر الجودة</div><div class="q-grid" id="qw"></div></div>
  </div>
  <div class="card pg" id="pg">
    <div class="pg-header"><div class="pg-status" id="pst">جارٍ...</div><div class="pg-speed"><div class="speed-dot"></div><span id="psp">—</span></div></div>
    <div class="track-bg"><div class="track-fill" id="fi"></div></div>
    <div class="pg-row"><span class="pg-pct" id="pp">0%</span><span id="pe">—</span></div>
    <button class="cancel-btn" onclick="doCancel()">✕ إلغاء</button>
  </div>
  <div class="card dn" id="dn"><div class="dn-ring">✅</div><div class="dn-title">اكتمل!</div><div class="dn-file" id="df"></div><button class="new-btn" onclick="resetAll()">تحميل جديد</button></div>
  <div class="err" id="er"><div style="font-size:18px;margin-bottom:6px">⚠️</div><span id="er-msg"></span></div>
  <div class="card pl-card" id="plCard">
    <div class="pl-header">
      <div class="pl-title-row"><span style="font-size:22px">📋</span><div><div class="pl-name" id="plName">قائمة</div><div class="pl-count" id="plCount">0 فيديو</div></div></div>
      <div class="pl-sel-row"><button class="sel-btn all" onclick="selectAll(true)">✅ تحديد الكل</button><button class="sel-btn none" onclick="selectAll(false)">✕ إلغاء</button></div>
    </div>
    <div class="pl-list" id="plList"></div>
    <div class="pl-quality-wrap">
      <div class="pl-q-label">جودة التحميل</div>
      <div class="pl-total-size" id="plTotalSize">اختر جودة</div>
      <div class="q-grid" id="plQw"></div>
      <button class="pl-start-btn" id="plStartBtn" onclick="startPlaylist()" disabled>⬇️ بدء التحميل</button>
    </div>
  </div>
  <div class="card pl-pg" id="plPg">
    <div class="pl-pg-header"><div class="pl-pg-title" id="plPgTitle">—</div><div class="pl-pg-count" id="plPgCount">0/0</div></div>
    <div class="pl-pg-step" id="plPgStep"></div>
    <div class="pl-overall-bg"><div class="pl-overall-fill" id="plOverall" style="width:0%"></div></div>
    <div class="pl-cur-bg"><div class="pl-cur-fill" id="plCur" style="width:0%"></div></div>
    <div class="pl-pg-row"><span id="plPgPct">0%</span><span id="plPgSpd">—</span><span id="plPgEta">—</span></div>
    <button class="pl-cancel-btn" onclick="cancelPlaylist()">✕ إيقاف</button>
  </div>
  <div class="card pl-done" id="plDone">
    <div class="pl-done-header"><div class="pl-done-icon">🏁</div><div><div class="pl-done-title" id="plDoneTitle">اكتمل!</div><div class="pl-done-sub" id="plDoneSub"></div></div></div>
    <div class="failed-section" id="plFailedSection" style="display:none"><div class="failed-title">❌ فاشلة</div><div id="plFailedList"></div></div>
    <button class="pl-new-btn" onclick="resetAll()">تحميل جديد</button>
  </div>
  <div class="save-path"><span style="font-size:14px;flex-shrink:0">📁</span><span id="sp2">...</span></div>
  <div class="history" id="hS" style="display:none"><div class="hist-header">⏱ آخر التحميلات</div><div id="hL"></div></div>
</div>
<script>
(function(){
  const c=document.getElementById('bgCanvas'),x=c.getContext('2d');
  let W,H,P=[],M={x:-999,y:-999};
  const R=110,F=0.38;
  function rsz(){W=c.width=innerWidth;H=c.height=innerHeight}
  function mkP(){const p={x:Math.random()*W,y:Math.random()*H,vx:0,vy:0,r:1.1+Math.random()*1.6,
    wA:8+Math.random()*14,wF:0.0006+Math.random()*0.0008,
    wX:Math.random()*Math.PI*2,wY:Math.random()*Math.PI*2,
    h:185+Math.random()*30,a:0.18+Math.random()*0.32};p.ox=p.x;p.oy=p.y;return p;}
  function init(){P=[];for(let i=0;i<Math.floor(W*H/6800);i++)P.push(mkP())}
  function draw(t){
    requestAnimationFrame(draw);x.clearRect(0,0,W,H);
    for(const p of P){
      const wx=p.ox+Math.sin(t*p.wF+p.wX)*p.wA,wy=p.oy+Math.cos(t*p.wF*.7+p.wY)*p.wA*.6;
      const dx=wx-M.x,dy=wy-M.y,d=Math.sqrt(dx*dx+dy*dy);
      let rx=0,ry=0;if(d<R&&d>0){const f=(R-d)*(1-d/R)*F*2.2;rx=dx/d*f;ry=dy/d*f;}
      p.vx+=(rx-p.vx)*.12;p.vy+=(ry-p.vy)*.12;p.x=wx+p.vx;p.y=wy+p.vy;
      x.beginPath();x.arc(p.x,p.y,p.r,0,Math.PI*2);x.fillStyle=`hsla(${p.h},85%,72%,${p.a})`;x.fill();
      for(const q of P){if(q===p)continue;const d2=Math.sqrt((p.x-q.x)**2+(p.y-q.y)**2);
        if(d2<80){x.beginPath();x.moveTo(p.x,p.y);x.lineTo(q.x,q.y);x.strokeStyle=`hsla(${p.h},80%,70%,${.04*(1-d2/80)})`;x.lineWidth=.5;x.stroke();}}
    }}
  addEventListener('mousemove',e=>{M.x=e.clientX;M.y=e.clientY});
  addEventListener('mouseleave',()=>{M.x=-999;M.y=-999});
  addEventListener('touchmove',e=>{M.x=e.touches[0].clientX;M.y=e.touches[0].clientY},{passive:true});
  addEventListener('resize',()=>{rsz();init()});
  rsz();init();requestAnimationFrame(draw);
})();
let mode='video',pT=null,plT=null,curU='',analyzing=false;
let _F=[],_plEntries=[],_plSel=new Set(),_plFmts=[],_plFid='best',_plMode='video';
function setMode(m){mode=m;
  document.getElementById('mV').className='tab vid'+(m==='video'?' on':'');
  document.getElementById('mA').className='tab aud'+(m==='audio'?' on':'');
  if(_F.length)buildQ();if(_plFmts.length)buildPlQ();}
async function doPaste(){try{const t=await navigator.clipboard.readText();document.getElementById('uI').value=t;toggleClearBtn();maybeAn(t);}catch{document.getElementById('uI').focus();}}
const uI=document.getElementById('uI');

// دالة إظهار وإخفاء زر الحذف
function toggleClearBtn() {
  document.getElementById('clearBtn').style.display = uI.value.length > 0 ? 'block' : 'none';
}

// دالة تصفير الحقل عند الضغط على ✖
function clearInput() {
  resetAll(); // هذه الدالة موجودة مسبقاً في كودك وتقوم بتنظيف كل شيء وتفريغ الحقل
  toggleClearBtn();
}

uI.addEventListener('input',function(){
  toggleClearBtn();
  const v=this.value.trim();
  if(v.startsWith('http')&&!analyzing)maybeAn(v);
});

uI.addEventListener('paste',function(){
  setTimeout(()=>{
    toggleClearBtn();
    const v=this.value.trim();
    if(v.startsWith('http')&&!analyzing)maybeAn(v);
  },80);
});
function maybeAn(url){if(url===curU)return;curU=url;doAn(url);}
function doAn(url){analyzing=true;hideAll();on('sp');
  fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})})
  .then(r=>r.json()).then(d=>{off('sp');analyzing=false;
    if(d.error){showEr(d.error);curU='';return;}
    if(d.is_playlist)showPlaylist(d);else showCard(d);
  }).catch(e=>{off('sp');analyzing=false;showEr(e.message);curU='';});}
function showCard(data){_F=data.formats||[];
  document.getElementById('vth').src=data.thumb||'';
  document.getElementById('vt').textContent=data.title||'فيديو';
  document.getElementById('vm').textContent=data.duration?fmtDur(data.duration):'';
  buildQ();on('vc');}
function buildQ(){const w=document.getElementById('qw');w.innerHTML='';
  if(mode==='audio'){w.appendChild(mkQBtn('🎵 أفضل جودة صوت — MP3','best','audio',0,true));return;}
  w.appendChild(mkQBtn('🔥 أفضل جودة','best','video',0,true));
  _F.slice().reverse().forEach(f=>w.appendChild(mkQBtn(`${f.res}p${f.fps?' · '+f.fps+'fps':''}`,f.id,'video',f.size,false)));}
function mkQBtn(lbl,fid,m,size,isBest){
  const b=document.createElement('button');b.className='qbtn'+(isBest?(m==='audio'?' aud-best':' best'):'');
  const sp=document.createElement('span');sp.textContent=lbl;b.appendChild(sp);
  if(isBest){const a=document.createElement('span');a.className='best-arrow';a.textContent='→';b.appendChild(a);}
  else if(size>0){const s=document.createElement('span');s.className='q-size';s.textContent=fmtSz(size);b.appendChild(s);}
  else{const s=document.createElement('span');s.className='q-size approx';s.textContent='~?';b.appendChild(s);}
  b.onclick=()=>startDL(curU,fid,m);return b;}
function startDL(url,fid,m){off('vc');on('pg');
  document.getElementById('pst').textContent='جارٍ التحميل...';document.getElementById('fi').style.width='0%';
  fetch('/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,format_id:fid,mode:m})});
  if(pT)clearInterval(pT);pT=setInterval(doPoll,700);}
function doPoll(){fetch('/progress').then(r=>r.json()).then(d=>{
  if(d.step)document.getElementById('pst').textContent=d.step;
  if(d.phase==='downloading'){document.getElementById('fi').style.width=d.percent+'%';
    document.getElementById('pp').textContent=d.percent.toFixed(1)+'%';
    document.getElementById('psp').textContent=d.speed||'';
    document.getElementById('pe').textContent=d.eta?'ETA '+d.eta:'';}
  else if(d.phase==='merging'){document.getElementById('fi').style.width='100%';document.getElementById('fi').style.background='var(--gold)';}
  else if(d.phase==='finished'){clearInterval(pT);off('pg');document.getElementById('df').textContent='📄 '+d.filename;on('dn');loadH();}
  else if(d.phase==='error'){clearInterval(pT);off('pg');showEr(d.error||'خطأ');}});}
function doCancel(){fetch('/cancel');clearInterval(pT);resetAll();}
function showPlaylist(data){_plEntries=data.entries||[];_plFmts=data.formats||[];
  _plSel=new Set(_plEntries.map(e=>e.index));
  document.getElementById('plName').textContent=data.pl_title||'قائمة تشغيل';
  document.getElementById('plCount').textContent=_plEntries.length+' فيديو';
  buildPlList();buildPlQ();on('plCard');}
function buildPlList(){const list=document.getElementById('plList');list.innerHTML='';
  _plEntries.forEach(e=>{const sel=_plSel.has(e.index);
    const div=document.createElement('div');div.className='pl-item'+(sel?' selected':'');div.dataset.idx=e.index;
    div.innerHTML=`<div class="pl-thumb"><img src="${esc(e.thumb)}" loading="lazy" onerror="this.src=''">
      ${e.duration?`<div class="pl-dur">${fmtDur(e.duration)}</div>`:''}</div>
      <div class="pl-meta"><div class="pl-vtitle">${esc(e.title)}</div><div class="pl-vdur">${e.duration?fmtDur(e.duration):''}</div></div>
      <div class="pl-cb"></div>`;
    div.onclick=()=>toggleSel(e.index,div);list.appendChild(div);});updateSelCount();}
function toggleSel(idx,el){if(_plSel.has(idx)){_plSel.delete(idx);el.classList.remove('selected');}
  else{_plSel.add(idx);el.classList.add('selected');}updateSelCount();updateTotalSize();}
function selectAll(v){if(v)_plEntries.forEach(e=>_plSel.add(e.index));else _plSel.clear();
  document.querySelectorAll('.pl-item').forEach(el=>{const idx=parseInt(el.dataset.idx);
    if(_plSel.has(idx))el.classList.add('selected');else el.classList.remove('selected');});
  updateSelCount();updateTotalSize();}
function updateSelCount(){document.getElementById('plCount').textContent=`${_plEntries.length} فيديو · ${_plSel.size} محدد`;
  document.getElementById('plStartBtn').disabled=(_plSel.size===0);}
function buildPlQ(){const w=document.getElementById('plQw');w.innerHTML='';
  if(mode==='audio'){w.appendChild(mkPlQBtn('🎵 MP3','best','audio',true));_plFid='best';_plMode='audio';updateTotalSize();return;}
  const bb=mkPlQBtn('🔥 أفضل جودة','best','video',true);w.appendChild(bb);
  _plFmts.slice().reverse().forEach(f=>w.appendChild(mkPlQBtn(`${f.res}p${f.fps?' · '+f.fps+'fps':''}`,f.id,'video',false,f.size)));
  _plFid='best';_plMode='video';bb.classList.add('sel-active');updateTotalSize();}
function mkPlQBtn(lbl,fid,m,isBest,size){
  const b=document.createElement('button');b.className='qbtn'+(isBest?' best':'');
  const s=document.createElement('span');s.textContent=lbl;b.appendChild(s);
  if(isBest){const a=document.createElement('span');a.className='best-arrow';a.textContent='→';b.appendChild(a);}
  else if(size>0){const sz=document.createElement('span');sz.className='q-size';sz.textContent=fmtSz(size);b.appendChild(sz);}
  b.onclick=()=>{document.querySelectorAll('#plQw .qbtn').forEach(x=>x.classList.remove('sel-active'));
    b.classList.add('sel-active');_plFid=fid;_plMode=m;updateTotalSize();};return b;}
function updateTotalSize(){if(_plFid==='best'||_plMode==='audio'){document.getElementById('plTotalSize').innerHTML='الحجم: <b>حسب كل فيديو</b>';return;}
  const fmt=_plFmts.find(f=>f.id===_plFid);
  if(!fmt||!fmt.size){document.getElementById('plTotalSize').innerHTML='الحجم: <b>~غير معروف</b>';return;}
  document.getElementById('plTotalSize').innerHTML=`الحجم لـ <b>${_plSel.size} فيديو</b> × ${fmt.res}p: <b>${fmtSz(fmt.size*_plSel.size)}</b>`;}
function startPlaylist(){const sel=_plEntries.filter(e=>_plSel.has(e.index));if(!sel.length)return;
  off('plCard');on('plPg');document.getElementById('plPgTitle').textContent='جارٍ...';
  document.getElementById('plPgCount').textContent=`0/${sel.length}`;
  fetch('/pl_download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:sel,format_id:_plFid,mode:_plMode})});
  if(plT)clearInterval(plT);plT=setInterval(pollPlaylist,800);}
function pollPlaylist(){fetch('/pl_progress').then(r=>r.json()).then(d=>{
  if(d.phase==='idle')return;
  document.getElementById('plPgTitle').textContent=d.current_title||'—';
  document.getElementById('plPgCount').textContent=`${d.current_index}/${d.total}`;
  document.getElementById('plPgStep').textContent=d.step||'';
  document.getElementById('plPgPct').textContent=d.current_percent.toFixed(1)+'%';
  document.getElementById('plPgSpd').textContent=d.current_speed||'—';
  document.getElementById('plPgEta').textContent=d.current_eta?'ETA '+d.current_eta:'—';
  document.getElementById('plOverall').style.width=(d.total>0?(d.done_count/d.total*100):0)+'%';
  document.getElementById('plCur').style.width=d.current_percent+'%';
  if(d.phase==='done'){clearInterval(plT);showPlDone(d);}});}
function showPlDone(d){off('plPg');const f=d.failed?.length||0;
  document.getElementById('plDoneTitle').textContent=f===0?'✅ اكتمل بنجاح!':'⚠️ اكتمل مع أخطاء';
  document.getElementById('plDoneSub').textContent=`${d.done_count} فيديو ✅`+(f?` · ${f} فاشل ❌`:'');
  const fs=document.getElementById('plFailedSection');
  if(f>0){fs.style.display='block';const list=document.getElementById('plFailedList');list.innerHTML='';
    (d.failed||[]).forEach(v=>{list.innerHTML+=`<div class="failed-item">
      <div class="failed-thumb"><img src="${esc(v.thumb)}" onerror="this.src=''"></div>
      <div class="failed-body"><div class="failed-name">${esc(v.title)}</div><div class="failed-err">${esc(v.error)}</div></div>
      <button class="retry-btn" onclick="retryFailed(${v.index})">🔄</button></div>`;});}
  else fs.style.display='none';on('plDone');loadH();}
function retryFailed(idx){const e=_plEntries.find(e=>e.index===idx);if(!e)return;
  fetch('/pl_download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:[e],format_id:_plFid,mode:_plMode})});
  off('plDone');on('plPg');if(plT)clearInterval(plT);plT=setInterval(pollPlaylist,800);}
function cancelPlaylist(){fetch('/pl_cancel');clearInterval(plT);resetAll();}
function loadH(){fetch('/history').then(r=>r.json()).then(h=>{if(!h||!h.length)return;
  const list=document.getElementById('hL');list.innerHTML='';
  h.slice(0,6).forEach(it=>{const q=it.quality?`<div class="hist-q">${esc(it.quality)}</div>`:'';
    list.innerHTML+=`<div class="hist-item"><div class="hist-icon">${it.mode==='audio'?'🎵':'🎬'}</div>
      <div class="hist-body"><div class="hist-title">${esc(it.title)}</div>
      <div class="hist-meta">${it.date} · ${esc(it.file)}</div></div>${q}</div>`;});
  document.getElementById('hS').style.display='block';});}
fetch('/info').then(r=>r.json()).then(d=>{document.getElementById('sp2').textContent=d.save_path;});
window.onload=()=>{hideAll();loadH();};
function resetAll(){clearInterval(pT);clearInterval(plT);_F=[];_plEntries=[];_plSel=new Set();_plFmts=[];
  hideAll();uI.value='';uI.focus();curU='';analyzing=false;
  const cb=document.getElementById('clearBtn');if(cb)cb.style.display='none';}
function fmtDur(s){s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
  if(h>0)return`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;return`${m}:${String(sec).padStart(2,'0')}`;}
function fmtSz(n){if(!n||n<=0)return'';if(n>=1073741824)return(n/1073741824).toFixed(1)+'GB';
  if(n>=1048576)return Math.round(n/1048576)+'MB';return Math.round(n/1024)+'KB';}
function on(id){const e=document.getElementById(id);if(e){e.classList.add('on');e.style.display='';}}
function off(id){const e=document.getElementById(id);if(e){e.classList.remove('on');e.style.display='none';}}
function hideAll(){['vc','pg','dn','er','sp','plCard','plPg','plDone'].forEach(off);}
function showEr(m){document.getElementById('er-msg').textContent=m;on('er');}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
</script></body></html>"""

# ── Flask ──
import logging as _logging
app = Flask(__name__)

@app.route('/')
def index(): return render_template_string(HTML)

@app.route('/info')
def info_r(): return jsonify({"save_path": SAVE_PATH})

@app.route('/analyze', methods=['POST'])
def analyze_r():
    url = (request.json or {}).get('url','').strip()
    if not url: return jsonify({'error':'لا يوجد رابط'})
    try:
        if is_playlist_url(url):
            d = analyze_playlist(url)
            return jsonify({'is_playlist':True,'pl_title':d['pl_title'],
                            'entries':d['entries'],'formats':d['formats']})
        else:
            d = analyze_url(url)
            return jsonify({'is_playlist':False,'title':d['title'],'duration':d['duration'],
                            'thumb':d['thumb'],'formats':d['formats']})
    except Exception as e:
        LOG(f"❌ /analyze: {e}", "ERROR")
        return jsonify({'error': str(e)[:300]})

@app.route('/download', methods=['POST'])
def download_r():
    b = request.json or {}
    if download_lock.locked(): return jsonify({'status':'busy'})
    threading.Thread(target=run_download,
        args=(b.get('url',''),b.get('format_id','best'),b.get('mode','video')),
        daemon=True).start()
    return jsonify({'status':'started'})

@app.route('/pl_download', methods=['POST'])
def pl_download_r():
    b = request.json or {}
    if playlist_lock.locked(): pl_stop_flag.set(); time.sleep(0.5)
    threading.Thread(target=run_playlist_download,
        args=(b.get('entries',[]),b.get('format_id','best'),b.get('mode','video')),
        daemon=True).start()
    return jsonify({'status':'started'})

@app.route('/pl_cancel')
def pl_cancel_r(): pl_stop_flag.set(); return jsonify({'status':'cancelled'})
@app.route('/pl_progress')
def pl_progress_r(): return jsonify(playlist_state)
@app.route('/cancel')
def cancel_r(): stop_flag.set(); return jsonify({'status':'cancelled'})
@app.route('/progress')
def prog_r(): return jsonify(state)
@app.route('/history')
def hist_r(): return jsonify(load_history())

def open_browser():
    """فتح المتصفح — يعمل في Termux وAndroid APK"""
    time.sleep(2)
    url = "http://localhost:8000"
    cmds = [
        ["termux-open-url", url],
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
        ["xdg-open", url],
    ]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5)
            if r.returncode == 0:
                LOG(f"✅ متصفح: {cmd[0]}")
                return
        except Exception as e:
            LOG(f"  {cmd[0]}: {e}", "WARN")
    LOG("⚠️ افتح http://localhost:8000 يدوياً", "WARN")

def cli_listen():
    """آمن في APK — لا يتعطل بدون stdin"""
    while True:
        try:
            line = input()
            if line.strip().lower() in ['q','exit','0','']:
                LOG("👋 إغلاق")
                os._exit(0)
        except EOFError:
            time.sleep(60)
        except Exception:
            time.sleep(60)

if __name__ == '__main__':
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)
    LOG(f"🚀 Flask يبدأ على 0.0.0.0:8000")
    threading.Thread(target=open_browser, daemon=True).start()
    threading.Thread(target=cli_listen,   daemon=True).start()
    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)
