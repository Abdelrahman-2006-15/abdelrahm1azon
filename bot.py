import requests
import json
import time
import re
import os
from datetime import datetime
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ============ إعدادات ============
TELEGRAM_TOKEN = "8877565774:AAE6Rw2qWqneA7Vf5-jsKqwMPSuO0brD8fg"
ADMIN_CHAT_ID = "6933040865"
EXCHANGE_API_KEY = "68cf82d22e898bc81703194d"
DATA_FILE = "data.json"
CHECK_INTERVAL = 3600

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MARKETS = {
    "eg": {"name": "مصر 🇪🇬",     "domain": "www.amazon.eg",  "currency": "EGP", "symbol": "ج.م"},
    "sa": {"name": "السعودية 🇸🇦", "domain": "www.amazon.sa",  "currency": "SAR", "symbol": "ر.س"},
    "ae": {"name": "الإمارات 🇦🇪", "domain": "www.amazon.ae",  "currency": "AED", "symbol": "د.إ"},
}

# pending لكل يوزر منفصل
user_pending = {}

# ============ قاعدة البيانات ============
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "settings": {"bot_active": True, "features": {"compare": True, "track": True}}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, chat_id):
    cid = str(chat_id)
    if cid not in data["users"]:
        data["users"][cid] = {"products": {}, "active": True, "joined": datetime.now().isoformat(), "name": ""}
    return data["users"][cid]

# ============ تيليجرام ============
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    d = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        d["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, data=d, timeout=10)
    except Exception as e:
        print(f"خطأ تيليجرام: {e}")

def send_keyboard(chat_id, text, buttons):
    kb = {"keyboard": [[{"text": b}] for b in buttons], "one_time_keyboard": True, "resize_keyboard": True}
    send_message(chat_id, text, reply_markup=kb)

def remove_keyboard(chat_id, text):
    send_message(chat_id, text, reply_markup={"remove_keyboard": True})

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35)
        return r.json()
    except:
        return {"result": []}

def notify_admin(text):
    send_message(ADMIN_CHAT_ID, text)

# ============ أسعار العملات ============
def get_exchange_rates():
    try:
        r = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/EGP", timeout=10)
        d = r.json()
        if d.get("result") == "success":
            rates = d["conversion_rates"]
            return {"EGP": 1.0, "SAR": 1 / rates["SAR"], "AED": 1 / rates["AED"]}
    except:
        pass
    return {"EGP": 1.0, "SAR": 8.5, "AED": 8.7}

