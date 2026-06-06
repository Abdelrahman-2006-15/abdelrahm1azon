import os
import re
import json
import time
import random
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup

# ============ الإعدادات الأساسية ============
TELEGRAM_TOKEN = "8877565774:AAE6Rw2qWqneA7Vf5-jsKqwMPSuO0brD8fg"
ADMIN_CHAT_ID = "6933040865"
EXCHANGE_API_KEY = "68cf82d22e898bc81703194d"
DATA_FILE = "data.json"
CHECK_INTERVAL = 3600  # فحص الأسعار كل ساعة

# قفل لمنع تضارب البيانات أثناء حفظها من خيوط (Threads) مختلفة
data_lock = threading.Lock()

# تدوير الـ User-Agents لتجنب حظر أمازون للبوت
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

MARKETS = {
    "eg": {"name": "مصر 🇪🇬", "domain": "www.amazon.eg", "currency": "EGP", "symbol": "ج.م"},
    "sa": {"name": "السعودية 🇸🇦", "domain": "www.amazon.sa", "currency": "SAR", "symbol": "ر.س"},
    "ae": {"name": "الإمارات 🇦🇪", "domain": "www.amazon.ae", "currency": "AED", "symbol": "د.إ"},
}

user_pending = {}

# ============ إدارة قاعدة البيانات بأمان ============
def load_data():
    with data_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {"users": {}, "settings": {"bot_active": True, "features": {"compare": True, "track": True}}}

def save_data(data):
    with data_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, chat_id):
    cid = str(chat_id)
    if cid not in data["users"]:
        data["users"][cid] = {
            "products": {},
            "active": True,
            "joined": datetime.now().isoformat(),
            "name": ""
        }
    return data["users"][cid]

# ============ أدوات شبكة أمازون المحترفة ============
def get_scrapper_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Device-Memory": "8"
    }

# ============ ألياف تيليجرام (Telegram API) ============
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        res = requests.post(url, data=payload, timeout=10)
        return res.json()
    except Exception as e:
        print(f"⚠️ خطأ إرسال تيليجرام: {e}")
        return None

def send_inline_keyboard(chat_id, text, buttons_data):
    # تحويل الأزرار إلى نظام الـ Inline الحديث تحت الرسالة مباشرة
    keyboard = {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in buttons_data]}
    send_message(chat_id, text, reply_markup=keyboard)

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35)
        return r.json()
    except Exception:
        time.sleep(5)  # حماية المعالج في حال انقطاع الشبكة
        return {"result": []}

# ============ جلب أسعار العملات الحقيقية ============
def get_exchange_rates():
    try:
        r = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/EGP", timeout=10)
        d = r.json()
        if d.get("result") == "success":
            rates = d["conversion_rates"]
            return {"EGP": 1.0, "SAR": 1 / rates["SAR"], "AED": 1 / rates["AED"]}
    except Exception:
        pass
    return {"EGP": 1.0, "SAR": 8.5, "AED": 8.7}

# ============ معالجة كشط بيانات أمازون ============
def extract_asin(url):
    patterns = [r"/dp/([A-Z0-9]{10})", r"/gp/product/([A-Z0-9]{10})", r"asin=([A-Z0-9]{10})"]
    for pattern in patterns:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None

def check_seller(soup):
    # فحص دقيق للبائع لمنع تجاوز تجار الطرف الثالث عند التفعيل
    seller_element = soup.find("a", {"id": "sellerProfileTriggerId"})
    if seller_element:
        name = seller_element.get_text(strip=True)
        return "amazon" in name.lower(), name
        
    merchant_element = soup.find("div", {"id": "merchant-info"})
    if merchant_element:
        text = merchant_element.get_text(strip=True)
        return "amazon" in text.lower(), text[:40]
        
    return False, "غير معروف"

