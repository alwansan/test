import flet as ft
import threading
import subprocess
import time
import os
import traceback

# ══════════════════════════════════════════════
#  مسار الحفظ واللوق
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

SAVE_PATH = get_save_path()
LOG_FILE  = os.path.join(SAVE_PATH, "log.txt")

flask_started = threading.Event()

# ══════════════════════════════════════════════
#  تشغيل Flask في خيط daemon
# ══════════════════════════════════════════════
def run_flask():
    try:
        import B_Ultra_v14
        flask_started.set()
        B_Ultra_v14.app.run(
            host="0.0.0.0",
            port=8000,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as e:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[FLASK-FATAL] {e}\n{traceback.format_exc()}")
        except: pass
        flask_started.set()

# ══════════════════════════════════════════════
#  فتح المتصفح — يجرب عدة طرق
# ══════════════════════════════════════════════
def open_browser(url="http://localhost:8000"):
    cmds = [
        ["am", "start", "-n",
         "com.android.chrome/com.google.android.apps.chrome.Main",
         "-a", "android.intent.action.VIEW", "-d", url],
        ["am", "start", "-a", "android.intent.action.VIEW",
         "-d", url, "--activity-clear-top"],
        ["termux-open-url", url],
        ["xdg-open", url],
    ]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=6)
            if r.returncode == 0:
                return True
        except: continue
    return False

def read_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except: pass
    return "(لا يوجد log بعد)"

# ══════════════════════════════════════════════
#  طلب الأذونات عبر Flet PermissionHandler
# ══════════════════════════════════════════════
async def request_all_permissions(page: ft.Page):
    """
    يطلب جميع الأذونات الضرورية دفعة واحدة عبر Flet PermissionHandler.
    """
    ph = ft.PermissionHandler()
    page.overlay.append(ph)
    await page.update_async()

    permissions = [
        ft.Permission.STORAGE,
        ft.Permission.MANAGE_EXTERNAL_STORAGE,
        ft.Permission.VIDEOS,
        ft.Permission.AUDIO,
        ft.Permission.IGNORE_BATTERY_OPTIMIZATIONS,
        ft.Permission.NOTIFICATION,
        ft.Permission.SCHEDULE_EXACT_ALARM,
        ft.Permission.SYSTEM_ALERT_WINDOW,
        ft.Permission.REQUEST_INSTALL_PACKAGES,
    ]

    results = {}
    for perm in permissions:
        try:
            status = await ph.check_permission_async(perm)
            if status != ft.PermissionStatus.GRANTED:
                status = await ph.request_permission_async(perm)
            results[perm.name] = status.name
        except Exception as e:
            results[str(perm)] = f"error: {e}"

    # لوق النتائج
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n[PERMISSIONS]\n")
            for k, v in results.items():
                f.write(f"  {k}: {v}\n")
    except: pass

    return results

# ══════════════════════════════════════════════
#  Flet UI
# ══════════════════════════════════════════════
def main(page: ft.Page):
    page.title      = "B-Ultra"
    page.padding    = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor    = "#080b12"

    loading_text = ft.Text("⏳ جارٍ تشغيل السيرفر...", size=16,
                            color="#63b3ed", text_align=ft.TextAlign.CENTER)
    loading_sub  = ft.Text("سيُفتح المتصفح تلقائياً", size=12,
                            color="#48556a", text_align=ft.TextAlign.CENTER)
    progress = ft.ProgressRing(color="#63b3ed", width=40, height=40)

    perm_status = ft.Text("", size=11, color="#68d391",
                           text_align=ft.TextAlign.CENTER, visible=False)

    open_btn = ft.ElevatedButton(
        "🌐 افتح في المتصفح",
        on_click=lambda _: open_browser(),
        bgcolor="#0d1018", color="#63b3ed", visible=False,
    )
    perm_btn = ft.FilledButton(
        "🔐 منح الأذونات الضرورية",
        on_click=lambda _: page.run_task(do_permissions),
        style=ft.ButtonStyle(
            bgcolor={"": "#1a2535"},
            color={"": "#63b3ed"},
        ),
        visible=False,
    )
    log_btn = ft.TextButton(
        "📋 عرض log.txt",
        on_click=lambda _: show_log(page),
        style=ft.ButtonStyle(color={"": "#48556a"}),
    )

    page.add(ft.Column([
        ft.Container(height=60),
        ft.Text("🦅 B-Ultra", size=30, weight=ft.FontWeight.W_900,
                color="#63b3ed", text_align=ft.TextAlign.CENTER),
        ft.Text("v14 — Playlist Edition", size=13, color="#48556a",
                text_align=ft.TextAlign.CENTER),
        ft.Container(height=32),
        progress,
        ft.Container(height=16),
        loading_text,
        loading_sub,
        ft.Container(height=12),
        perm_status,
        ft.Container(height=16),
        perm_btn,
        ft.Container(height=8),
        open_btn,
        ft.Container(height=8),
        log_btn,
    ], alignment=ft.MainAxisAlignment.START,
       horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True))
    page.update()

    async def do_permissions():
        perm_btn.text = "⏳ جارٍ طلب الأذونات..."
        perm_btn.disabled = True
        perm_status.visible = False
        page.update()
        results = await request_all_permissions(page)
        granted = sum(1 for v in results.values() if v == "GRANTED")
        total   = len(results)
        perm_status.value   = f"✅ {granted}/{total} إذن مُمنوح"
        perm_status.visible = True
        perm_btn.text       = "🔐 منح الأذونات مجدداً"
        perm_btn.disabled   = False
        page.update()

    async def startup():
        # طلب الأذونات فوراً عند أول تشغيل
        perm_btn.visible = True
        page.update()
        await request_all_permissions(page)

        # بدء Flask في الخلفية
        threading.Thread(target=run_flask, daemon=True).start()
        flask_started.wait(timeout=15)
        time.sleep(1.2)

        loading_text.value = "🌐 فتح المتصفح..."
        loading_sub.value  = "http://localhost:8000"
        page.update()

        # محاولة فتح المتصفح (مرتين)
        opened = open_browser()
        if not opened:
            time.sleep(1.5)
            opened = open_browser()

        progress.visible  = False
        open_btn.visible  = True
        if opened:
            loading_text.value = "✅ المتصفح مفتوح — السيرفر يعمل بالخلفية"
            loading_sub.value  = "http://localhost:8000"
        else:
            loading_text.value = "⚠️ اضغط الزر لفتح المتصفح يدوياً"
            loading_sub.value  = "http://localhost:8000"
        page.update()

    page.run_task(startup)


def show_log(page: ft.Page):
    content = read_log()
    if len(content) > 4000:
        content = "...[مقتطع]\n" + content[-4000:]
    dlg = ft.AlertDialog(
        title=ft.Text("📋 log.txt", color="#63b3ed"),
        content=ft.Column([
            ft.Text(content, size=10, font_family="monospace",
                    color="#dde6f0", selectable=True)
        ], scroll=ft.ScrollMode.AUTO, height=420),
        actions=[ft.TextButton("إغلاق", on_click=lambda _: close_dlg(page, dlg))],
    )
    page.dialog = dlg
    dlg.open    = True
    page.update()


def close_dlg(page, dlg):
    dlg.open = False
    page.update()


ft.app(target=main)