# ============ أمازون ============
def extract_asin(url):
    for pattern in [r"/dp/([A-Z0-9]{10})", r"/gp/product/([A-Z0-9]{10})"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

def check_seller(soup):
    for el_id in ["sellerProfileTriggerId"]:
        el = soup.find("a", {"id": el_id})
        if el:
            name = el.get_text(strip=True)
            return "amazon" in name.lower(), name
    merchant = soup.find("div", {"id": "merchant-info"})
    if merchant:
        text = merchant.get_text(strip=True)
        return "amazon" in text.lower(), text[:40]
    return False, "غير معروف"

def extract_price(soup):
    for sel in [{"class": "a-price-whole"}, {"id": "priceblock_ourprice"},
                {"id": "priceblock_dealprice"}, {"class": "a-offscreen"}]:
        el = soup.find("span", sel)
        if el:
            clean = re.sub(r"[^\d.]", "", el.get_text(strip=True).replace(",", ""))
            if clean:
                try:
                    return float(clean)
                except:
                    pass
    return None

def get_product_data(asin, market_key, amazon_only=True):
    market = MARKETS[market_key]
    url = f"https://{market['domain']}/dp/{asin}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("span", {"id": "productTitle"})
        title = title_el.get_text(strip=True)[:70] if title_el else None
        is_amazon, seller_name = check_seller(soup)
        seller_note = "✅ أمازون" if is_amazon else f"🏪 {seller_name[:25]}"
        if amazon_only and not is_amazon:
            return {"title": title, "price": None, "no_amazon": True,
                    "currency": market["currency"], "symbol": market["symbol"],
                    "seller": seller_note, "url": url, "market_name": market["name"]}
        price = extract_price(soup)
        return {"title": title, "price": price, "currency": market["currency"],
                "symbol": market["symbol"], "seller": seller_note, "is_amazon": is_amazon,
                "url": url, "market_name": market["name"], "no_amazon": False}
    except Exception as e:
        print(f"خطأ {market_key}: {e}")
        return None

def get_eg_price(url, amazon_only):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("span", {"id": "productTitle"})
        title = title_el.get_text(strip=True)[:60] if title_el else "منتج غير معروف"
        is_amazon, _ = check_seller(soup)
        if amazon_only and not is_amazon:
            return title, None, "not_amazon"
        price = extract_price(soup)
        return title, price, "ok"
    except:
        return None, None, "error"

# ============ مقارنة الأسعار ============
def compare_prices(chat_id, asin, amazon_only):
    send_message(chat_id, "⏳ بقارن الأسعار على الـ 3 مواقع... لحظة!")
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
        time.sleep(1)

    if not results:
        send_message(chat_id, "❌ مش لقيت أسعار" + ("\nالمنتج مش بيتباع من أمازون مباشرة." if amazon_only else ""))
        return

    results.sort(key=lambda x: x["egp_price"])
    title = results[0]["title"] or "المنتج"
    seller_type = "أمازون فقط" if amazon_only else "كل البائعين"
    msg = f"📦 <b>{title}</b>\n🔍 البحث عن: <b>{seller_type}</b>\n\n💰 <b>مقارنة الأسعار:</b>\n\n"
    for i, r in enumerate(results):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else "  "
        msg += (f"{medal} <b>{r['market_name']}</b>\n"
                f"   السعر: {r['price']:,.0f} {r['symbol']}\n"
                f"   = <b>{r['egp_price']:,.0f} ج.م</b>\n"
                f"   البائع: {r['seller']}\n"
                f"   🔗 <a href='{r['url']}'>فتح الصفحة</a>\n\n")
    if skipped:
        msg += f"⚠️ مش متاح من أمازون في: {', '.join(skipped)}\n\n"
    if len(results) > 1:
        diff = results[-1]["egp_price"] - results[0]["egp_price"]
        msg += f"💡 <b>توفر لو اشتريت من الأرخص: {diff:,.0f} ج.م</b>"
    send_message(chat_id, msg)

# ============ فحص الأسعار الدورية ============
def check_all_prices(data):
    users = data.get("users", {})
    for chat_id, user in users.items():
        if not user.get("active", True):
            continue
        products = user.get("products", {})
        for url, prod in list(products.items()):
            amazon_only = prod.get("amazon_only", True)
            title, current_price, status = get_eg_price(url, amazon_only)
            if status == "not_amazon":
                send_message(chat_id, f"⚠️ <b>{prod['title']}</b>\nالمنتج اتباع من تاجر خارجي مش أمازون.")
                continue
            if current_price is None:
                continue
            old_price = prod.get("current_price")
            data["users"][chat_id]["products"][url]["current_price"] = current_price
            if current_price <= prod["target_price"]:
                send_message(chat_id,
                    f"🔥 <b>السعر وصل!</b>\n\n📦 {prod['title']}\n"
                    f"💰 السعر دلوقتي: <b>{current_price} ج.م</b>\n"
                    f"🎯 السعر اللي طلبته: {prod['target_price']} ج.م\n\n"
                    f"🛒 اشتري دلوقتي:\n{url}")
            elif old_price and current_price < old_price:
                send_message(chat_id,
                    f"📉 <b>السعر نزل!</b>\n\n📦 {prod['title']}\n"
                    f"💰 من {old_price} → <b>{current_price} ج.م</b>\n"
                    f"🎯 السعر المطلوب: {prod['target_price']} ج.م\n\n"
                    f"🔗 {url}")
            time.sleep(1)
    save_data(data)
    return data

# ============ أوامر الأدمن ============
def handle_admin(text, data):
    settings = data.get("settings", {})
    features = settings.get("features", {})
    users = data.get("users", {})

    # إحصائيات
    if text == "/admin":
        total = len(users)
        active = sum(1 for u in users.values() if u.get("active", True))
        total_products = sum(len(u.get("products", {})) for u in users.values())
        bot_status = "✅ شغال" if settings.get("bot_active", True) else "🔴 متوقف"
        compare_status = "✅" if features.get("compare", True) else "❌"
        track_status = "✅" if features.get("track", True) else "❌"
        msg = (f"👑 <b>لوحة الأدمن</b>\n\n"
               f"👥 المستخدمين: {total} (نشط: {active})\n"
               f"📦 إجمالي المنتجات المتابعة: {total_products}\n"
               f"🤖 البوت: {bot_status}\n\n"
               f"⚙️ <b>الخدمات:</b>\n"
               f"🔍 المقارنة: {compare_status}\n"
               f"📌 التتبع: {track_status}\n\n"
               f"<b>الأوامر:</b>\n"
               f"/stop_bot — إيقاف البوت\n"
               f"/start_bot — تشغيل البوت\n"
               f"/disable_compare — إيقاف المقارنة\n"
               f"/enable_compare — تشغيل المقارنة\n"
               f"/disable_track — إيقاف التتبع\n"
               f"/enable_track — تشغيل التتبع\n"
               f"/users — قائمة المستخدمين\n"
               f"/broadcast نص — رسالة لكل المستخدمين\n"
               f"/ban CHAT_ID — حظر مستخدم\n"
               f"/unban CHAT_ID — فك الحظر")
        send_message(ADMIN_CHAT_ID, msg)

    elif text == "/stop_bot":
        data["settings"]["bot_active"] = False
        save_data(data)
        send_message(ADMIN_CHAT_ID, "🔴 البوت اتوقف. المستخدمين مش هيقدروا يستخدموه.")

    elif text == "/start_bot":
        data["settings"]["bot_active"] = True
        save_data(data)
        send_message(ADMIN_CHAT_ID, "✅ البوت اتشغل تاني!")

    elif text == "/disable_compare":
        data["settings"]["features"]["compare"] = False
        save_data(data)
        send_message(ADMIN_CHAT_ID, "❌ خدمة المقارنة اتعطلت.")

    elif text == "/enable_compare":
        data["settings"]["features"]["compare"] = True
        save_data(data)
        send_message(ADMIN_CHAT_ID, "✅ خدمة المقارنة اتشغلت.")

    elif text == "/disable_track":
        data["settings"]["features"]["track"] = False
        save_data(data)
        send_message(ADMIN_CHAT_ID, "❌ خدمة التتبع اتعطلت.")

    elif text == "/enable_track":
        data["settings"]["features"]["track"] = True
        save_data(data)
        send_message(ADMIN_CHAT_ID, "✅ خدمة التتبع اتشغلت.")

    elif text == "/users":
        if not users:
            send_message(ADMIN_CHAT_ID, "مفيش مستخدمين لسه.")
            return data
        msg = f"👥 <b>المستخدمين ({len(users)}):</b>\n\n"
        for cid, u in users.items():
            status = "✅" if u.get("active", True) else "🚫"
            products_count = len(u.get("products", {}))
            name = u.get("name", "بدون اسم")
            msg += f"{status} {name} | ID: <code>{cid}</code> | منتجات: {products_count}\n"
        send_message(ADMIN_CHAT_ID, msg)

    elif text.startswith("/broadcast "):
        broadcast_text = text[11:]
        sent = 0
        for cid, u in users.items():
            if u.get("active", True) and cid != ADMIN_CHAT_ID:
                send_message(cid, f"📢 <b>رسالة من الإدارة:</b>\n\n{broadcast_text}")
                sent += 1
                time.sleep(0.3)
        send_message(ADMIN_CHAT_ID, f"✅ الرسالة اتبعتت لـ {sent} مستخدم.")

    elif text.startswith("/ban "):
        target_id = text[5:].strip()
        if target_id in data["users"]:
            data["users"][target_id]["active"] = False
            save_data(data)
            send_message(ADMIN_CHAT_ID, f"🚫 تم حظر المستخدم {target_id}")
            send_message(target_id, "🚫 تم إيقاف حسابك. تواصل مع الإدارة.")
        else:
            send_message(ADMIN_CHAT_ID, "❌ المستخدم ده مش موجود.")

    elif text.startswith("/unban "):
        target_id = text[7:].strip()
        if target_id in data["users"]:
            data["users"][target_id]["active"] = True
            save_data(data)
            send_message(ADMIN_CHAT_ID, f"✅ تم فك حظر المستخدم {target_id}")
            send_message(target_id, "✅ تم تفعيل حسابك مرة تانية!")
        else:
            send_message(ADMIN_CHAT_ID, "❌ المستخدم ده مش موجود.")

    return data

# ============ معالجة رسائل المستخدم ============
def handle_user_message(chat_id, text, first_name, data):
    global user_pending
    cid = str(chat_id)
    text = text.strip()

    settings = data.get("settings", {})
    features = settings.get("features", {})

    # تسجيل المستخدم
    user = get_user(data, chat_id)
    if not user.get("name"):
        data["users"][cid]["name"] = first_name
        notify_admin(f"👤 مستخدم جديد!\nالاسم: {first_name}\nID: {chat_id}")

    # فحص لو البوت متوقف
    if not settings.get("bot_active", True):
        send_message(chat_id, "⚠️ البوت متوقف مؤقتاً. حاول تاني بعدين.")
        return data

    # فحص لو المستخدم محظور
    if not user.get("active", True):
        send_message(chat_id, "🚫 حسابك محظور. تواصل مع الإدارة.")
        return data

    pending = user_pending.get(cid, {})

    if text == "/start":
        compare_status = "✅ مقارنة أسعار" if features.get("compare", True) else ""
        track_status = "✅ تتبع سعر" if features.get("track", True) else ""
        services = "\n".join(filter(None, [compare_status, track_status]))
        send_message(chat_id,
            f"👋 أهلاً {first_name}!\n\n"
            f"أنا بوت أمازون الشاطر 😎\n\n"
            f"<b>الخدمات المتاحة:</b>\n{services}\n\n"
            f"ابعتلي لينك منتج من أمازون وأنا هسألك عايز إيه! 🚀\n\n"
            f"/list — المنتجات اللي بتتابعها\n"
            f"/clear — امسح كل المنتجات")

    elif text == "/list":
        products = user.get("products", {})
        if not products:
            send_message(chat_id, "📭 مفيش منتجات بتتابعها دلوقتي.")
        else:
            msg = "📋 <b>المنتجات اللي بتتابعها:</b>\n\n"
            for i, (url, prod) in enumerate(products.items(), 1):
                seller_type = "أمازون فقط" if prod.get("amazon_only") else "كل البائعين"
                msg += (f"{i}. {prod['title']}\n"
                        f"   💰 الحالي: {prod.get('current_price', '؟')} ج.م\n"
                        f"   🎯 المطلوب: {prod['target_price']} ج.م\n"
                        f"   🏪 البائع: {seller_type}\n\n")
            send_message(chat_id, msg)

    elif text == "/clear":
        data["users"][cid]["products"] = {}
        save_data(data)
        send_message(chat_id, "🗑️ تم مسح كل المنتجات!")

    # رد على سؤال نوع البائع
    elif text in ["🏪 أمازون فقط", "🌍 كل البائعين"] and pending.get("waiting_seller"):
        amazon_only = text == "🏪 أمازون فقط"
        action = pending.get("action")
        remove_keyboard(chat_id, "تمام! ⏳")

        if action == "compare":
            asin = pending["asin"]
            user_pending[cid] = {}
            compare_prices(chat_id, asin, amazon_only)

        elif action == "track":
            url = pending["url"]
            target_price = pending["target_price"]
            user_pending[cid] = {}
            send_message(chat_id, "⏳ بجيب معلومات المنتج...")
            title, current_price, status = get_eg_price(url, amazon_only)
            if status == "not_amazon":
                send_message(chat_id, "❌ المنتج مش بيتباع من أمازون دلوقتي.\nجرب 'كل البائعين'.")
                return data
            if not title:
                send_message(chat_id, "❌ مقدرتش أجيب معلومات المنتج.")
                return data
            data["users"][cid]["products"][url] = {
                "title": title, "target_price": target_price,
                "current_price": current_price, "amazon_only": amazon_only,
                "added_at": datetime.now().isoformat()
            }
            save_data(data)
            price_msg = f"{current_price} ج.م" if current_price else "مش ظاهر"
            send_message(chat_id,
                f"✅ تم إضافة المنتج!\n\n"
                f"📦 <b>{title}</b>\n"
                f"💰 السعر الحالي: {price_msg}\n"
                f"🎯 هبعتلك لما يوصل: {target_price} ج.م\n"
                f"🏪 البائع: {'أمازون فقط' if amazon_only else 'كل البائعين'}")

    # رد على سؤال مقارنة أو تتبع
    elif text in ["🔍 قارن الأسعار", "📌 تتبع السعر"] and pending.get("waiting_action"):
        action = "compare" if text == "🔍 قارن الأسعار" else "track"

        if action == "compare" and not features.get("compare", True):
            remove_keyboard(chat_id, "❌ خدمة المقارنة مش متاحة دلوقتي.")
            return data
        if action == "track" and not features.get("track", True):
            remove_keyboard(chat_id, "❌ خدمة التتبع مش متاحة دلوقتي.")
            return data

        if action == "track":
            user_pending[cid] = {"waiting_target": True, "url": pending["url"], "asin": pending["asin"]}
            remove_keyboard(chat_id, "اكتب السعر المطلوب بالجنيه المصري 💰\nمثال: 500")
        else:
            user_pending[cid] = {"waiting_seller": True, "action": "compare", "asin": pending["asin"]}
            send_keyboard(chat_id, "البحث بيكون من؟", ["🏪 أمازون فقط", "🌍 كل البائعين"])

    # رد على سؤال السعر
    elif pending.get("waiting_target"):
        try:
            target_price = float(text.replace(",", ""))
        except:
            send_message(chat_id, "⚠️ ادخل رقم صح. مثال: 500")
            return data
        user_pending[cid] = {
            "waiting_seller": True, "action": "track",
            "url": pending["url"], "asin": pending["asin"],
            "target_price": target_price
        }
        send_keyboard(chat_id, "التتبع بيكون من؟", ["🏪 أمازون فقط", "🌍 كل البائعين"])

    # لينك أمازون
    elif text.startswith("http") and "amazon" in text:
        asin = extract_asin(text)
        if not asin:
            send_message(chat_id, "❌ مش قادر أجيب كود المنتج.\nجرب تبعت اللينك من صفحة المنتج مباشرة.")
            return data

        buttons = []
        if features.get("compare", True):
            buttons.append("🔍 قارن الأسعار")
        if features.get("track", True):
            buttons.append("📌 تتبع السعر")

        if not buttons:
            send_message(chat_id, "⚠️ كل الخدمات متوقفة دلوقتي.")
            return data

        user_pending[cid] = {"waiting_action": True, "url": text, "asin": asin}
        send_keyboard(chat_id, "عايز إيه؟ 👇", buttons)

    else:
        send_message(chat_id, "❓ ابعتلي لينك منتج من أمازون وأنا هكمل! 😊\nأو ابعت /start للتعليمات.")

    return data

# ============ لوحة تحكم ويب ============
class AdminHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # إخفاء logs

    def do_GET(self):
        data = load_data()
        settings = data.get("settings", {})
        features = settings.get("features", {})
        users = data.get("users", {})
        total = len(users)
        active_users = sum(1 for u in users.values() if u.get("active", True))
        total_products = sum(len(u.get("products", {})) for u in users.values())
        bot_active = settings.get("bot_active", True)

        users_rows = ""
        for cid, u in users.items():
            status = "نشط ✅" if u.get("active", True) else "محظور 🚫"
            products_count = len(u.get("products", {}))
            name = u.get("name", "بدون اسم")
            joined = u.get("joined", "")[:10]
            users_rows += f"<tr><td>{name}</td><td>{cid}</td><td>{products_count}</td><td>{status}</td><td>{joined}</td></tr>"

        html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Abdelrahmazon Admin</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
  h1 {{ color: #f97316; text-align: center; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; justify-content: center; margin: 20px 0; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 20px 30px; text-align: center; min-width: 140px; }}
  .card .num {{ font-size: 2em; font-weight: bold; color: #f97316; }}
  .card .label {{ color: #94a3b8; margin-top: 5px; }}
  .section {{ background: #1e293b; border-radius: 12px; padding: 20px; margin: 16px 0; }}
  .section h2 {{ margin-top: 0; color: #f97316; }}
  .toggle {{ display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #334155; }}
  .badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.85em; }}
  .on {{ background: #166534; color: #86efac; }}
  .off {{ background: #7f1d1d; color: #fca5a5; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 10px; text-align: right; border-bottom: 1px solid #334155; }}
  th {{ color: #94a3b8; font-weight: normal; }}
  .btn {{ padding: 6px 14px; border-radius: 8px; border: none; cursor: pointer; font-size: 0.85em; }}
  .btn-green {{ background: #166534; color: #86efac; }}
  .btn-red {{ background: #7f1d1d; color: #fca5a5; }}
  a {{ color: #f97316; text-decoration: none; }}
</style>
</head>
<body>
<h1>🛒 Abdelrahmazon — لوحة التحكم</h1>

<div class="cards">
  <div class="card"><div class="num">{total}</div><div class="label">إجمالي المستخدمين</div></div>
  <div class="card"><div class="num">{active_users}</div><div class="label">مستخدمين نشطين</div></div>
  <div class="card"><div class="num">{total_products}</div><div class="label">منتجات تحت المراقبة</div></div>
  <div class="card"><div class="num">{'✅' if bot_active else '🔴'}</div><div class="label">حالة البوت</div></div>
</div>

<div class="section">
  <h2>⚙️ إعدادات البوت</h2>
  <div class="toggle">
    <span>حالة البوت</span>
    <span class="badge {'on' if bot_active else 'off'}">{'شغال' if bot_active else 'متوقف'}</span>
  </div>
  <div class="toggle">
    <span>🔍 خدمة المقارنة</span>
    <span class="badge {'on' if features.get('compare', True) else 'off'}">{'مفعلة' if features.get('compare', True) else 'معطلة'}</span>
  </div>
  <div class="toggle">
    <span>📌 خدمة التتبع</span>
    <span class="badge {'on' if features.get('track', True) else 'off'}">{'مفعلة' if features.get('track', True) else 'معطلة'}</span>
  </div>
  <br>
  <small style="color:#94a3b8">للتحكم في الإعدادات استخدم أوامر الأدمن في تيليجرام 👇<br>
  /admin — /stop_bot — /start_bot — /disable_compare — /enable_compare — /disable_track — /enable_track</small>
</div>

<div class="section">
  <h2>👥 المستخدمين</h2>
  <table>
    <tr><th>الاسم</th><th>Chat ID</th><th>المنتجات</th><th>الحالة</th><th>تاريخ الانضمام</th></tr>
    {users_rows if users_rows else '<tr><td colspan="5" style="text-align:center;color:#94a3b8">لا يوجد مستخدمين بعد</td></tr>'}
  </table>
</div>

<p style="text-align:center;color:#475569;font-size:0.85em">آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), AdminHandler)
    print(f"🌐 لوحة التحكم على البورت {port}")
    server.serve_forever()

# ============ الحلقة الرئيسية ============
def main():
    print("🤖 البوت شغال!")

    # تشغيل لوحة الويب في thread منفصل
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    data = load_data()
    send_message(ADMIN_CHAT_ID, "🚀 البوت اتشغل!\n\nابعت /admin لتشوف لوحة التحكم.")

    offset = None
    last_check = time.time()

    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))
            first_name = msg.get("chat", {}).get("first_name", "مستخدم")

            if not text or not chat_id:
                continue

            data = load_data()  # تحميل أحدث بيانات

            if chat_id == ADMIN_CHAT_ID and text.startswith("/") and text not in ["/start", "/list", "/clear"]:
                data = handle_admin(text, data)
            else:
                data = handle_user_message(chat_id, text, first_name, data)

            save_data(data)

        if time.time() - last_check >= CHECK_INTERVAL:
            data = load_data()
            if data.get("settings", {}).get("bot_active", True):
                print(f"[{datetime.now().strftime('%H:%M')}] بفحص الأسعار...")
                data = check_all_prices(data)
            last_check = time.time()

        time.sleep(2)

if __name__ == "__main__":
    main()