def extract_price(soup):
    # محرك بحث متعدد المستويات عن السعر لضمان القنص الصحيح
    for selector in [{"class": "a-price-whole"}, {"id": "priceblock_ourprice"}, {"id": "priceblock_dealprice"}]:
        el = soup.find("span", selector)
        if el:
            clean = re.sub(r"[^\d.]", "", el.get_text(strip=True).replace(",", ""))
            if clean:
                try: return float(clean)
                except ValueError: pass
                
    # فحص أخير في حالة الأسعار المخفية
    offscreen = soup.find("span", {"class": "a-offscreen"})
    if offscreen:
        clean = re.sub(r"[^\d.]", "", offscreen.get_text(strip=True).replace(",", ""))
        try: return float(clean)
        except ValueError: pass
    return None

def get_product_data(asin, market_key, amazon_only=True):
    market = MARKETS[market_key]
    url = f"https://{market['domain']}/dp/{asin}"
    try:
        r = requests.get(url, headers=get_scrapper_headers(), timeout=15)
        if r.status_code != 200:
            return None
            
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("span", {"id": "productTitle"})
        if not title_el:
            return None
            
        title = title_el.get_text(strip=True)[:60]
        is_amazon, seller_name = check_seller(soup)
        seller_note = "✅ أمازون" if is_amazon else f"🏪 {seller_name[:20]}"
        
        if amazon_only and not is_amazon:
            return {"title": title, "price": None, "no_amazon": True, "market_name": market["name"]}
            
        price = extract_price(soup)
        return {
            "title": title, "price": price, "currency": market["currency"],
            "symbol": market["symbol"], "seller": seller_note, "is_amazon": is_amazon,
            "url": url, "market_name": market["name"], "no_amazon": False
        }
    except Exception:
        return None

# ============ منطق البوت ومقارنة الأسعار ============
def compare_prices(chat_id, asin, amazon_only):
    send_message(chat_id, "⏳ جاري فحص ومقارنة الأسعار عبر الأسواق الثلاثة... برعاية <b>Abdelrahman</b>")
    rates = get_exchange_rates()
    results = []
    skipped = []
    
    for key in MARKETS:
        d = get_product_data(asin, key, amazon_only)
        if d:
            if d.get("no_amazon"):
                skipped.append(d["market_name"])
            elif d["price"]:
                d["egp_price"] = round(d["price"] * rates.get(d["currency"], 1), 2)
                results.append(d)
        time.sleep(1.5) # حماية تأخير لتفادي حظر الـ IP
        
    if not results:
        send_message(chat_id, "❌ لم يتم العثور على أسعار مطابقة لشروطك حالياً.")
        return
        
    results.sort(key=lambda x: x["egp_price"])
    title = results[0]["title"]
    seller_type = "أمازون مباشرة" if amazon_only else "جميع البائعين المتاحين"
    
    msg = f"📦 <b>{title}</b>\n🔍 نطاق البحث: <b>{seller_type}</b>\n\n🎯 <b>نتائج مقارنة الأسعار المتوفرة:</b>\n\n"
    for i, r in enumerate(results):
        medal = ["🥇 الأرخص", "🥈 الثاني", "🥉 الثالث"][i] if i < 3 else "🔹"
        msg += (f"{medal} <b>{r['market_name']}</b>\n"
                f" 💰 السعر الأصلي: {r['price']:,.2f} {r['symbol']}\n"
                f" 🔄 بما يعادل: <b>{r['egp_price']:,.2f} ج.م</b>\n"
                f" 🏪 شحن وتعبئة: {r['seller']}\n"
                f" 🔗 <a href='{r['url']}'>انتقل للمنتج مباشرة</a>\n\n")
                
    if skipped:
        msg += f"⚠️ غير متوفر من أمازون مباشرة في: {', '.join(skipped)}\n\n"
        
    if len(results) > 1:
        diff = results[-1]["egp_price"] - results[0]["egp_price"]
        msg += f"💡 <b>مقدار التوفير عند الشراء من السوق الأرخص: {diff:,.2f} ج.م</b>\n\n"
        
    msg += "⚙️ تم التطوير بواسطة: <b>Abdelrahman</b>"
    send_message(chat_id, msg)

