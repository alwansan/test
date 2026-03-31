"""
B-Ultra v12
══════════════════════════════════════════════
جديد في v12:
  ✅ تحميل قوائم التشغيل الكاملة (Playlist)
  ✅ اختيار فيديوهات محددة من القائمة
  ✅ عرض الحجم الإجمالي حسب الجودة المختارة
  ✅ تحميل متسلسل مع عرض حالة كل فيديو
  ✅ معالجة الأخطاء مع خيار الاستئناف
  ✅ استئناف التحميل المنقطع (continue-dl)
  ✅ إصلاح عرض الصورة الكاملة في بطاقة الفيديو
"""

import os, sys, subprocess, threading, time, json, logging, re
from pathlib import Path
from datetime import datetime

def pip(pkg, upgrade=False):
    flags = ["--quiet","--break-system-packages"]
    for cmd in [
        [sys.executable,"-m","pip","install"]+( ["--upgrade"] if upgrade else [])+[pkg]+flags,
        [sys.executable,"-m","pip","install"]+( ["--upgrade"] if upgrade else [])+[pkg,"--quiet"],
    ]:
        try:
            if subprocess.run(cmd, capture_output=True).returncode==0: return True
        except: continue
    return False

try: from flask import Flask, render_template_string, request, jsonify
except ImportError: pip("flask"); from flask import Flask, render_template_string, request, jsonify

pip("yt-dlp", upgrade=True)
try: import yt_dlp
except ImportError: pip("yt-dlp"); import yt_dlp
print(f"✅ yt-dlp v{yt_dlp.version.__version__}")

def get_save_path():
    for p in ["/storage/emulated/0/Download/B-Ultra",
              os.path.expanduser("~/storage/downloads/B-Ultra"),
              os.path.join(os.path.expanduser("~"),"Downloads","B-Ultra")]:
        try:
            os.makedirs(p,exist_ok=True); t=os.path.join(p,"._t")
            open(t,"w").close(); os.remove(t); return p
        except: continue
    fb=os.path.join(os.path.dirname(os.path.abspath(__file__)),"Downloads")
    os.makedirs(fb,exist_ok=True); return fb

SAVE_PATH    = get_save_path()
HISTORY_FILE = os.path.join(SAVE_PATH,".history.json")

# ─── Logging System ───
LOG_FILE = os.path.join(SAVE_PATH, "log.txt")

class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, data):
        for f in self.files:
            f.write(data)
            f.flush()  # تحديث مباشر

    def flush(self):
        for f in self.files:
            f.flush()

# فتح ملف اللوق
log_f = open(LOG_FILE, "a", encoding="utf-8")

# ربط print + errors بالملف
sys.stdout = Tee(sys.stdout, log_f)
sys.stderr = Tee(sys.stderr, log_f)

print(f"📝 Logging started → {LOG_FILE}")

UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

def fmt_size(n):
    if not n or n<=0: return ""
    if n>=1_073_741_824: return f"{n/1_073_741_824:.1f}GB"
    if n>=1_048_576:     return f"{n/1_048_576:.0f}MB"
    return f"{n/1024:.0f}KB"

# ══ حالة التحميل الفردي ══
state = {"phase":"idle","percent":0,"speed":"","eta":"","filename":"","error":"","step":""}

# ══ حالة تحميل القائمة ══
playlist_state = {
    "phase": "idle",       # idle | fetching | downloading | done
    "total": 0,
    "current_index": 0,    # الفيديو الحالي (1-based)
    "current_title": "",
    "current_percent": 0,
    "current_speed": "",
    "current_eta": "",
    "items": [],           # قائمة حالة كل فيديو
    "failed": [],          # الفاشلة
    "done_count": 0,
    "step": "",
}

stop_flag          = threading.Event()
pl_stop_flag       = threading.Event()
download_lock      = threading.Lock()
playlist_lock      = threading.Lock()

def load_history():
    try:
        if os.path.exists(HISTORY_FILE): return json.load(open(HISTORY_FILE,encoding="utf-8"))
    except: pass
    return []

