import requests
import json
import time
import os
from datetime import datetime
from bs4 import BeautifulSoup

# ============ إعدادات البوت ============
TELEGRAM_TOKEN = "8877565774:AAE6Rw2qWqneA7Vf5-jsKqwMPSuO0brD8fg"
CHAT_ID = "6933040865"
DATA_FILE = "products.json"
CHECK_INTERVAL = 3600  # كل ساعة

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ============ تيليجرام ============
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"خطأ في إرسال الرسالة: {e}")

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30, "offset": offset}
    try:
        r = requests.get(url, params=params, timeout=35)
        return r.json()
    except:
        return {"result": []}

# ============ جلب سعر أمازون ============
def get_amazon_price(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # اسم المنتج
        title_el = soup.find("span", {"id": "productTitle"})
        title = title_el.get_text(strip=True)[:60] if title_el else "منتج غير معروف"

        # السعر - بنجرب أكثر من مكان
        price = None
        selectors = [
            {"class": "a-price-whole"},
            {"id": "priceblock_ourprice"},
            {"id": "priceblock_dealprice"},
            {"class": "a-offscreen"},
        ]
        for sel in selectors:
            el = soup.find("span", sel)
            if el:
                price_text = el.get_text(strip=True).replace(",", "").replace("ج.م", "").replace("EGP", "").strip()
                # شيل الفراغات والحروف
                price_clean = ""
                for ch in price_text:
                    if ch.isdigit() or ch == ".":
                        price_clean += ch
                if price_clean:
                    price = float(price_clean)
                    break

        return title, price
    except Exception as e:
        print(f"خطأ في جلب السعر: {e}")
        return None, None

# ============ حفظ وتحميل المنتجات ============
def load_products():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_products(products):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

# ============ معالجة الأوامر ============
def handle_message(text, products):
    text = text.strip()

    if text == "/start":
        send_message(
            "👋 أهلاً يا عبدالرحمن!\n\n"
            "أنا بوت تتبع أسعار أمازون مصر 🇪🇬\n\n"
            "الأوامر المتاحة:\n"
            "➕ <b>ابعتلي لينك المنتج + السعر المطلوب</b>\n"
            "مثال:\n<code>https://www.amazon.eg/dp/xxx 500</code>\n\n"
            "/list - شوف المنتجات اللي بتتابعها\n"
            "/clear - امسح كل المنتجات"
        )
        return products

    elif text == "/list":
        if not products:
            send_message("📭 مفيش منتجات بتتابعها دلوقتي.")
        else:
            msg = "📋 <b>المنتجات اللي بتتابعها:</b>\n\n"
            for i, (url, data) in enumerate(products.items(), 1):
                msg += (
                    f"{i}. {data['title']}\n"
                    f"   💰 السعر الحالي: {data.get('current_price', '؟')} ج.م\n"
                    f"   🎯 السعر المطلوب: {data['target_price']} ج.م\n\n"
                )
            send_message(msg)
        return products

    elif text == "/clear":
        products = {}
        save_products(products)
        send_message("🗑️ تم مسح كل المنتجات!")
        return products

    elif text.startswith("http"):
        parts = text.split()
        if len(parts) < 2:
            send_message("⚠️ ابعت اللينك والسعر المطلوب مع بعض.\nمثال:\n<code>https://www.amazon.eg/dp/xxx 500</code>")
            return products

        url = parts[0]
        try:
            target_price = float(parts[1])
        except:
            send_message("⚠️ السعر لازم يكون رقم. مثال: 500")
            return products

        send_message("⏳ بجيب معلومات المنتج...")
        title, current_price = get_amazon_price(url)

        if not title:
            send_message("❌ مقدرتش أجيب معلومات المنتج. تأكد من اللينك.")
            return products

        products[url] = {
            "title": title,
            "target_price": target_price,
            "current_price": current_price,
            "added_at": datetime.now().isoformat(),
        }
        save_products(products)

        price_msg = f"{current_price} ج.م" if current_price else "مش ظاهر دلوقتي"
        send_message(
            f"✅ تم إضافة المنتج!\n\n"
            f"📦 <b>{title}</b>\n"
            f"💰 السعر الحالي: {price_msg}\n"
            f"🎯 هبعتلك لما يوصل: {target_price} ج.م"
        )
        return products

    else:
        send_message("❓ مش فاهم. ابعت /start لتشوف الأوامر.")
        return products

# ============ فحص الأسعار ============
def check_prices(products):
    if not products:
        return products

    print(f"[{datetime.now().strftime('%H:%M')}] بفحص {len(products)} منتج...")
    changed = False

    for url, data in list(products.items()):
        title, current_price = get_amazon_price(url)
        if current_price is None:
            continue

        old_price = data.get("current_price")
        products[url]["current_price"] = current_price

        if current_price <= data["target_price"]:
            send_message(
                f"🔥 <b>السعر وصل!</b>\n\n"
                f"📦 {data['title']}\n"
                f"💰 السعر دلوقتي: <b>{current_price} ج.م</b>\n"
                f"🎯 السعر اللي طلبته: {data['target_price']} ج.م\n\n"
                f"🛒 اشتري دلوقتي:\n{url}"
            )
        elif old_price and current_price < old_price:
            send_message(
                f"📉 <b>السعر نزل!</b>\n\n"
                f"📦 {data['title']}\n"
                f"💰 من {old_price} → <b>{current_price} ج.م</b>\n"
                f"🎯 السعر المطلوب: {data['target_price']} ج.م\n\n"
                f"🔗 {url}"
            )

        changed = True
        time.sleep(2)  # عشان ما نضربش السيرفر

    if changed:
        save_products(products)

    return products

# ============ الحلقة الرئيسية ============
def main():
    print("🤖 البوت شغال!")
    send_message("🚀 البوت اتشغل! ابعت /start للبداية.")

    products = load_products()
    offset = None
    last_check = time.time()

    while True:
        # استقبال الرسائل
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "")
            if text:
                products = handle_message(text, products)

        # فحص الأسعار كل ساعة
        if time.time() - last_check >= CHECK_INTERVAL:
            products = check_prices(products)
            last_check = time.time()

        time.sleep(2)

if __name__ == "__main__":
    main()