# ============ الفحص الدوري الذكي للأسعار المتتبعة ============
def check_all_prices():
    print(f"[{datetime.now().strftime('%H:%M')}] بدأت دورة الفحص التلقائي... بطلب من Abdelrahman")
    data = load_data()
    users = data.get("users", {})
    updated = False
    
    for chat_id, user in users.items():
        if not user.get("active", True):
            continue
        products = user.get("products", {})
        for url, prod in list(products.items()):
            asin = extract_asin(url)
            if not asin: continue
            
            # نفترض دائماً فحص السوق المصري للتتبع المحلي
            d = get_product_data(asin, "eg", amazon_only=prod.get("amazon_only", True))
            if not d or not d.get("price"):
                continue
                
            current_price = d["price"]
            old_price = prod.get("current_price")
            
            data["users"][chat_id]["products"][url]["current_price"] = current_price
            updated = True
            
            if current_price <= prod["target_price"]:
                send_message(chat_id, f"🔥 <b>هبط السعر للمستوى المطلوب!</b>\n\n📦 {prod['title']}\n"
                                      f"💰 السعر الحالي الآن: <b>{current_price:,.2f} ج.م</b>\n"
                                      f"🎯 السعر المستهدف: {prod['target_price']:,.2f} ج.م\n\n"
                                      f"🛒 <a href='{url}'>اضغط هنا للشراء الفوري ومستند لـ Abdelrahman</a>")
            elif old_price and current_price < old_price:
                send_message(chat_id, f"📉 <b>تحديث: السعر انخفض عما سبق!</b>\n\n📦 {prod['title']}\n"
                                      f"💰 من {old_price:,.2f} ج.م ← <b>{current_price:,.2f} ج.م</b>\n"
                                      f"🎯 هدفك: {prod['target_price']:,.2f} ج.م\n\n"
                                      f"🔗 <a href='{url}'>رابط معاينة السلعة</a>")
            time.sleep(2)
            
    if updated:
        save_data(data)

# ============ لوحة التحكم البروفيشنال للأدمن ============
def handle_admin(text, data):
    settings = data.get("settings", {})
    features = settings.get("features", {})
    users = data.get("users", {})
    
    if text == "/admin":
        total = len(users)
        active = sum(1 for u in users.values() if u.get("active", True))
        total_products = sum(len(u.get("products", {})) for u in users.values())
        bot_status = "✅ مفعّل" if settings.get("bot_active", True) else "🔴 معطّل"
        
        msg = (f"👑 <b>لوحة تحكم المشرف الفائقة</b>\n"
               f"المطور المسؤول: <b>Abdelrahman</b>\n\n"
               f"👥 إجمالي المشتركين: {total} (النشطين: {active})\n"
               f"📦 المنتجات المراقبة حالياً: {total_products}\n"
               f"🤖 وضع البوت الحالي: {bot_status}\n\n"
               f"<b>التحكم السريع عبر الأوامر النصية متاح بالكامل لك.</b>")
        send_message(ADMIN_CHAT_ID, msg)
    elif text == "/stop_bot":
        data["settings"]["bot_active"] = False
        send_message(ADMIN_CHAT_ID, "🔴 تم إيقاف استقبال الطلبات بنجاح بواسطة Abdelrahman.")
    elif text == "/start_bot":
        data["settings"]["bot_active"] = True
        send_message(ADMIN_CHAT_ID, "✅ تم تشغيل البوت وإعادة تفعيله بواسطة Abdelrahman.")
    return data