def save_history(entry):
    h=load_history(); h.insert(0,entry); h=h[:50]
    try: json.dump(h,open(HISTORY_FILE,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    except: pass

class Q:
    def debug(self, m):
        print(f"[DEBUG] {m}")

    def warning(self, m):
        print(f"[WARN] {m}")

    def error(self, m):
        print(f"[ERROR] {m}")

def hook(d):
    if stop_flag.is_set(): raise Exception("Cancelled")
    if d["status"]=="downloading":
        total=d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        done=d.get("downloaded_bytes",0); spd=d.get("speed") or 0; eta=d.get("eta") or 0
        state.update({"phase":"downloading","percent":round(done/total*100,1),
                      "speed":f"{spd/1024/1024:.2f} MB/s" if spd else "...","eta":f"{int(eta//60):02d}:{int(eta%60):02d}"})
    elif d["status"]=="finished":
        state.update({"phase":"merging","step":"🔧 دمج الفيديو والصوت..."})

def pl_hook(d):
    """hook خاص بقائمة التشغيل"""
    if pl_stop_flag.is_set(): raise Exception("Cancelled")
    if d["status"]=="downloading":
        total=d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        done=d.get("downloaded_bytes",0); spd=d.get("speed") or 0; eta=d.get("eta") or 0
        playlist_state["current_percent"] = round(done/total*100,1)
        playlist_state["current_speed"]   = f"{spd/1024/1024:.2f} MB/s" if spd else "..."
        playlist_state["current_eta"]     = f"{int(eta//60):02d}:{int(eta%60):02d}"
    elif d["status"]=="finished":
        playlist_state["step"] = "🔧 دمج..."

def opts_base():
    return {"quiet":True,"no_warnings":True,"user_agent":UA,"logger":Q(),
            "nocheckcertificate":True,"socket_timeout":30,"retries":5}

# ══════════════════════════════════════════════
#  كشف نوع الرابط
# ══════════════════════════════════════════════
def is_playlist_url(url):
    return ("playlist?list=" in url or
            ("list=" in url and "youtube" in url and "watch" not in url) or
            "/playlist" in url)

# ══════════════════════════════════════════════
#  حساب الحجم الذكي (DASH = فيديو + صوت)
# ══════════════════════════════════════════════
def get_smart_size(video_fmt, all_formats):
    v_size = video_fmt.get("filesize") or video_fmt.get("filesize_approx") or 0
    has_audio = video_fmt.get("acodec","none") != "none"
    if has_audio: return v_size
    best_audio = None; best_abr = 0
    for f in all_formats:
        if f.get("vcodec","none") != "none": continue
        if f.get("acodec","none") == "none": continue
        abr = f.get("abr") or f.get("tbr") or 0
        a_size = f.get("filesize") or f.get("filesize_approx") or 0
        if a_size > 0 and abr >= best_abr:
            best_abr = abr; best_audio = f
    if best_audio:
        a_size = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
        return (v_size + a_size) if (v_size + a_size) > 0 else 0
    return v_size

def extract_video_formats(formats):
    seen={}
    for f in formats:
        h=f.get("height"); vc=f.get("vcodec","none")
        if vc=="none" or not h: continue
        fid=f.get("format_id",""); ext=f.get("ext","?"); fps=f.get("fps") or 0
        url_=f.get("url","") or ""; is_dash="m3u8" not in url_ and url_.startswith("http")
        size=f.get("filesize") or f.get("filesize_approx") or 0
        ex=seen.get(h)
        if not ex: seen[h]={"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
        elif is_dash and not ex["dash"]: seen[h]={"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
        elif is_dash==ex["dash"]:
            if ext=="mp4" and ex["ext"]!="mp4": seen[h]={"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
            elif ext==ex["ext"] and fps>ex["fps"]: seen[h]={"id":fid,"res":h,"ext":ext,"fps":fps,"dash":is_dash,"size":size,"_fmt":f}
    result=[]
    for h,v in seen.items():
        smart=get_smart_size(v["_fmt"], formats)
        entry=dict(v); entry["size"]=smart; del entry["_fmt"]
        result.append(entry)
    return sorted(result,key=lambda x:x["res"])

# ══════════════════════════════════════════════
#  تحليل فيديو واحد
# ══════════════════════════════════════════════
def analyze_url(url):
    print(f"\n🔍 تحليل: {url}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n=== ANALYZE ===\nURL: {url}\nTime: {datetime.now()}\n")
    title=""; duration=0; thumb=""; all_formats=[]
    try:
        with yt_dlp.YoutubeDL(opts_base()) as ydl:
            info=ydl.extract_info(url,download=False)
        all_formats=list(info.get("formats",[]))
        title=info.get("title",""); duration=info.get("duration",0); thumb=info.get("thumbnail","")
    except Exception as e:
        print(f"   ❌ {str(e)[:80]}")
    video_fmts=extract_video_formats(all_formats)
    return {"title":title,"duration":duration,"thumb":thumb,
            "formats":video_fmts,"all_formats":all_formats}

# ══════════════════════════════════════════════
#  تحليل قائمة تشغيل — الخطوات السريعة
# ══════════════════════════════════════════════
def analyze_playlist(url):
    """
    يجلب بيانات القائمة بدون تحليل كل فيديو بشكل كامل.
    يستخدم extract_flat لجلب العناوين والصور فقط.
    ثم يحلل أول فيديو للحصول على الجودات المتاحة.
    """
    print(f"\n📋 تحليل قائمة: {url}")
    opts = opts_base()
    opts.update({
        "extract_flat": "in_playlist",
        "playlistend":  500,   # حد أقصى
        "ignoreerrors": True,
    })

    entries = []
    pl_title = ""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        pl_title = info.get("title","قائمة تشغيل")
        raw = info.get("entries",[]) or []
        for i,e in enumerate(raw):
            if not e: continue
            vid_id = e.get("id","")
            vid_url = e.get("url","") or f"https://www.youtube.com/watch?v={vid_id}"
            if not vid_url.startswith("http"):
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
            # الصورة المصغرة
            thumb = e.get("thumbnail","")
            if not thumb and vid_id:
                thumb = f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg"
            entries.append({
                "index":   i+1,
                "id":      vid_id,
                "url":     vid_url,
                "title":   e.get("title","فيديو "+str(i+1)),
                "duration": e.get("duration",0) or 0,
                "thumb":   thumb,
            })
        print(f"   📋 {len(entries)} فيديو في القائمة")
    except Exception as e:
        print(f"   ❌ {str(e)[:120]}")
        raise

    # تحليل أول فيديو للحصول على الجودات
    formats = []
    if entries:
        try:
            first = analyze_url(entries[0]["url"])
            formats = first["formats"]
            # أضف مدة/جودة للفيديو الأول
            if not entries[0]["duration"] and first["duration"]:
                entries[0]["duration"] = first["duration"]
        except: pass

    return {"pl_title": pl_title, "entries": entries, "formats": formats}

# ══════════════════════════════════════════════
#  اختيار صيغة التحميل
# ══════════════════════════════════════════════
def pick_format(format_id, all_formats, mode):
    if mode=="audio": return "bestaudio/best","mp3"
    if format_id=="best": return "bestvideo+bestaudio/best",None
    sel=next((f for f in all_formats if f["format_id"]==format_id),None)
    if not sel: return f"{format_id}+bestaudio/best",None
    v_ext=sel.get("ext","mp4"); has_audio=sel.get("acodec","none")!="none"
    if has_audio: return format_id,None
    if v_ext=="webm": return f"{format_id}+bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio","webm"
    return f"{format_id}+bestaudio[ext=m4a]/bestaudio[acodec=aac]/bestaudio","mp4"

def quality_label(format_id, all_formats, mode):
    if mode=="audio": return "MP3"
    if format_id=="best": return "best"
    sel=next((f for f in all_formats if f["format_id"]==format_id),None)
    if sel:
        h=sel.get("height")
        if h: return f"{h}p"
    return format_id

# ══════════════════════════════════════════════
#  تحميل فيديو واحد (الوضع العادي)
# ══════════════════════════════════════════════
def run_download(url, format_id, mode):
    with download_lock:
        stop_flag.clear()
        state.update({"phase":"downloading","percent":0,"speed":"جارٍ...","eta":"--:--",
                      "filename":"","error":"","step":"🔍 تحليل..."})
        try:
            data=analyze_url(url)
            safe_title=re.sub(r'[\\/*?:"<>|]',"_",data["title"])
            all_fmts=data["all_formats"]
            req_fmt,merge_ext=pick_format(format_id,all_fmts,mode)
            qlabel=quality_label(format_id,all_fmts,mode)
            state["step"]="⬇️ جارٍ التحميل..."
            out_name = f"{safe_title} [{qlabel}]"
            
            # 🔥 LOG DOWNLOAD (حطه هنا بالضبط)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n=== DOWNLOAD ===\n")
                f.write(f"Title: {data['title']}\n")
                f.write(f"URL: {url}\n")
                f.write(f"Format: {req_fmt}\n")
                f.write(f"Mode: {mode}\n")
                f.write(f"Time: {datetime.now()}\n")


            dl_opts=opts_base()
            dl_opts.update({"format":req_fmt,
                            "outtmpl":os.path.join(SAVE_PATH,f"{out_name}.%(ext)s"),
                            "progress_hooks":[hook],"noprogress":True,"overwrites":True,
                            "continuedl":True})
            if merge_ext: dl_opts["merge_output_format"]=merge_ext
            if mode=="audio": dl_opts["postprocessors"]=[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]
            with yt_dlp.YoutubeDL(dl_opts) as ydl: ydl.download([url])
            ext="mp3" if mode=="audio" else (merge_ext or "mp4")
            fname=f"{out_name}.{ext}"
            if not os.path.exists(os.path.join(SAVE_PATH,fname)):
                newer=sorted([f for f in Path(SAVE_PATH).glob(f"{out_name}*") if f.is_file()],
                             key=lambda x:x.stat().st_mtime,reverse=True)
                fname=newer[0].name if newer else fname
            state.update({"phase":"finished","percent":100,"filename":fname,"step":"✅ اكتمل!"})
            save_history({"title":data["title"],"url":url,"file":fname,"mode":mode,
                          "quality":qlabel,"date":datetime.now().strftime("%Y-%m-%d %H:%M"),"path":SAVE_PATH})
        except Exception as e:
            msg=str(e)
            if "Cancelled" in msg: state["phase"]="idle"
            else: state.update({"phase":"error","error":msg[:300]})

# ══════════════════════════════════════════════
#  تحميل قائمة تشغيل
# ══════════════════════════════════════════════
def run_playlist_download(entries, format_id, mode, resume_failed=False):
    """
    entries: قائمة من {index, url, title, thumb, duration}
    format_id: "best" أو رقم format محدد
    resume_failed: إذا True يعيد تحميل الفاشلة فقط
    """
    with playlist_lock:
        pl_stop_flag.clear()
        total = len(entries)

        playlist_state.update({
            "phase":           "downloading",
            "total":           total,
            "current_index":   0,
            "current_title":   "",
            "current_percent": 0,
            "current_speed":   "",
            "current_eta":     "",
            "done_count":      0,
            "step":            "",
            "items":           [{"index":e["index"],"title":e["title"],"thumb":e["thumb"],
                                  "status":"pending","filename":"","error":""} for e in entries],
            "failed":          [],
        })

        for i, entry in enumerate(entries):
            if pl_stop_flag.is_set(): break
            idx = entry["index"]
            url = entry["url"]
            title = entry["title"]

            # تحديث الحالة
            playlist_state["current_index"] = i+1
            playlist_state["current_title"] = title
            playlist_state["current_percent"] = 0
            playlist_state["items"][i]["status"] = "downloading"
            playlist_state["step"] = f"⬇️ [{i+1}/{total}] {title[:40]}"
            print(f"\n[{i+1}/{total}] تحميل: {title[:60]}")

            try:
                # تحليل الفيديو
                data = analyze_url(url)
                safe_title = re.sub(r'[\\/*?:"<>|]',"_",data["title"] or title)
                all_fmts   = data["all_formats"]

                # إذا format_id رقم محدد ولم يوجد في هذا الفيديو → fallback لـ best
                actual_fid = format_id
                if format_id not in ("best","bestaudio/best"):
                    found = any(f.get("format_id")==format_id for f in all_fmts)
                    if not found:
                        actual_fid = "best"

                req_fmt, merge_ext = pick_format(actual_fid, all_fmts, mode)
                qlabel = quality_label(actual_fid, all_fmts, mode)
                out_name = f"{safe_title} [{qlabel}]"

                dl_opts = opts_base()
                dl_opts.update({
                    "format":         req_fmt,
                    "outtmpl":        os.path.join(SAVE_PATH, f"{out_name}.%(ext)s"),
                    "progress_hooks": [pl_hook],
                    "noprogress":     True,
                    "overwrites":     False,  # لا تعيد تحميل الموجود
                    "continuedl":     True,   # استكمل المنقطع
                    "ignoreerrors":   False,
                })
                if merge_ext: dl_opts["merge_output_format"] = merge_ext
                if mode=="audio": dl_opts["postprocessors"]=[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]

                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.download([url])

                ext = "mp3" if mode=="audio" else (merge_ext or "mp4")
                fname = f"{out_name}.{ext}"
                if not os.path.exists(os.path.join(SAVE_PATH,fname)):
                    newer=sorted([f for f in Path(SAVE_PATH).glob(f"{out_name}*") if f.is_file()],
                                 key=lambda x:x.stat().st_mtime,reverse=True)
                    fname=newer[0].name if newer else fname

                playlist_state["items"][i]["status"]   = "done"
                playlist_state["items"][i]["filename"] = fname
                playlist_state["done_count"] += 1
                print(f"   ✅ {fname}")
                save_history({"title":data["title"],"url":url,"file":fname,"mode":mode,
                              "quality":qlabel,"date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "path":SAVE_PATH})

            except Exception as e:
                if pl_stop_flag.is_set(): break
                err_msg = str(e)[:200]
                print(f"   ❌ فشل: {err_msg}")
                playlist_state["items"][i]["status"] = "failed"
                playlist_state["items"][i]["error"]  = err_msg
                playlist_state["failed"].append({
                    "index": idx, "title": title, "thumb": entry.get("thumb",""),
                    "url": url, "error": err_msg
                })

        playlist_state["phase"] = "done"
        done  = playlist_state["done_count"]
        fails = len(playlist_state["failed"])
        playlist_state["step"] = f"✅ اكتمل: {done} فيديو" + (f" — ❌ {fails} فاشل" if fails else "")
        print(f"\n🏁 انتهى: {done} نجح، {fails} فشل")

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
:root{
  --bg:#080b12;--surface:#0d1018;--surface2:#12151f;--surface3:#171b28;
  --border:rgba(255,255,255,.07);--border-active:rgba(99,179,237,.38);
  --accent:#63b3ed;--accent2:#68d391;--gold:#f6c90e;--red:#fc8181;--pu:#c084fc;
  --txt:#dde6f0;--muted:#48556a;--r:16px;
  --font:'Cairo',sans-serif;--mono:'JetBrains Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--txt);font-family:var(--font);min-height:100vh;
  display:flex;flex-direction:column;align-items:center;padding-bottom:80px;overflow-x:hidden}
#bgCanvas{position:fixed;inset:0;z-index:0;pointer-events:none;width:100%;height:100%}
#bgOverlay{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(ellipse 90% 55% at 15% -5%,rgba(99,179,237,.09) 0%,transparent 60%),
             radial-gradient(ellipse 70% 45% at 85% 105%,rgba(104,211,145,.06) 0%,transparent 60%),
             radial-gradient(ellipse 50% 35% at 50% 50%,rgba(246,201,14,.025) 0%,transparent 70%)}
.w{width:100%;max-width:660px;padding:20px 16px;position:relative;z-index:2}

/* HEADER */
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

/* CARD */
.card{background:rgba(13,16,24,.78);border:1px solid var(--border);border-radius:var(--r);
  overflow:hidden;margin-bottom:12px;transition:border-color .3s;backdrop-filter:blur(12px)}
.card:focus-within{border-color:var(--border-active)}

/* INPUT */
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
.url-input::placeholder{color:var(--muted);direction:rtl;text-align:right;font-size:13px}
.url-input:focus{border-color:var(--border-active);box-shadow:0 0 0 3px rgba(99,179,237,.08)}
.paste-btn{background:rgba(99,179,237,.1);border:1px solid rgba(99,179,237,.2);border-radius:12px;
  color:var(--accent);padding:12px 16px;cursor:pointer;font-size:13px;font-weight:700;
  transition:all .2s;white-space:nowrap;font-family:var(--font);flex-shrink:0}
.paste-btn:hover{background:rgba(99,179,237,.18);border-color:rgba(99,179,237,.35)}
.paste-btn:active{transform:scale(.97)}

/* SPINNER */
.spinner-wrap{display:none;padding:32px;text-align:center}
.spinner-wrap.on{display:block}
.spinner{width:36px;height:36px;margin:0 auto 12px;border:3px solid rgba(99,179,237,.1);
  border-top:3px solid var(--accent);border-radius:50%;animation:rot .8s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
.spinner-txt{font-size:13px;color:var(--muted)}

/* VIDEO CARD (فيديو واحد) */
.vc{display:none;animation:fadeUp .35s ease}.vc.on{display:block}
@keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}

/* ── الصورة كاملة الارتفاع ── */
.thumb-wrap{position:relative;width:100%;height:200px;background:var(--surface2);overflow:hidden}
.thumb-wrap img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
  background:#000;opacity:.85}
.thumb-wrap::after{content:'';position:absolute;inset:0;
  background:linear-gradient(to bottom,transparent 40%,rgba(13,16,24,.95) 100%)}
.thumb-overlay{position:absolute;bottom:0;left:0;right:0;padding:16px;z-index:1}
.vid-title{font-size:14px;font-weight:700;line-height:1.4;margin-bottom:4px}
.vid-meta{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);font-family:var(--mono)}

/* QUALITY */
.quality-wrap{padding:14px 16px 16px}
.q-label{font-size:10px;font-weight:700;color:var(--muted);letter-spacing:1.5px;
  text-transform:uppercase;margin-bottom:10px;font-family:var(--mono)}
.q-grid{display:flex;flex-wrap:wrap;gap:7px}
.qbtn{background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--txt);
  padding:9px 14px;cursor:pointer;font-size:12px;font-weight:600;transition:all .18s;
  display:flex;align-items:center;gap:7px;font-family:var(--font)}
.qbtn:hover{border-color:rgba(99,179,237,.3);color:var(--accent);background:rgba(99,179,237,.05);transform:translateY(-1px)}
.qbtn:active{transform:translateY(0)}
.qbtn.best{flex-basis:100%;background:linear-gradient(135deg,rgba(99,179,237,.08),rgba(104,211,145,.05));
  border-color:rgba(99,179,237,.25);color:var(--accent);font-size:13px;justify-content:space-between}
.qbtn.best:hover{background:linear-gradient(135deg,rgba(99,179,237,.15),rgba(104,211,145,.08));
  border-color:rgba(99,179,237,.4);transform:translateY(-1px)}
.qbtn.aud-best{flex-basis:100%;background:linear-gradient(135deg,rgba(104,211,145,.08),rgba(99,179,237,.05));
  border-color:rgba(104,211,145,.25);color:var(--accent2);font-size:13px;justify-content:space-between}
.q-size{font-size:11px;font-weight:600;font-family:var(--mono);color:var(--accent2);opacity:.85;margin-right:auto}
.q-size.approx{color:var(--gold)}
.best-arrow{font-size:14px;opacity:.5}

/* PROGRESS (فيديو واحد) */
.pg{display:none;padding:20px;animation:fadeUp .3s ease}.pg.on{display:block}
.pg-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.pg-status{font-size:13px;font-weight:600;color:var(--txt)}
.pg-speed{font-size:11px;color:var(--muted);font-family:var(--mono);display:flex;align-items:center;gap:6px}
.speed-dot{width:6px;height:6px;border-radius:50%;background:var(--accent2);animation:pulse 1.2s ease infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}
.track-bg{height:4px;background:rgba(255,255,255,.05);border-radius:99px;overflow:hidden;margin-bottom:8px}
.track-fill{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),var(--accent2));
  border-radius:99px;transition:width .4s ease;box-shadow:0 0 8px rgba(99,179,237,.4)}
.pg-row{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:14px;font-family:var(--mono)}
.pg-pct{color:var(--accent);font-weight:700}
.cancel-btn{width:100%;background:rgba(252,129,129,.06);border:1px solid rgba(252,129,129,.15);
  color:var(--red);border-radius:10px;padding:10px;cursor:pointer;font-size:13px;font-weight:700;
  transition:all .2s;font-family:var(--font)}
.cancel-btn:hover{background:rgba(252,129,129,.12);border-color:rgba(252,129,129,.28)}

/* DONE (فيديو واحد) */
.dn{display:none;padding:28px 20px;text-align:center;animation:fadeUp .4s ease}.dn.on{display:block}
.dn-ring{width:64px;height:64px;margin:0 auto 14px;
  background:linear-gradient(135deg,rgba(104,211,145,.12),rgba(99,179,237,.06));
  border:2px solid rgba(104,211,145,.25);border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:26px}
.dn-title{font-size:18px;font-weight:800;color:var(--accent2);margin-bottom:6px}
.dn-file{font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:8px;word-break:break-all;
  padding:8px 12px;background:var(--surface2);border-radius:8px;border:1px solid var(--border)}
.new-btn{margin-top:18px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;
  border-radius:50px;padding:12px 32px;color:#080b12;font-weight:800;font-size:14px;cursor:pointer;
  transition:all .2s;font-family:var(--font)}
.new-btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(99,179,237,.25)}

/* ERROR */
.err{display:none;padding:16px;margin-top:12px;background:rgba(252,129,129,.04);
  border:1px solid rgba(252,129,129,.15);border-radius:12px;color:var(--red);
  font-size:13px;line-height:1.6;animation:fadeUp .3s ease}.err.on{display:block}

/* SAVE PATH */
.save-path{display:flex;align-items:center;gap:8px;padding:10px 14px;
  background:rgba(13,16,24,.7);border:1px solid var(--border);border-radius:12px;
  font-size:11px;color:var(--muted);margin-bottom:12px;font-family:var(--mono);backdrop-filter:blur(8px)}
.save-path span{flex:1;word-break:break-all;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* HISTORY */
.history{margin-top:8px}
.hist-header{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;color:var(--muted);
  letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px;padding:0 2px;font-family:var(--mono)}
.hist-item{display:flex;align-items:center;gap:10px;padding:11px 14px;background:rgba(13,16,24,.7);
  border:1px solid var(--border);border-radius:12px;margin-bottom:7px;font-size:12px;
  transition:border-color .2s;backdrop-filter:blur(8px)}
.hist-item:hover{border-color:rgba(99,179,237,.18)}
.hist-icon{width:34px;height:34px;flex-shrink:0;background:var(--surface2);border-radius:9px;
  display:flex;align-items:center;justify-content:center;font-size:14px}
.hist-body{flex:1;min-width:0}
.hist-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.hist-meta{font-size:10px;color:var(--muted);font-family:var(--mono)}
.hist-q{font-size:10px;padding:2px 8px;border-radius:6px;background:rgba(99,179,237,.08);
  border:1px solid rgba(99,179,237,.15);color:var(--accent);font-family:var(--mono);flex-shrink:0}

/* ══════════════════════════════════════════
   PLAYLIST STYLES
══════════════════════════════════════════ */
.pl-card{display:none;animation:fadeUp .35s ease}.pl-card.on{display:block}

/* رأس القائمة */
.pl-header{padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.pl-title-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.pl-icon{font-size:22px}
.pl-info{}
.pl-name{font-size:15px;font-weight:800;line-height:1.3}
.pl-count{font-size:11px;color:var(--muted);font-family:var(--mono);margin-top:2px}

/* أزرار تحديد الكل */
.pl-sel-row{display:flex;gap:8px}
.sel-btn{padding:7px 14px;border-radius:9px;font-size:12px;font-weight:700;
  cursor:pointer;border:1px solid;transition:all .18s;font-family:var(--font)}
.sel-btn.all{background:rgba(99,179,237,.1);border-color:rgba(99,179,237,.25);color:var(--accent)}
.sel-btn.all:hover{background:rgba(99,179,237,.2)}
.sel-btn.none{background:rgba(252,129,129,.07);border-color:rgba(252,129,129,.2);color:var(--red)}
.sel-btn.none:hover{background:rgba(252,129,129,.14)}

/* قائمة الفيديوهات */
.pl-list{max-height:420px;overflow-y:auto;padding:8px 0}
.pl-list::-webkit-scrollbar{width:4px}
.pl-list::-webkit-scrollbar-thumb{background:var(--muted);border-radius:2px}

/* مستطيل فيديو */
.pl-item{display:flex;align-items:center;gap:10px;padding:9px 14px;
  border-bottom:1px solid rgba(255,255,255,.03);cursor:pointer;
  transition:background .18s;position:relative}
.pl-item:last-child{border-bottom:none}
.pl-item:hover{background:rgba(99,179,237,.04)}
.pl-item.selected{background:rgba(99,179,237,.05)}
.pl-item.selected .pl-cb{border-color:var(--accent);background:var(--accent)}
.pl-item.selected .pl-cb::after{opacity:1}

/* صورة الفيديو كاملة */
.pl-thumb{width:80px;height:52px;border-radius:8px;overflow:hidden;flex-shrink:0;
  background:var(--surface2);position:relative}
.pl-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.pl-thumb .pl-dur{position:absolute;bottom:3px;left:3px;background:rgba(0,0,0,.75);
  color:#fff;font-size:9px;padding:1px 4px;border-radius:4px;font-family:var(--mono)}

/* معلومات الفيديو */
.pl-meta{flex:1;min-width:0}
.pl-vtitle{font-size:13px;font-weight:600;line-height:1.35;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.pl-vdur{font-size:11px;color:var(--muted);font-family:var(--mono)}

/* مربع التحديد */
.pl-cb{width:20px;height:20px;border-radius:6px;border:2px solid var(--muted);
  flex-shrink:0;transition:all .18s;position:relative;background:transparent}
.pl-cb::after{content:'✓';position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;font-size:11px;font-weight:900;color:#080b12;opacity:0;transition:opacity .15s}

/* حالة كل فيديو أثناء التحميل */
.pl-item .pl-status{position:absolute;top:6px;left:8px;font-size:10px;
  padding:2px 7px;border-radius:5px;font-family:var(--mono)}
.pl-item .pl-status.done{background:rgba(104,211,145,.15);color:var(--accent2)}
.pl-item .pl-status.failed{background:rgba(252,129,129,.15);color:var(--red)}
.pl-item .pl-status.downloading{background:rgba(99,179,237,.15);color:var(--accent)}
.pl-item .pl-status.pending{background:rgba(72,85,106,.2);color:var(--muted)}

/* قسم الجودة للقائمة */
.pl-quality-wrap{padding:14px 16px 16px;border-top:1px solid var(--border)}
.pl-q-label{font-size:10px;font-weight:700;color:var(--muted);letter-spacing:1.5px;
  text-transform:uppercase;margin-bottom:10px;font-family:var(--mono)}
.pl-total-size{font-size:11px;color:var(--muted);font-family:var(--mono);
  margin-bottom:10px;padding:8px 12px;background:var(--surface2);border-radius:8px}
.pl-total-size b{color:var(--gold)}

/* زر بدء قائمة التشغيل */
.pl-start-btn{width:100%;background:linear-gradient(135deg,var(--accent),var(--accent2));
  border:none;border-radius:12px;padding:13px;color:#080b12;font-weight:800;
  font-size:14px;cursor:pointer;transition:all .2s;font-family:var(--font);margin-top:4px}
.pl-start-btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(99,179,237,.3)}
.pl-start-btn:disabled{opacity:.4;cursor:not-allowed;transform:none}

/* ══ تقدم قائمة التشغيل ══ */
.pl-pg{display:none;padding:18px;animation:fadeUp .3s ease}.pl-pg.on{display:block}
.pl-pg-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px}
.pl-pg-title{font-size:13px;font-weight:700;flex:1;min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:8px}
.pl-pg-count{font-size:11px;font-family:var(--mono);color:var(--accent);flex-shrink:0;font-weight:700}
.pl-pg-step{font-size:11px;color:var(--muted);margin-bottom:10px;min-height:16px}
.pl-overall-bg{height:5px;background:rgba(255,255,255,.05);border-radius:99px;overflow:hidden;margin-bottom:6px}
.pl-overall-fill{height:100%;background:linear-gradient(90deg,var(--pu),var(--accent));
  border-radius:99px;transition:width .5s ease;box-shadow:0 0 8px rgba(192,132,252,.35)}
.pl-cur-bg{height:3px;background:rgba(255,255,255,.04);border-radius:99px;overflow:hidden;margin-bottom:6px}
.pl-cur-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));
  border-radius:99px;transition:width .35s ease}
.pl-pg-row{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);
  margin-bottom:12px;font-family:var(--mono)}
