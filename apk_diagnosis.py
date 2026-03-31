import os, sys, subprocess, socket, json, time, threading
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════
#  مسار الإخراج
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
    return os.path.dirname(os.path.abspath(__file__))

SAVE_PATH  = get_save_path()
ERROR_FILE = os.path.join(SAVE_PATH, "error.txt")

lines = []

def W(msg="", icon=""):
    line = f"{icon}  {msg}" if icon else msg
    lines.append(line)
    print(line)

def SEP(title=""):
    w = 58
    if title:
        pad = (w - len(title) - 2) // 2
        W("═"*pad + f" {title} " + "═"*pad)
    else:
        W("═" * w)

def OK(msg):  W(f"✅  {msg}")
def ERR(msg): W(f"❌  {msg}")
def WARN(msg):W(f"⚠️   {msg}")
def INFO(msg):W(f"ℹ️   {msg}")

# ══════════════════════════════════════════════
#  1. معلومات البيئة
# ══════════════════════════════════════════════
def check_environment():
    SEP("البيئة والنظام")
    INFO(f"التاريخ والوقت : {datetime.now()}")
    INFO(f"Python          : {sys.version}")
    INFO(f"Platform        : {sys.platform}")
    INFO(f"Executable      : {sys.executable}")
    INFO(f"CWD             : {os.getcwd()}")
    INFO(f"__file__        : {os.path.abspath(__file__)}")
    INFO(f"Save Path       : {SAVE_PATH}")
    INFO(f"Error File      : {ERROR_FILE}")

    # هل نعمل داخل APK؟
    is_apk = "com.flet" in os.getcwd() or "com.flet" in sys.executable or \
             "/data/user/0/" in os.getcwd()
    if is_apk:
        WARN("يبدو أننا داخل APK (Flet container)")
    else:
        OK("نعمل في بيئة عادية (Termux / Desktop)")

    # متغيرات البيئة المهمة
    for var in ["HOME","TMPDIR","PATH","ANDROID_DATA","EXTERNAL_STORAGE"]:
        val = os.environ.get(var,"غير موجود")
        INFO(f"  {var} = {val[:80]}")

# ══════════════════════════════════════════════
#  2. صلاحيات الملفات
# ══════════════════════════════════════════════
def check_storage():
    SEP("صلاحيات التخزين")
    paths_to_test = [
        "/storage/emulated/0/Download",
        "/storage/emulated/0/Download/B-Ultra",
        os.path.expanduser("~"),
        SAVE_PATH,
        "/tmp",
        os.path.dirname(os.path.abspath(__file__)),
    ]
    for p in paths_to_test:
        try:
            os.makedirs(p, exist_ok=True)
            test_file = os.path.join(p, f"._btest_{int(time.time())}")
            open(test_file, "w").close()
            os.remove(test_file)
            OK(f"قراءة/كتابة ✓  {p}")
        except PermissionError:
            ERR(f"ممنوع الوصول ✗  {p}")
        except Exception as e:
            WARN(f"خطأ: {e}  ({p})")

# ══════════════════════════════════════════════
#  3. اتصال الشبكة
# ══════════════════════════════════════════════
def check_network():
    SEP("اتصال الشبكة")

    # DNS lookup
    hosts = ["youtube.com", "8.8.8.8", "google.com", "i.ytimg.com"]
    for host in hosts:
        try:
            ip = socket.gethostbyname(host)
            OK(f"DNS  {host} → {ip}")
        except socket.gaierror as e:
            ERR(f"DNS فشل: {host} → {e}")
        except Exception as e:
            ERR(f"DNS خطأ: {host} → {e}")

    # TCP connection
    for host, port in [("youtube.com", 443), ("8.8.8.8", 53)]:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            OK(f"TCP  {host}:{port} ✓")
        except Exception as e:
            ERR(f"TCP  {host}:{port} → {e}")

    # HTTP test (بدون مكتبات خارجية)
    try:
        import urllib.request
        req = urllib.request.urlopen("https://www.youtube.com", timeout=8)
        OK(f"HTTP youtube.com → {req.status}")
        req.close()
    except Exception as e:
        ERR(f"HTTP youtube.com → {e}")