# ============ معالجة طلبات المستخدمين والمحادثات ============
def handle_user_message(chat_id, text, first_name, data):
    global user_pending
    cid = str(chat_id)
    user = get_user(data, chat_id)
    
    if not user.get("name"):
        data["users"][cid]["name"] = first_name
        send_message(ADMIN_CHAT_ID, f"🔔 مستخدم جديد انضم للبوت: {first_name} (ID: {chat_id})")

    if not data["settings"].get("bot_active", True) and chat_id != ADMIN_CHAT_ID:
        send_message(chat_id, "⚠️ البوت يخضع لعملية صيانة وتحديث مجدولة حالياً. يرجى المراجعة لاحقاً.")
        return data

    if not user.get("active", True):
        send_message(chat_id, "🚫 تم تقييد حسابك من استخدام النظام. راجع الدعم الفني.")
        return data

    pending = user_pending.get(cid, {})

    # الأوامر الأساسية
    if text == "/start":
        msg = f"👋 مرحباً بك يا {first_name} في نظام المراقبة الذكي للأسعار من أمازون!\n\n" \
              f"المنصة تمكنك من مقارنة وتتبع السلع عبر مصر والسعودية والإمارات بكفاءة عالية.\n\n" \
              f"🚀 <b>طريقة العمل:</b> فقط أرسل رابط المنتج من تطبيق أو موقع أمازون مباشرة إلى هنا.\n\n" \
              f"📋 /list - لعرض سلعك المراقبة.\n" \
              f"🧹 /clear - لإلغاء مراقبة جميع السلع.\n\n" \
              f"🔒 مبرمج ومطور النظام: <b>Abdelrahman</b>"
        send_message(chat_id, msg)
        user_pending[cid] = {}
        
    elif text == "/list":
        products = user.get("products", {})
        if not products:
            send_message(chat_id, "📭 قائمة المراقبة الخاصة بك فارغة حالياً.")
        else:
            msg = "📋 <b>المنتجات المستهدفة بالمراقبة لديك:</b>\n\n"
            for i, (url, prod) in enumerate(products.items(), 1):
                st = "أمازون حصراً" if prod.get("amazon_only") else "كل التجار"
                msg += f"{i}. <b>{prod['title']}</b>\n💰 الحالي: {prod.get('current_price', 'قيد الفحص')} ج.م | 🎯 هدفك: {prod['target_price']} ج.م\n🏪 النطاق: {st}\n\n"
            send_message(chat_id, msg)
            
    elif text == "/clear":
        data["users"][cid]["products"] = {}
        send_message(chat_id, "🗑️ تم تصفية قائمة تتبعك بنجاح بطلب من المشرف Abdelrahman.")

    # منطق الرد على خطوات المعالجة والـ State Machine
    elif pending.get("waiting_action") and text in ["🔍 قارن الأسعار", "📌 تتبع السعر"]:
        if text == "🔍 قارن الأسعار":
            user_pending[cid] = {"waiting_seller": True, "action": "compare", "asin": pending["asin"]}
            send_inline_keyboard(chat_id, "💡 اختر نطاق البائعين المعتمد للمقارنة:", [("أمازون فقط 🏪", "AMZ"), ("كل البائعين 🌍", "ALL")])
        else:
            user_pending[cid] = {"waiting_target": True, "url": pending["url"], "asin": pending["asin"]}
            send_message(chat_id, "💰 يرجى كتابة السعر المستهدف الذي ترغب في تلقي إشعار فور الوصول إليه (بالجنيه المصري):\nمثال: 450")
            
    elif pending.get("waiting_target"):
        try:
            target_price = float(text.replace(",", ""))
            user_pending[cid] = {"waiting_seller": True, "action": "track", "url": pending["url"], "asin": pending["asin"], "target_price": target_price}
            send_inline_keyboard(chat_id, "💡 اختر نطاق البائعين المعتمد للتتبع التلقائي:", [("أمازون فقط 🏪", "AMZ"), ("كل البائعين 🌍", "ALL")])
        except ValueError:
            send_message(chat_id, "⚠️ عذراً، يرجى إدخال قيمة رقمية صحيحة بدون رموز أو حروف. مثال: 1200")

    # التقاط واكتشاف روابط أمازون المرسلة من المستخدمين
    elif text.startswith("http") and "amazon" in text:
        asin = extract_asin(text)
        if not asin:
            send_message(chat_id, "❌ لم نتمكن من تحديد المعرف القياسي (ASIN) للمنتج. يرجى نسخ الرابط كاملاً من المتصفح.")
            return data
            
        user_pending[cid] = {"waiting_action": True, "url": text, "asin": asin}
        # استخدام الأزرار المدمجة لضمان تجربة مستخدم نظيفة
        send_inline_keyboard(chat_id, "🎯 ما هي الخدمة المطلوبة لهذا المنتج؟", [("🔍 قارن الأسعار", "🔍 قارن الأسعار"), ("📌 تتبع السعر", "📌 تتبع السعر")])
        
    else:
        # التعامل مع أزرار الـ Inline عبر المحاكاة البسيطة أو النصوص المرسلة
        if text in ["AMZ", "ALL", "أمازون فقط 🏪", "كل البائعين 🌍"] or pending.get("waiting_seller"):
            amazon_only = "AMZ" in text or "أمازون" in text
            action = pending.get("action")
            
            if action == "compare":
                asin = pending["asin"]
                user_pending[cid] = {}
                compare_prices(chat_id, asin, amazon_only)
            elif action == "track":
                url = pending["url"]
                target_price = pending["target_price"]
                user_pending[cid] = {}
                
                send_message(chat_id, "⏳ جاري إدراج المنتج بقاعدة البيانات وتحليل سعره الافتتاحي...")
                d = get_product_data(pending["asin"], "eg", amazon_only)
                if d and d.get("price"):
                    data["users"][cid]["products"][url] = {
                        "title": d["title"], "target_price": target_price,
                        "current_price": d["price"], "amazon_only": amazon_only,
                        "added_at": datetime.now().isoformat()
                    }
                    send_message(chat_id, f"✅ تم تفعيل المراقبة بنجاح!\n\n📦 <b>{d['title']}</b>\n💰 السعر الحالي بمصر: {d['price']:,.2f} ج.م\n🎯 هدف الإشعار: {target_price:,.2f} ج.م\n\nنظام مبرمج بواسطة المشرف: <b>Abdelrahman</b>")
                else:
                    send_message(chat_id, "❌ تعذر تتبع السلعة بالمواصفات المحددة (قد تكون غير متوفرة حالياً من البائع المختار).")
        else:
            send_message(chat_id, "❓ عذراً، لم أفهم الإجراء المطلوب. من فضلك أرسل رابط منتج أمازون لبدء الفحص والمقارنة.")
            
    return data