.pl-cancel-btn{width:100%;background:rgba(252,129,129,.06);border:1px solid rgba(252,129,129,.15);
  color:var(--red);border-radius:10px;padding:9px;cursor:pointer;font-size:13px;
  font-weight:700;transition:all .2s;font-family:var(--font)}

/* ══ نتائج القائمة ══ */
.pl-done{display:none;padding:20px;animation:fadeUp .4s ease}.pl-done.on{display:block}
.pl-done-header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.pl-done-icon{font-size:32px}
.pl-done-info{}
.pl-done-title{font-size:16px;font-weight:800;color:var(--accent2)}
.pl-done-sub{font-size:12px;color:var(--muted);font-family:var(--mono);margin-top:3px}

/* الفيديوهات الفاشلة */
.failed-section{margin-top:14px}
.failed-title{font-size:11px;font-weight:700;color:var(--red);letter-spacing:1px;
  text-transform:uppercase;margin-bottom:8px;font-family:var(--mono)}
.failed-item{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;
  background:rgba(252,129,129,.04);border:1px solid rgba(252,129,129,.15);
  border-radius:10px;margin-bottom:7px}
.failed-thumb{width:60px;height:40px;border-radius:6px;overflow:hidden;flex-shrink:0}
.failed-thumb img{width:100%;height:100%;object-fit:cover}
.failed-body{flex:1;min-width:0}
.failed-name{font-size:12px;font-weight:600;margin-bottom:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.failed-err{font-size:10px;color:var(--red);opacity:.75;line-height:1.4}
.retry-btn{padding:6px 12px;background:rgba(99,179,237,.1);border:1px solid rgba(99,179,237,.25);
  border-radius:8px;color:var(--accent);font-size:11px;font-weight:700;cursor:pointer;
  transition:all .2s;font-family:var(--font);white-space:nowrap;flex-shrink:0}
.retry-btn:hover{background:rgba(99,179,237,.2)}

/* زر تحميل جديد */
.pl-new-btn{margin-top:16px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  border:none;border-radius:50px;padding:11px 28px;color:#080b12;font-weight:800;
  font-size:14px;cursor:pointer;transition:all .2s;font-family:var(--font)}
.pl-new-btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(99,179,237,.25)}
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas>
<div id="bgOverlay"></div>
<div class="w">

  <!-- HEADER -->
  <div class="header">
    <div class="logo-wrap">
      <div class="logo-icon">B</div>
      <div><div class="logo-text">B-Ultra</div></div>
      <div class="logo-ver">v12</div>
    </div>
    <div class="tagline">محمّل فيديو سريع وخفيف</div>
  </div>

  <!-- INPUT -->
  <div class="card input-card">
    <div class="mode-tabs">
      <button class="tab vid on" id="mV" onclick="setMode('video')">🎬 فيديو</button>
      <button class="tab aud" id="mA" onclick="setMode('audio')">🎵 MP3</button>
    </div>
    <div class="url-row">
      <input id="uI" class="url-input" type="text" placeholder="رابط فيديو أو قائمة تشغيل...">
      <button class="paste-btn" onclick="doPaste()">📋 لصق</button>
    </div>
  </div>

  <!-- SPINNER -->
  <div class="spinner-wrap" id="sp">
    <div class="spinner"></div>
    <div class="spinner-txt" id="sp-txt">جارٍ التحليل...</div>
  </div>

  <!-- VIDEO CARD (فيديو واحد) -->
  <div class="card vc" id="vc">
    <div class="thumb-wrap">
      <img id="vth" src="" alt="">
      <div class="thumb-overlay">
        <div class="vid-title" id="vt"></div>
        <div class="vid-meta"><span id="vm"></span></div>
      </div>
    </div>
    <div class="quality-wrap">
      <div class="q-label">اختر الجودة</div>
      <div class="q-grid" id="qw"></div>
    </div>
  </div>

  <!-- PROGRESS (فيديو واحد) -->
  <div class="card pg" id="pg">
    <div class="pg-header">
      <div class="pg-status" id="pst">جارٍ التحميل...</div>
      <div class="pg-speed"><div class="speed-dot"></div><span id="psp">—</span></div>
    </div>
    <div class="track-bg"><div class="track-fill" id="fi"></div></div>
    <div class="pg-row"><span class="pg-pct" id="pp">0%</span><span id="pe">—</span></div>
    <button class="cancel-btn" onclick="doCancel()">✕ إلغاء التحميل</button>
  </div>

  <!-- DONE (فيديو واحد) -->
  <div class="card dn" id="dn">
    <div class="dn-ring">✅</div>
    <div class="dn-title">اكتمل التحميل!</div>
    <div class="dn-file" id="df"></div>
    <button class="new-btn" onclick="resetAll()">تحميل جديد</button>
  </div>

  <!-- ERROR -->
  <div class="err" id="er">
    <div style="font-size:18px;margin-bottom:6px">⚠️</div>
    <span id="er-msg"></span>
  </div>

  <!-- ══════════════════════════════════════════
       PLAYLIST CARD
  ══════════════════════════════════════════ -->
  <div class="card pl-card" id="plCard">

    <!-- رأس القائمة + أزرار التحديد -->
    <div class="pl-header">
      <div class="pl-title-row">
        <div class="pl-icon">📋</div>
        <div class="pl-info">
          <div class="pl-name" id="plName">قائمة تشغيل</div>
          <div class="pl-count" id="plCount">0 فيديو</div>
        </div>
      </div>
      <div class="pl-sel-row">
        <button class="sel-btn all" onclick="selectAll(true)">✅ تحديد الكل</button>
        <button class="sel-btn none" onclick="selectAll(false)">✕ إلغاء الكل</button>
      </div>
    </div>

    <!-- قائمة الفيديوهات -->
    <div class="pl-list" id="plList"></div>

    <!-- اختيار الجودة -->
    <div class="pl-quality-wrap">
      <div class="pl-q-label">جودة التحميل</div>
      <div class="pl-total-size" id="plTotalSize">اختر جودة لعرض الحجم الإجمالي</div>
      <div class="q-grid" id="plQw"></div>
      <button class="pl-start-btn" id="plStartBtn" onclick="startPlaylist()" disabled>
        ⬇️ بدء التحميل
      </button>
    </div>
  </div>

  <!-- ══ تقدم القائمة ══ -->
  <div class="card pl-pg" id="plPg">
    <div class="pl-pg-header">
      <div class="pl-pg-title" id="plPgTitle">—</div>
      <div class="pl-pg-count" id="plPgCount">0/0</div>
    </div>
    <div class="pl-pg-step" id="plPgStep"></div>
    <div class="pl-overall-bg"><div class="pl-overall-fill" id="plOverall" style="width:0%"></div></div>
    <div class="pl-cur-bg"><div class="pl-cur-fill" id="plCur" style="width:0%"></div></div>
    <div class="pl-pg-row">
      <span id="plPgPct">0%</span>
      <span id="plPgSpd">—</span>
      <span id="plPgEta">—</span>
    </div>
    <button class="pl-cancel-btn" onclick="cancelPlaylist()">✕ إيقاف القائمة</button>
  </div>

  <!-- ══ نتائج القائمة ══ -->
  <div class="card pl-done" id="plDone">
    <div class="pl-done-header">
      <div class="pl-done-icon">🏁</div>
      <div class="pl-done-info">
        <div class="pl-done-title" id="plDoneTitle">اكتمل التحميل!</div>
        <div class="pl-done-sub" id="plDoneSub"></div>
      </div>
    </div>
    <div class="failed-section" id="plFailedSection" style="display:none">
      <div class="failed-title">❌ الفيديوهات الفاشلة</div>
      <div id="plFailedList"></div>
    </div>
    <button class="pl-new-btn" onclick="resetAll()">تحميل جديد</button>
  </div>

  <!-- SAVE PATH -->
  <div class="save-path">
    <span style="font-size:14px;flex-shrink:0">📁</span>
    <span id="sp2">...</span>
  </div>

  <!-- HISTORY -->
  <div class="history" id="hS" style="display:none">
    <div class="hist-header">⏱ آخر التحميلات</div>
    <div id="hL"></div>
  </div>

