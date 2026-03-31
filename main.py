import flet as ft
import threading
import time
# نقوم باستدعاء ملفك الأصلي كـ مكتبة
import B_Ultra_v14

def run_flask():
    # نقوم بتشغيل سيرفر فلاسك الخاص بك في الخلفية
    # استخدمنا 127.0.0.1 ليكون محلياً داخل التطبيق فقط
    B_Ultra_v14.app.run(host='127.0.0.1', port=8000, debug=False, use_reloader=False)

def main(page: ft.Page):
    # إعدادات نافذة التطبيق
    page.title = "B-Ultra"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK

    # 1. تشغيل السكربت الخاص بك (Flask) في مسار (Thread) منفصل
    # لكي لا يتجمد التطبيق
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. الانتظار ثانية واحدة للتأكد من أن السيرفر اشتغل تماماً
    time.sleep(1)

    # 3. إنشاء متصفح داخلي (WebView) لعرض واجهة الـ HTML الخاصة بك
    webview = ft.WebView(
        url="http://127.0.0.1:8000",
        expand=True, # لجعله يملأ شاشة الهاتف بالكامل
    )
    
    # إضافة المتصفح إلى صفحة التطبيق
    page.add(webview)

# تشغيل تطبيق Flet
ft.app(target=main)