# ============ خادم واجهة الويب الإدارية الاحترافية ============
class ProAdminDashboard(BaseHTTPRequestHandler):
    def log_message(self, format, *args): 
        pass  # حظر الـ Logs السطحية بالكونسول للحفاظ على نظافة البيئة التجريبية
        
    def do_GET(self):
        data = load_data()
        users = data.get("users", {})
        
        total = len(users)
        active_count = sum(1 for u in users.values() if u.get("active", True))
        total_products = sum(len(u.get("products", {})) for u in users.values())
        bot_active = data.get("settings", {}).get("bot_active", True)
        
        rows = ""
        for cid, u in users.items():
            st = "<span class='badge on'>نشط</span>" if u.get("active", True) else "<span class='badge off'>محظور</span>"
            rows += f"<tr><td>{u.get('name', 'مجهول')}</td><td><code>{cid}</code></td><td>{len(u.get('products',{}))}</td><td>{st}</td><td>{u.get('joined',' ')[:10]}</td></tr>"
            
        html = f"""<!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <title>Abdelrahmazon Enterprise Control Panel</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0b0f19; color: #f1f5f9; margin: 0; padding: 40px; }}
                h1 {{ color: #ff7a00; border-bottom: 2px solid #1e293b; padding-bottom: 15px; text-align: center; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 30px; }}
                .card {{ background: #111827; border: 1px solid #1f2937; padding: 25px; border-radius: 12px; text-align: center; }}
                .card .val {{ font-size: 2.2rem; font-weight: bold; color: #ff7a00; margin-bottom: 5px; }}
                .card .lbl {{ color: #9ca3af; font-size: 0.95rem; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background: #111827; border-radius: 8px; overflow: hidden; }}
                th, td {{ padding: 14px 20px; text-align: right; border-bottom: 1px solid #1f2937; }}
                th {{ background: #1f2937; color: #ff7a00; font-weight: 600; }}
                .badge {{ padding: 5px 12px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }}
                .on {{ background: rgba(16, 185, 129, 0.2); color: #10b981; }}
                .off {{ background: rgba(239, 68, 68, 0.2); color: #ef4444; }}
                footer {{ text-align: center; margin-top: 40px; color: #4b5563; font-size: 0.9rem; }}
            </style>
        </head>
        <body>
            <h1>🛒 Abdelrahmazon Enterprise — لوحة تحكم الإدارة</h1>
            <div class="grid">
                <div class="card"><div class="val">{total}</div><div class="lbl">إجمالي قاعدة البيانات</div></div>
                <div class="card"><div class="val">{active_count}</div><div class="lbl">المستخدمين النشطين</div></div>
                <div class="card"><div class="val">{total_products}</div><div class="lbl">الروابط تحت التعقب المستمر</div></div>
                <div class="card"><div class="val">{'مفعّل' if bot_active else 'متوقف'}</div><div class="lbl">حالة الخادم الفورية</div></div>
            </div>
            <h2>👥 سجل المشتركين في النظام</h2>
            <table>
                <thead><tr><th>الاسم المستعار</th><th>معرّف الشات (Chat ID)</th><th>الروابط المتتبعة</th><th>حالة الحساب</th><th>تاريخ التسجيل</th></tr></thead>
                <tbody>{rows if rows else "<tr><td colspan='5' style='text-align:center;'>لا توجد بيانات مسجلة حتى الآن</td></tr>"}</tbody>
            </table>
            <footer>النظام والملكية الفكرية محفوظة لـ <b>Abdelrahman</b> &copy; {datetime.now().year}</footer>
        </body>
        </html>"""
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