</div>

<script>
/* ══ Canvas خلفية ══ */
(function(){
  const canvas=document.getElementById('bgCanvas'),ctx=canvas.getContext('2d');
  let W,H,particles=[],mouse={x:-999,y:-999};
  const REPEL_RADIUS=110,REPEL_FORCE=0.38;
  function resize(){W=canvas.width=window.innerWidth;H=canvas.height=window.innerHeight}
  function mkP(){
    const p={x:Math.random()*W,y:Math.random()*H,vx:0,vy:0,
      r:1.1+Math.random()*1.6,wAmp:8+Math.random()*14,
      wFreq:0.0006+Math.random()*0.0008,
      wPhasX:Math.random()*Math.PI*2,wPhasY:Math.random()*Math.PI*2,
      hue:185+Math.random()*30,alpha:0.18+Math.random()*0.32};
    p.ox=p.x;p.oy=p.y;return p;
  }
  function init(){particles=[];const N=Math.floor(W*H/6800);for(let i=0;i<N;i++)particles.push(mkP())}
  let lastT=0;
  function draw(t){
    requestAnimationFrame(draw);ctx.clearRect(0,0,W,H);
    for(const p of particles){
      const wx=p.ox+Math.sin(t*p.wFreq+p.wPhasX)*p.wAmp;
      const wy=p.oy+Math.cos(t*p.wFreq*.7+p.wPhasY)*p.wAmp*.6;
      const dx=wx-mouse.x,dy=wy-mouse.y,dist=Math.sqrt(dx*dx+dy*dy);
      let rx=0,ry=0;
      if(dist<REPEL_RADIUS&&dist>0){
        const f=(REPEL_RADIUS-dist)*(1-dist/REPEL_RADIUS)*REPEL_FORCE*2.2;
        rx=dx/dist*f;ry=dy/dist*f;
      }
      p.vx+=(rx-p.vx)*.12;p.vy+=(ry-p.vy)*.12;
      p.x=wx+p.vx;p.y=wy+p.vy;
      ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle=`hsla(${p.hue},85%,72%,${p.alpha})`;ctx.fill();
      for(const q of particles){
        if(q===p)continue;
        const d2=Math.sqrt((p.x-q.x)**2+(p.y-q.y)**2);
        if(d2<80){ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);
          ctx.strokeStyle=`hsla(${p.hue},80%,70%,${.04*(1-d2/80)})`;ctx.lineWidth=.5;ctx.stroke()}
      }
    }
  }
  window.addEventListener('mousemove',e=>{mouse.x=e.clientX;mouse.y=e.clientY});
  window.addEventListener('mouseleave',()=>{mouse.x=-999;mouse.y=-999});
  window.addEventListener('touchmove',e=>{const t=e.touches[0];mouse.x=t.clientX;mouse.y=t.clientY},{passive:true});
  window.addEventListener('resize',()=>{resize();init()});
  resize();init();requestAnimationFrame(draw);
})();