# ══════════════════════════════════════════════
#  4. المكتبات المطلوبة
# ══════════════════════════════════════════════
def check_libraries():
    SEP("المكتبات")
    libs = [
        ("flask",     "Flask"),
        ("yt_dlp",    "yt-dlp"),
        ("flet",      "Flet"),
        ("requests",  "requests"),
        ("urllib3",   "urllib3"),
    ]
    for mod, name in libs:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", None) or \
                  getattr(getattr(m, "version", None), "__version__", "?")
            OK(f"{name} v{ver}")
        except ImportError:
            ERR(f"{name} غير مثبّت")
        except Exception as e:
            WARN(f"{name} → {e}")

# ══════════════════════════════════════════════
#  5. subprocess / ffmpeg
# ══════════════════════════════════════════════
def check_subprocess():
    SEP("subprocess / FFmpeg")

    # هل يعمل subprocess؟
    try:
        r = subprocess.run(["echo", "test"], capture_output=True, timeout=5)
        if r.returncode == 0:
            OK("subprocess يعمل")
        else:
            WARN(f"subprocess returncode={r.returncode}")
    except Exception as e:
        ERR(f"subprocess فشل: {e}")

    # ffmpeg
    for cmd in ["ffmpeg", "/data/data/com.termux/files/usr/bin/ffmpeg"]:
        try:
            r = subprocess.run([cmd, "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                ver = r.stdout.decode(errors="replace").split("\n")[0]
                OK(f"ffmpeg ✓  ({ver[:60]})")
                break
        except FileNotFoundError:
            pass
        except Exception as e:
            WARN(f"ffmpeg '{cmd}': {e}")
    else:
        ERR("ffmpeg غير موجود — دمج الفيديو والصوت سيفشل!")

    # am (Android Activity Manager)
    try:
        r = subprocess.run(["am", "--help"], capture_output=True, timeout=5)
        OK("am (Android) متاح") if r.returncode in [0,1] else WARN("am غير متاح")
    except Exception as e:
        WARN(f"am: {e}")

# ══════════════════════════════════════════════
#  6. yt-dlp — اختبار تحليل URL
# ══════════════════════════════════════════════
def check_ytdlp():
    SEP("yt-dlp — اختبار تحليل فيديو")
    try:
        import yt_dlp
        INFO(f"yt-dlp version: {yt_dlp.version.__version__}")

        TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        INFO(f"اختبار: {TEST_URL}")

        opts = {
            "quiet":    False,
            "verbose":  True,
            "logger":   type("L", (), {
                "debug":   lambda s,m: W(f"  [YT-DBG] {m}"),
                "warning": lambda s,m: W(f"  [YT-WARN] {m}"),
                "error":   lambda s,m: ERR(f"  [YT-ERR] {m}"),
            })(),
            "nocheckcertificate": True,
            "socket_timeout": 20,
            "retries": 2,
        }

        start = time.time()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(TEST_URL, download=False)
            elapsed = time.time() - start
            OK(f"تحليل نجح في {elapsed:.1f}s")
            OK(f"  عنوان: {info.get('title','?')[:50]}")
            OK(f"  صيغ:   {len(info.get('formats',[]))}")
        except Exception as e:
            ERR(f"تحليل فشل: {e}")
            W(f"  نوع الخطأ: {type(e).__name__}")

    except ImportError:
        ERR("yt-dlp غير مثبّت")
    except Exception as e:
        ERR(f"خطأ عام: {e}")

# ══════════════════════════════════════════════
#  7. Flask — اختبار السيرفر
# ══════════════════════════════════════════════
def check_flask():
    SEP("Flask — اختبار السيرفر")
    try:
        from flask import Flask
        app_test = Flask("_test")

        @app_test.route("/ping")
        def ping(): return "pong"

        srv_ready = threading.Event()
        def run_srv():
            import logging
            logging.getLogger('werkzeug').setLevel(logging.ERROR)
            try:
                app_test.run(host="127.0.0.1", port=18765,
                             debug=False, use_reloader=False)
            except: pass

        t = threading.Thread(target=run_srv, daemon=True)
        t.start()
        time.sleep(1.5)

        try:
            import urllib.request
            resp = urllib.request.urlopen("http://127.0.0.1:18765/ping", timeout=3)
            if resp.read() == b"pong":
                OK("Flask سيرفر محلي يعمل ✓")
            else:
                WARN("Flask يعمل لكن الرد غير متوقع")
        except Exception as e:
            ERR(f"Flask محلي فشل: {e}")

    except ImportError:
        ERR("Flask غير مثبّت")

# ══════════════════════════════════════════════
#  8. تحليل ملف log.txt الموجود
# ══════════════════════════════════════════════
def analyze_existing_log():
    SEP("تحليل log.txt الموجود")
    log_file = os.path.join(SAVE_PATH, "log.txt")
    if not os.path.exists(log_file):
        WARN("log.txt غير موجود بعد")
        return

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        INFO(f"حجم log.txt: {len(content)} حرف")

        # البحث عن أنماط الأخطاء
        patterns = {
            "No address associated with hostname": "🔴 مشكلة DNS — الشبكة محجوبة في APK",
            "Unable to download webpage":          "🔴 فشل تحميل صفحة يوتيوب",
            "No such file or directory":           "🔴 ملف أو مسار غير موجود",
            "Permission denied":                   "🔴 ممنوع الوصول للملف/المجلد",
            "ModuleNotFoundError":                 "🔴 مكتبة Python مفقودة",
            "ImportError":                         "🔴 خطأ استيراد مكتبة",
            "FATAL":                               "🔴 خطأ مميت",
            "ffmpeg":                              "🟡 ذُكر ffmpeg في السجل",
            "JS runtime":                          "🟡 تحذير JavaScript runtime",
            "DeprecationWarning":                  "🟡 تحذير Deprecation",
            "✅":                                   "🟢 عمليات نجحت",
        }
        found_any = False
        for pattern, meaning in patterns.items():
            count = content.count(pattern)
            if count > 0:
                W(f"  [{count:3d}x] {meaning}")
                found_any = True

        if not found_any:
            INFO("لم يُعثر على أنماط أخطاء معروفة")

        # آخر 20 سطر
        W()
        W("── آخر 15 سطر في log.txt ──")
        last_lines = content.strip().split("\n")[-15:]
        for ln in last_lines:
            W(f"  {ln}")

    except Exception as e:
        ERR(f"لم يمكن قراءة log.txt: {e}")

# ══════════════════════════════════════════════
#  9. التوصيات
# ══════════════════════════════════════════════
def print_recommendations():
    SEP("التوصيات والحلول المقترحة")

    W("""
🔴 المشكلة الرئيسية المرجّحة:
   APK (Flet) يعمل في sandbox معزول عن الشبكة.
   yt-dlp يحاول الاتصال بـ youtube.com لكن DNS يفشل.

📋 الحلول المقترحة بالترتيب:

1. [الأهم] إضافة إذن INTERNET في AndroidManifest.xml:
   <uses-permission android:name="android.permission.INTERNET"/>
   <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
   
   في flet build: تأكد من وجود هذا الإذن في
   buildozer.spec أو flet.yaml

2. إذن التخزين:
   <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"/>
   <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE"/>
   android:requestLegacyExternalStorage="true" في <application>

3. في flet.yaml أضف:
   android:
     permissions:
       - INTERNET
       - ACCESS_NETWORK_STATE
       - WRITE_EXTERNAL_STORAGE
       - READ_EXTERNAL_STORAGE
       - MANAGE_EXTERNAL_STORAGE

4. ffmpeg غير مضمّن في APK:
   يجب تضمين ffmpeg ثنائي في APK أو استخدام
   مكتبة python-ffmpeg-binary

5. النسخة الجديدة من main.py تفتح Chrome الخارجي
   بدلاً من WebView — هذا يتجنب قيود Sandbox

6. إذا استمرت مشكلة الشبكة جرّب:
   opts["source_address"] = "0.0.0.0"  في yt-dlp opts
""")

# ══════════════════════════════════════════════
#  تشغيل التشخيص الكامل
# ══════════════════════════════════════════════
def run_all():
    SEP("B-Ultra APK Diagnosis Tool")
    W(f"التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    SEP()

    check_environment()
    W()
    check_storage()
    W()
    check_network()
    W()
    check_libraries()
    W()
    check_subprocess()
    W()
    # اختبار yt-dlp يحتاج شبكة — شغّله لكن لا تتوقف إذا فشل
    try:
        check_ytdlp()
    except Exception as e:
        ERR(f"check_ytdlp انتهى بخطأ: {e}")
    W()
    check_flask()
    W()
    analyze_existing_log()
    W()
    print_recommendations()

    SEP("نهاية التشخيص")
    W(f"حُفظت النتائج في: {ERROR_FILE}")

    # كتابة الملف
    try:
        with open(ERROR_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n✅ error.txt محفوظ في: {ERROR_FILE}")
    except Exception as e:
        print(f"❌ لم يُحفظ error.txt: {e}")


if __name__ == "__main__":
    run_all()