def run_server_thread():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), ProAdminDashboard)
    print(f"🌐 تم تفعيل خادم لوحة التحكم الإدارية للويب بنجاح على المنفذ المحمي {port} لـ Abdelrahman")
    server.serve_forever()

# ============ نقطة الانطلاق الرئيسية الفائقة (Main Loop) ============
def main():
    print("🚀 تم تشغيل محرك ومستشعر البوت بأعلى كفاءة إنتاجية... الملكية لـ Abdelrahman")
    
    # تشغيل خادم لوحة الويب كخلفية آمنة (Daemon Thread)
    t = threading.Thread(target=run_server_thread, daemon=True)
    t.start()
    
    send_message(ADMIN_CHAT_ID, "🚀 <b>أهلاً Abdelrahman!</b> تم إعادة تشغيل البوت بنجاح وبكفاءة بروفيشينال تامة.\nأرسل /admin للاطلاع على الإحصائيات الفورية.")
    
    offset = None
    last_check_time = time.time()
    
    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            first_name = msg.get("chat", {}).get("first_name", "مستخدم")
            
            if not text or not chat_id:
                continue
                
            data = load_data()
            
            # فلترة وتحليل أوامر المشرف الأساسي لـ Abdelrahman
            ADMIN_KEYWORDS = ["/admin", "/stop_bot", "/start_bot"]
            is_admin = chat_id == ADMIN_CHAT_ID and any(text.startswith(k) for k in ADMIN_KEYWORDS)
            
            if is_admin:
                data = handle_admin(text, data)
            else:
                data = handle_user_message(chat_id, text, first_name, data)
                
            save_data(data)
            
        # فحص الأسعار الدوري المتوازي دون تجميد استقبال رسائل المستخدمين
        if time.time() - last_check_time >= CHECK_INTERVAL:
            try:
                check_all_prices()
            except Exception as e:
                print(f"❌ حدث عطل مفاجئ أثناء جولة قنص الأسعار الدورية: {e}")
            last_check_time = time.time()
            
        time.sleep(1) # تأخير طفيف ومستقر لمنع أي تحميل زائد على المعالج (0% CPU idle state)

if __name__ == "__main__":
    main()