/* ══ App State ══ */
let mode='video',pT=null,plT=null,curU='',analyzing=false;
let _F=[],_plEntries=[],_plSel=new Set(),_plFmts=[],_plFid='best',_plMode='video';

/* ══ Mode ══ */
function setMode(m){
  mode=m;
  document.getElementById('mV').className='tab vid'+(m==='video'?' on':'');
  document.getElementById('mA').className='tab aud'+(m==='audio'?' on':'');
  if(_F.length)buildQ();
  if(_plFmts.length)buildPlQ();
}

/* ══ Paste ══ */
async function doPaste(){
  try{const t=await navigator.clipboard.readText();document.getElementById('uI').value=t;maybeAn(t);}
  catch{document.getElementById('uI').focus();}
}
const uI=document.getElementById('uI');
uI.addEventListener('input',function(){const v=this.value.trim();if(v.startsWith('http')&&!analyzing)maybeAn(v);});
uI.addEventListener('paste',function(){setTimeout(()=>{const v=this.value.trim();if(v.startsWith('http')&&!analyzing)maybeAn(v);},80);});

function maybeAn(url){if(url===curU)return;curU=url;doAn(url);}

/* ══ Analyze ══ */
function doAn(url){
  analyzing=true;hideAll();on('sp');
  document.getElementById('sp-txt').textContent='جارٍ التحليل...';
  fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})})
  .then(r=>r.json()).then(d=>{
    off('sp');analyzing=false;
    if(d.error){showEr(d.error);curU='';return;}
    if(d.is_playlist) showPlaylist(d);
    else showCard(d);
  }).catch(e=>{off('sp');analyzing=false;showEr(e.message);curU='';});
}

/* ══ Video Card (واحد) ══ */
function showCard(data){
  _F=data.formats||[];
  document.getElementById('vth').src=data.thumb||'';
  document.getElementById('vt').textContent=data.title||'فيديو';
  document.getElementById('vm').textContent=data.duration?fmtDur(data.duration):'';
  buildQ();on('vc');
}
function buildQ(){
  const w=document.getElementById('qw');w.innerHTML='';
  if(mode==='audio'){w.appendChild(mkQBtn('🎵 أفضل جودة صوت — MP3','best','audio',0,true));return;}
  w.appendChild(mkQBtn('🔥 أفضل جودة متاحة','best','video',0,true));
  _F.slice().reverse().forEach(f=>w.appendChild(mkQBtn(`${f.res}p${f.fps?' · '+f.fps+'fps':''}`,f.id,'video',f.size,false)));
}
function mkQBtn(lbl,fid,m,size,isBest){
  const b=document.createElement('button');
  b.className='qbtn'+(isBest?(m==='audio'?' aud-best':' best'):'');
  const sp=document.createElement('span');sp.textContent=lbl;b.appendChild(sp);
  if(isBest){const a=document.createElement('span');a.className='best-arrow';a.textContent='→';b.appendChild(a);}
  else if(size>0){const s=document.createElement('span');s.className='q-size';s.textContent=fmtSz(size);b.appendChild(s);}
  else{const s=document.createElement('span');s.className='q-size approx';s.textContent='~?';b.appendChild(s);}
  b.onclick=()=>startDL(curU,fid,m);return b;
}
function startDL(url,fid,m){
  off('vc');on('pg');
  document.getElementById('pst').textContent='جارٍ التحميل...';
  document.getElementById('fi').style.width='0%';
  fetch('/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,format_id:fid,mode:m})});
  if(pT)clearInterval(pT);pT=setInterval(doPoll,700);
}
function doPoll(){
  fetch('/progress').then(r=>r.json()).then(d=>{
    if(d.step)document.getElementById('pst').textContent=d.step;
    if(d.phase==='downloading'){
      document.getElementById('fi').style.width=d.percent+'%';
      document.getElementById('pp').textContent=d.percent.toFixed(1)+'%';
      document.getElementById('psp').textContent=d.speed||'';
      document.getElementById('pe').textContent=d.eta?'ETA '+d.eta:'';
    }else if(d.phase==='merging'){
      document.getElementById('fi').style.width='100%';
      document.getElementById('fi').style.background='var(--gold)';
      document.getElementById('pp').textContent='100%';
    }else if(d.phase==='finished'){clearInterval(pT);off('pg');document.getElementById('df').textContent='📄 '+d.filename;on('dn');loadH();}
    else if(d.phase==='error'){clearInterval(pT);off('pg');showEr(d.error||'خطأ');}
  });
}
function doCancel(){fetch('/cancel');clearInterval(pT);resetAll();}

/* ══════════════════════════════════════════
   PLAYLIST
══════════════════════════════════════════ */
function showPlaylist(data){
  _plEntries = data.entries || [];
  _plFmts    = data.formats || [];
  _plSel     = new Set(_plEntries.map(e=>e.index));

  document.getElementById('plName').textContent  = data.pl_title || 'قائمة تشغيل';
  document.getElementById('plCount').textContent = _plEntries.length + ' فيديو';

  buildPlList();
  buildPlQ();
  on('plCard');
}

function buildPlList(){
  const list=document.getElementById('plList');
  list.innerHTML='';
  _plEntries.forEach(e=>{
    const sel=_plSel.has(e.index);
    const div=document.createElement('div');
    div.className='pl-item'+(sel?' selected':'');
    div.dataset.idx=e.index;
    div.innerHTML=`
      <div class="pl-thumb">
        <img src="${esc(e.thumb)}" loading="lazy" onerror="this.src=''">
        ${e.duration?`<div class="pl-dur">${fmtDur(e.duration)}</div>`:''}
      </div>
      <div class="pl-meta">
        <div class="pl-vtitle">${esc(e.title)}</div>
        <div class="pl-vdur">${e.duration?fmtDur(e.duration):''}</div>
      </div>
      <div class="pl-cb"></div>`;
    div.onclick=()=>toggleSel(e.index,div);
    list.appendChild(div);
  });
  updateSelCount();
}

function toggleSel(idx,el){
  if(_plSel.has(idx)){_plSel.delete(idx);el.classList.remove('selected');}
  else{_plSel.add(idx);el.classList.add('selected');}
  updateSelCount();
  updateTotalSize();
}
function selectAll(v){
  if(v) _plEntries.forEach(e=>_plSel.add(e.index));
  else _plSel.clear();
  document.querySelectorAll('.pl-item').forEach(el=>{
    const idx=parseInt(el.dataset.idx);
    if(_plSel.has(idx))el.classList.add('selected');
    else el.classList.remove('selected');
  });
  updateSelCount();updateTotalSize();
}
function updateSelCount(){
  document.getElementById('plCount').textContent=
    `${_plEntries.length} فيديو · ${_plSel.size} محدد`;
  document.getElementById('plStartBtn').disabled=(_plSel.size===0);
}

/* بناء أزرار الجودة للقائمة */
function buildPlQ(){
  const w=document.getElementById('plQw');w.innerHTML='';
  if(mode==='audio'){
    const b=mkPlQBtn('🎵 MP3 — أفضل جودة صوت','best','audio',true);
    w.appendChild(b);_plFid='best';_plMode='audio';
    updateTotalSize();return;
  }
  const bb=mkPlQBtn('🔥 أفضل جودة','best','video',true);w.appendChild(bb);
  _plFmts.slice().reverse().forEach(f=>w.appendChild(mkPlQBtn(`${f.res}p${f.fps?' · '+f.fps+'fps':''}`,f.id,'video',false,f.size)));
  // تحديد أفضل جودة افتراضياً
  _plFid='best';_plMode='video';
  bb.classList.add('sel-active');
  updateTotalSize();
}

function mkPlQBtn(lbl,fid,m,isBest,size){
  const b=document.createElement('button');
  b.className='qbtn'+(isBest?' best':'');
  if(isBest){
    const s=document.createElement('span');s.textContent=lbl;b.appendChild(s);
    const a=document.createElement('span');a.className='best-arrow';a.textContent='→';b.appendChild(a);
  }else{
    const s=document.createElement('span');s.textContent=lbl;b.appendChild(s);
    if(size>0){const sz=document.createElement('span');sz.className='q-size';sz.textContent=fmtSz(size);b.appendChild(sz);}
  }
  b.onclick=()=>{
    document.querySelectorAll('#plQw .qbtn').forEach(x=>x.classList.remove('sel-active'));
    b.classList.add('sel-active');
    _plFid=fid;_plMode=m;
    updateTotalSize();
  };
  return b;
}

/* حساب الحجم الإجمالي */
function updateTotalSize(){
  if(_plFid==='best'||_plMode==='audio'){
    document.getElementById('plTotalSize').innerHTML='الحجم الكلي: <b>غير محدد (يعتمد على كل فيديو)</b>';
    return;
  }
  const fmt=_plFmts.find(f=>f.id===_plFid);
  if(!fmt||!fmt.size){document.getElementById('plTotalSize').innerHTML='الحجم: <b>~غير معروف</b>';return;}
  const totalBytes=fmt.size*_plSel.size;
  document.getElementById('plTotalSize').innerHTML=
    `الحجم الكلي لـ <b>${_plSel.size} فيديو</b> بجودة ${fmt.res}p: <b>${fmtSz(totalBytes)}</b> تقريباً`;
}

/* بدء تحميل القائمة */
function startPlaylist(){
  const selEntries=_plEntries.filter(e=>_plSel.has(e.index));
  if(!selEntries.length)return;
  off('plCard');on('plPg');
  document.getElementById('plPgTitle').textContent='جارٍ التحميل...';
  document.getElementById('plPgCount').textContent=`0/${selEntries.length}`;
  fetch('/pl_download',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({entries:selEntries,format_id:_plFid,mode:_plMode})});
  if(plT)clearInterval(plT);plT=setInterval(pollPlaylist,800);
}

function pollPlaylist(){
  fetch('/pl_progress').then(r=>r.json()).then(d=>{
    if(d.phase==='idle')return;
    document.getElementById('plPgTitle').textContent=d.current_title||'—';
    document.getElementById('plPgCount').textContent=`${d.current_index}/${d.total}`;
    document.getElementById('plPgStep').textContent=d.step||'';
    document.getElementById('plPgPct').textContent=d.current_percent.toFixed(1)+'%';
    document.getElementById('plPgSpd').textContent=d.current_speed||'—';
    document.getElementById('plPgEta').textContent=d.current_eta?'ETA '+d.current_eta:'—';
    // شريط الكلي
    const overallPct=d.total>0?((d.done_count)/d.total*100):0;
    document.getElementById('plOverall').style.width=overallPct+'%';
    document.getElementById('plCur').style.width=d.current_percent+'%';
    // تحديث حالة العناصر في القائمة (مخفية الآن لكن مفيدة)
    if(d.phase==='done'){
      clearInterval(plT);
      showPlDone(d);
    }
  });
}

function showPlDone(d){
  off('plPg');
  const total=d.total, done=d.done_count, fails=d.failed?.length||0;
  document.getElementById('plDoneTitle').textContent= fails===0?'✅ اكتمل بنجاح!':'⚠️ اكتمل مع أخطاء';
  document.getElementById('plDoneSub').textContent=`${done} فيديو ✅` + (fails?` · ${fails} فاشل ❌`:'');
  // الفاشلة
  const failSec=document.getElementById('plFailedSection');
  if(fails>0){
    failSec.style.display='block';
    const list=document.getElementById('plFailedList');
    list.innerHTML='';
    (d.failed||[]).forEach(f=>{
      list.innerHTML+=`
        <div class="failed-item">
          <div class="failed-thumb"><img src="${esc(f.thumb)}" onerror="this.src=''"></div>
          <div class="failed-body">
            <div class="failed-name">${esc(f.title)}</div>
            <div class="failed-err">${esc(f.error)}</div>
          </div>
          <button class="retry-btn" onclick="retryFailed(${esc(String(f.index))})">🔄 استئناف</button>
        </div>`;
    });
  } else failSec.style.display='none';
  on('plDone');
  loadH();
}

function retryFailed(idx){
  const entry=_plEntries.find(e=>e.index===idx);
  if(!entry)return;
  // إعادة تشغيل للفيديو الواحد الفاشل
  fetch('/pl_download',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({entries:[entry],format_id:_plFid,mode:_plMode})});
  off('plDone');on('plPg');
  if(plT)clearInterval(plT);plT=setInterval(pollPlaylist,800);
}

function cancelPlaylist(){
  fetch('/pl_cancel');clearInterval(plT);resetAll();
}

/* ══ History ══ */
function loadH(){
  fetch('/history').then(r=>r.json()).then(h=>{
    if(!h||!h.length)return;
    const list=document.getElementById('hL');list.innerHTML='';
    h.slice(0,6).forEach(it=>{
      const qbadge=it.quality?`<div class="hist-q">${esc(it.quality)}</div>`:'';
      list.innerHTML+=`<div class="hist-item">
        <div class="hist-icon">${it.mode==='audio'?'🎵':'🎬'}</div>
        <div class="hist-body">
          <div class="hist-title">${esc(it.title)}</div>
          <div class="hist-meta">${it.date} · ${esc(it.file)}</div>
        </div>${qbadge}</div>`;
    });
    document.getElementById('hS').style.display='block';
  });
}

fetch('/info').then(r=>r.json()).then(d=>{document.getElementById('sp2').textContent=d.save_path;});
window.onload=()=>{hideAll();loadH();};

function resetAll(){
  clearInterval(pT);clearInterval(plT);
  _F=[];_plEntries=[];_plSel=new Set();_plFmts=[];
  hideAll();uI.value='';uI.focus();curU='';analyzing=false;
}

/* ══ Helpers ══ */
function fmtDur(s){
  s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
  if(h>0)return`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return`${m}:${String(sec).padStart(2,'0')}`;
}
function fmtSz(n){
  if(!n||n<=0)return'';
  if(n>=1073741824)return(n/1073741824).toFixed(1)+'GB';
  if(n>=1048576)return Math.round(n/1048576)+'MB';
  return Math.round(n/1024)+'KB';
}
function on(id){const e=document.getElementById(id);if(e){e.classList.add('on');e.style.display='';}}
function off(id){const e=document.getElementById(id);if(e){e.classList.remove('on');e.style.display='none';}}
function hideAll(){['vc','pg','dn','er','sp','plCard','plPg','plDone'].forEach(off);}
function showEr(m){document.getElementById('er-msg').textContent=m;on('er');}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
</script>
</body>
</html>"""

# ── Flask ──
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
            return jsonify({
                'is_playlist': True,
                'pl_title':   d['pl_title'],
                'entries':    d['entries'],
                'formats':    d['formats'],
            })
        else:
            d = analyze_url(url)
            return jsonify({
                'is_playlist': False,
                'title':   d['title'],
                'duration': d['duration'],
                'thumb':   d['thumb'],
                'formats': d['formats'],
            })
    except Exception as e:
        return jsonify({'error': str(e)[:300]})

@app.route('/download', methods=['POST'])
def download_r():
    b = request.json or {}
    if download_lock.locked(): return jsonify({'status':'busy'})
    threading.Thread(target=run_download,
        args=(b.get('url',''), b.get('format_id','best'), b.get('mode','video')),
        daemon=True).start()
    return jsonify({'status':'started'})

@app.route('/pl_download', methods=['POST'])
def pl_download_r():
    b = request.json or {}
    if playlist_lock.locked():
        # إيقاف القائمة الحالية أولاً
        pl_stop_flag.set()
        time.sleep(0.5)
    threading.Thread(target=run_playlist_download,
        args=(b.get('entries',[]), b.get('format_id','best'), b.get('mode','video')),
        daemon=True).start()
    return jsonify({'status':'started'})

@app.route('/pl_cancel')
def pl_cancel_r():
    pl_stop_flag.set()
    return jsonify({'status':'cancelled'})

@app.route('/pl_progress')
def pl_progress_r():
    return jsonify(playlist_state)

@app.route('/cancel')
def cancel_r(): stop_flag.set(); return jsonify({'status':'cancelled'})
@app.route('/progress')
def prog_r(): return jsonify(state)
@app.route('/history')
def hist_r(): return jsonify(load_history())

def open_browser():
    time.sleep(2)
    for cmd in [["termux-open-url","http://localhost:8000"],
                ["am","start","-a","android.intent.action.VIEW","-d","http://localhost:8000"],
                ["xdg-open","http://localhost:8000"]]:
        try: subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); return
        except: continue

def cli_listen():
    while True:
        try:
            if input().strip().lower() in ['q','exit','0','']: os._exit(0)
        except: pass

if __name__=='__main__':
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    print(f"""
{'═'*58}
  🦅  B-Ultra v12  —  Playlist Edition
  ✅  تحميل فيديوهات فردية
  ✅  تحميل قوائم تشغيل كاملة
  ✅  اختيار فيديوهات محددة من القائمة
  ✅  استئناف التحميل المنقطع
  ✅  معالجة الأخطاء مع إمكانية الاستئناف
  📡  http://localhost:8000
  💾  {SAVE_PATH}
  ℹ️   اكتب q أو Enter للإغلاق
{'═'*58}
""")
    threading.Thread(target=open_browser,daemon=True).start()
    threading.Thread(target=cli_listen,daemon=True).start()
    app.run(host='0.0.0.0',port=8000,debug=False,use_reloader=False)
