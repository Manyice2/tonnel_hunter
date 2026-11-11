"""
TONNEL HUNTER — Telegram gift monitor with a menu.
Версия 6.4: Автоматическая регистрация команд в меню Telegram.
"""

import os
import re
import json
import time
import asyncio
from collections import deque
from datetime import datetime

import requests
from tonnelmp import getGifts

try:
    import aportalsmp as portals
    PORTALS_AVAILABLE = True
except ImportError:
    PORTALS_AVAILABLE = False

# ---------------- НАСТРОЙКИ ----------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
PORTALS_AUTH = os.environ.get("PORTALS_AUTH", "").strip()

MIN_PRICE = 2.0
DEFAULT_MIN_DISCOUNT = 15.0
DEFAULT_MAX_PRICE = 20.0
DEFAULT_CHECK_EVERY = 60
MAX_CHECKS_PER_CYCLE = 10
BUYER_FEE = 1.10

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tonnel_settings.json")

COLOR_EMOJIS = {
    "Black": "⚫", "White": "⚪", "Red": "🔴", "Blue": "🔵",
    "Green": "🟢", "Yellow": "🟡", "Purple": "🟣", "Orange": "🟠",
    "Pink": "🌸", "Gray": "🔘"
}
AVAILABLE_COLORS = list(COLOR_EMOJIS.keys())

REFERENCE_CACHE = {} 
LIQUIDITY_CACHE = {} 
PORTALS_CACHE = {}
CACHE_TTL = 300  

COLOR_FAMILIES = {
    "dark": ["black", "dark", "noir", "midnight", "charcoal", "shadow", "night", "ebony", "onyx", "obsidian", "navy"],
    "light": ["white", "light", "snow", "ivory", "pearl", "cream", "silver", "frost", "pale", "ghost"],
    "red": ["red", "crimson", "blood", "scarlet", "ruby", "cherry", "maroon", "burgundy", "wine", "rose", "coral"],
    "blue": ["blue", "azure", "sky", "ocean", "sapphire", "cobalt", "cyan", "teal", "turquoise", "indigo"],
    "green": ["green", "emerald", "lime", "mint", "olive", "jade", "forest", "sage", "toxic"],
    "yellow": ["yellow", "gold", "golden", "amber", "lemon", "sun", "honey"],
    "purple": ["purple", "violet", "lilac", "lavender", "plum", "magenta", "fuchsia"],
    "orange": ["orange", "peach", "tangerine", "copper", "bronze", "rust"],
    "pink": ["pink", "blush", "flamingo", "bubblegum"],
    "gray": ["gray", "grey", "ash", "smoke", "steel", "chrome", "iron"]
}

# ---------------- ЗАГРУЗКА/СОХРАНЕНИЕ ----------------

def load_settings():
    default = {
        "max_price": DEFAULT_MAX_PRICE,
        "min_discount": DEFAULT_MIN_DISCOUNT,
        "check_liquid": True,
        "check_monochrome": False,
        "target_backdrop": None,
        "check_every": DEFAULT_CHECK_EVERY,
        "is_paused": False
    }
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return {**default, **data}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default

def save_settings():
    data = {
        "max_price": MAX_PRICE,
        "min_discount": MIN_DISCOUNT,
        "check_liquid": CHECK_LIQUID,
        "check_monochrome": CHECK_MONOCHROME,
        "target_backdrop": TARGET_BACKDROP,
        "check_every": CHECK_EVERY,
        "is_paused": IS_PAUSED
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError as exc:
        print("[Settings] Не удалось сохранить настройки:", exc)

_settings = load_settings()
MAX_PRICE = _settings.get("max_price")
MIN_DISCOUNT = _settings.get("min_discount", DEFAULT_MIN_DISCOUNT)
CHECK_LIQUID = _settings.get("check_liquid", True)
CHECK_MONOCHROME = _settings.get("check_monochrome", False)
TARGET_BACKDROP = _settings.get("target_backdrop")
CHECK_EVERY = _settings.get("check_every", DEFAULT_CHECK_EVERY)
IS_PAUSED = _settings.get("is_paused", False)

awaiting_max_price = False
awaiting_discount = False
awaiting_interval = False
awaiting_deadline = 0.0

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HTTP = requests.Session()

seen_listings = set()
recent_deals = deque(maxlen=20)
stats = {
    "started": datetime.now(),
    "received": 0,
    "checked": 0,
    "deals": 0,
    "errors": 0,
    "last_check": "пока не было",
}

# ---------------- TELEGRAM UI ----------------

def api_call(method, payload=None, timeout=20):
    response = HTTP.post(f"{API}/{method}", json=payload or {}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data.get("result")

def set_bot_commands():
    """Устанавливает меню команд в Telegram"""
    commands = [
        {"command": "menu", "description": "🏠 Главное меню"},
        {"command": "pause", "description": "⏸ Пауза"},
        {"command": "resume", "description": "▶️ Продолжить"},
        {"command": "stats", "description": "📊 Статистика"},
        {"command": "settings", "description": "⚙️ Настройки"}
    ]
    try:
        api_call("setMyCommands", {"commands": commands})
        print("[Telegram] Команды бота успешно обновлены.")
    except Exception as exc:
        print("[Telegram] Не удалось обновить команды:", exc)

def main_menu():
    if IS_PAUSED:
        pause_btn = {"text": "▶️ Запустить поиск", "callback_data": "toggle_pause"}
    else:
        pause_btn = {"text": "⏸ Пауза (Стоп)", "callback_data": "toggle_pause"}

    return {
        "inline_keyboard": [
            [{"text": "📊 Статистика", "callback_data": "stats"}, {"text": "⚙️ Настройки", "callback_data": "settings"}],
            [{"text": "🎯 Настроить подбор", "callback_data": "setup_filter"}, {"text": "🎁 Последние находки", "callback_data": "recent"}],
            [pause_btn],
            [{"text": "🔄 Обновить", "callback_data": "menu"}],
        ]
    }

def setup_filter_menu():
    liquid_btn_text = "💧 Ликвидность: ВКЛ" if CHECK_LIQUID else "💧 Ликвидность: ВЫКЛ"
    monochrome_btn_text = "🖤 Монохром: ВКЛ" if CHECK_MONOCHROME else "🖤 Монохром: ВЫКЛ"
    max_price_btn_text = f"💰 Макс. цена: {MAX_PRICE:g} TON" if MAX_PRICE is not None else "💰 Макс. цена: без лимита"
    discount_btn_text = f"📉 Мин. скидка: {MIN_DISCOUNT:g}%"
    interval_btn_text = f"⏱ Интервал: {CHECK_EVERY} сек"
    
    if TARGET_BACKDROP:
        emoji = COLOR_EMOJIS.get(TARGET_BACKDROP, "🎨")
        color_btn_text = f"{emoji} Цвет фона: {TARGET_BACKDROP}"
    else:
        color_btn_text = "🎨 Цвет фона: Любой"
        
    keyboard = [
        [{"text": liquid_btn_text, "callback_data": "toggle_liquid"}],
        [{"text": monochrome_btn_text, "callback_data": "toggle_monochrome"}],
        [{"text": color_btn_text, "callback_data": "choose_color"}],
        [{"text": discount_btn_text, "callback_data": "set_discount"}],
        [{"text": max_price_btn_text, "callback_data": "maxprice"}],
        [{"text": interval_btn_text, "callback_data": "set_interval"}],
        [{"text": "⬅️ Назад в меню", "callback_data": "menu"}],
    ]
    return {"inline_keyboard": keyboard}

def choose_color_menu():
    keyboard = []
    row = []
    reset_text = "❌ Любой фон" if TARGET_BACKDROP is None else "🔄 Сбросить на любой"
    keyboard.append([{"text": reset_text, "callback_data": "set_color_None"}])
    for color in AVAILABLE_COLORS:
        emoji = COLOR_EMOJIS.get(color, "🎨")
        is_active = "✅ " if TARGET_BACKDROP == color else ""
        row.append({"text": f"{is_active}{emoji} {color}", "callback_data": f"set_color_{color}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    keyboard.append([{"text": "⬅️ Назад к настройкам подбора", "callback_data": "setup_filter"}])
    return {"inline_keyboard": keyboard}

def send_message(text, keyboard=None, chat_id=None):
    target = str(chat_id or CHAT_ID)
    if not target: return False
    payload = {"chat_id": target, "text": text, "disable_web_page_preview": True}
    if keyboard: payload["reply_markup"] = keyboard
    try:
        api_call("sendMessage", payload)
        return True
    except Exception as exc:
        print("[Telegram] Ошибка отправки:", exc)
        return False

def edit_message(chat_id, message_id, text, keyboard=None):
    if not text or not str(text).strip(): return
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}
    if keyboard: payload["reply_markup"] = keyboard
    try:
        api_call("editMessageText", payload)
    except Exception as exc:
        err_msg = str(exc)
        if "400" not in err_msg:
            print("[Telegram] Ошибка редактирования:", exc)
            send_message(text, keyboard, chat_id)

def startup_text():
    max_price_text = f"{MAX_PRICE:g} TON" if MAX_PRICE is not None else "без ограничения"
    liquid_text = "Только ликвид" if CHECK_LIQUID else "Любые лоты"
    mono_text = "Только монохром" if CHECK_MONOCHROME else "Любой"
    if TARGET_BACKDROP:
        emoji = COLOR_EMOJIS.get(TARGET_BACKDROP, "🎨")
        color_text = f"{emoji} {TARGET_BACKDROP}"
    else:
        color_text = "Любой фон"
    
    portals_status = "✅ Вкл" if PORTALS_AVAILABLE and PORTALS_AUTH else "❌ Выкл (нет токена)"
    status_text = "⏸ ПАУЗА" if IS_PAUSED else "🟢 МОНИТОРИНГ АКТИВЕН"
    
    return (
        "💎 TONNEL HUNTER 6.4\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}\n\n"
        "🔎 Текущие фильтры:\n"
        f"• Ликвидность: {liquid_text}\n"
        f"• Монохром: {mono_text}\n"
        f"• Цвет фона: {color_text}\n"
        f"• Скидка: от {MIN_DISCOUNT:g}%\n"
        f"• Цена: {MIN_PRICE:g} - {max_price_text}\n"
        f"• Интервал: {CHECK_EVERY} сек.\n"
        f"• Portals: {portals_status}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Выбери нужный раздел в меню 👇"
    )

def settings_text():
    max_price_text = f"{MAX_PRICE:g} TON" if MAX_PRICE is not None else "без ограничения"
    liquid_text = "Вкл (только ликвид)" if CHECK_LIQUID else "Выкл (ищет всё)"
    mono_text = "Вкл (ищет оттенки)" if CHECK_MONOCHROME else "Выкл"
    if TARGET_BACKDROP:
        emoji = COLOR_EMOJIS.get(TARGET_BACKDROP, "🎨")
        color_text = f"{emoji} {TARGET_BACKDROP}"
    else:
        color_text = "Любой (без фильтра)"
    
    return (
        "⚙️ ОБЩИЕ НАСТРОЙКИ\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Максимальная цена: {max_price_text}\n"
        f"📉 Мин. скидка: {MIN_DISCOUNT:g}%\n"
        f"⏱ Интервал проверки: {CHECK_EVERY} сек.\n"
        "🧾 Комиссия в расчёте: 10%\n\n"
        "🔎 ФИЛЬТРЫ ПОДБОРА\n"
        f"💧 Ликвидность: {liquid_text}\n"
        f"🖤 Монохром: {mono_text}\n"
        f"🎨 Цвет фона: {color_text}\n\n"
        "Все эти параметры можно изменить в разделе «Настроить подбор»."
    )

def setup_filter_text():
    liquid_text = "✅ Вкл" if CHECK_LIQUID else "❌ Выкл"
    mono_text = "✅ Вкл" if CHECK_MONOCHROME else "❌ Выкл"
    if TARGET_BACKDROP:
        emoji = COLOR_EMOJIS.get(TARGET_BACKDROP, "🎨")
        color_text = f"{emoji} {TARGET_BACKDROP}"
    else:
        color_text = "Любой"
    max_price_text = f"{MAX_PRICE:g} TON" if MAX_PRICE is not None else "Без лимита"
    
    return (
        "🎯 НАСТРОЙКА ПОДБОРА\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💧 Искать ликвидные лоты: {liquid_text}\n"
        f"🖤 Искать монохром (похожие оттенки): {mono_text}\n"
        f"🎨 Искать конкретный фон: {color_text}\n"
        f"📉 Минимальная скидка: {MIN_DISCOUNT:g}%\n"
        f"💰 Максимальная цена: {max_price_text}\n"
        f"⏱ Интервал проверки: {CHECK_EVERY} сек.\n\n"
        "Нажми на нужный параметр, чтобы изменить:"
    )

def stats_text():
    started = stats["started"].strftime("%d.%m.%Y %H:%M")
    return (
        "📊 СТАТИСТИКА\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Запущен: {started}\n"
        f"📦 Получено лотов: {stats['received']}\n"
        f"🔍 Проверено: {stats['checked']}\n"
        f"💎 Найдено: {stats['deals']}\n"
        f"⚠️ Ошибок: {stats['errors']}\n"
        f"🕒 Последняя проверка: {stats['last_check']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

def recent_text():
    if not recent_deals:
        return "🎁 ПОСЛЕДНИЕ НАХОДКИ\n━━━━━━━━━━━━━━━━━━\nПока подходящих лотов нет."
    lines = ["🎁 ПОСЛЕДНИЕ НАХОДКИ", "━━━━━━━━━━━━━━━━━━"]
    for item in list(recent_deals)[:10]:
        lines.append(f"• {item['name']} #{item['num']} — {item['price']:g} TON")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def handle_updates(offset):
    global MAX_PRICE, MIN_DISCOUNT, CHECK_LIQUID, CHECK_MONOCHROME, TARGET_BACKDROP, CHECK_EVERY, IS_PAUSED
    global awaiting_max_price, awaiting_discount, awaiting_interval, awaiting_deadline
    try:
        updates = api_call("getUpdates", {"offset": offset, "timeout": 1, "allowed_updates": ["message", "callback_query"]}, timeout=5)
    except Exception as exc:
        print("[Telegram] Ошибка получения:", exc)
        return offset

    for update in updates or []:
        offset = max(offset, update["update_id"] + 1)

        if "message" in update:
            message = update["message"]
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "").strip().lower()
            if chat_id != CHAT_ID: continue

            if awaiting_max_price:
                if time.monotonic() > awaiting_deadline:
                    awaiting_max_price = False
                    send_message("Время ожидания вышло. Попробуй снова.", main_menu(), chat_id)
                else:
                    if text in ("off", "выкл", "без лимита", "нет"):
                        MAX_PRICE = None; save_settings(); awaiting_max_price = False
                        send_message("✅ Ограничение цены отключено.", setup_filter_menu(), chat_id)
                        send_message(setup_filter_text(), setup_filter_menu(), chat_id)
                    else:
                        try:
                            new_price = float(text.replace(",", "."))
                            if new_price <= 0: raise ValueError
                            MAX_PRICE = new_price; save_settings(); awaiting_max_price = False
                            send_message(f"✅ Макс. цена: {MAX_PRICE:g} TON.", setup_filter_menu(), chat_id)
                            send_message(setup_filter_text(), setup_filter_menu(), chat_id)
                        except ValueError:
                            send_message("Введи число, например 25, или «без лимита».", setup_filter_menu(), chat_id)
                    continue

            if awaiting_discount:
                if time.monotonic() > awaiting_deadline:
                    awaiting_discount = False
                    send_message("Время ожидания вышло. Попробуй снова.", main_menu(), chat_id)
                else:
                    try:
                        new_discount = float(text.replace(",", "."))
                        if new_discount < 0 or new_discount > 100: raise ValueError
                        MIN_DISCOUNT = new_discount; save_settings(); awaiting_discount = False
                        send_message(f"✅ Мин. скидка: {MIN_DISCOUNT:g}%.", setup_filter_menu(), chat_id)
                        send_message(setup_filter_text(), setup_filter_menu(), chat_id)
                    except ValueError:
                        send_message("Введи число от 0 до 100, например 15.", setup_filter_menu(), chat_id)
                continue

            if awaiting_interval:
                if time.monotonic() > awaiting_deadline:
                    awaiting_interval = False
                    send_message("Время ожидания вышло. Попробуй снова.", main_menu(), chat_id)
                else:
                    try:
                        new_interval = int(text.replace(",", "."))
                        if new_interval < 10 or new_interval > 3600: raise ValueError
                        CHECK_EVERY = new_interval; save_settings(); awaiting_interval = False
                        send_message(f"✅ Интервал проверки: {CHECK_EVERY} сек.", setup_filter_menu(), chat_id)
                        send_message(setup_filter_text(), setup_filter_menu(), chat_id)
                    except ValueError:
                        send_message("Введи целое число от 10 до 3600 (в секундах), например 60.", setup_filter_menu(), chat_id)
                continue

            if text in ("/start", "/menu", "меню"):
                send_message(startup_text(), main_menu(), chat_id)
            elif text == "/settings":
                send_message(settings_text(), main_menu(), chat_id)
            elif text in ("/pause", "пауза"):
                IS_PAUSED = True; save_settings()
                send_message("⏸ Поиск приостановлен. Чтобы продолжить, напиши /resume или нажми кнопку в меню.", main_menu(), chat_id)
            elif text in ("/resume", "старт", "продолжить"):
                IS_PAUSED = False; save_settings()
                send_message("▶️ Поиск возобновлен!", main_menu(), chat_id)

        elif "callback_query" in update:
            query = update["callback_query"]
            chat_id = str(query.get("message", {}).get("chat", {}).get("id", ""))
            message_id = query.get("message", {}).get("message_id")
            data = query.get("data", "")
            try: api_call("answerCallbackQuery", {"callback_query_id": query["id"]})
            except: pass
            if chat_id != CHAT_ID or message_id is None: continue

            if data == "menu": edit_message(chat_id, message_id, startup_text(), main_menu())
            elif data == "stats": edit_message(chat_id, message_id, stats_text(), main_menu())
            elif data == "settings": edit_message(chat_id, message_id, settings_text(), main_menu())
            elif data == "recent": edit_message(chat_id, message_id, recent_text(), main_menu())
            elif data == "setup_filter": edit_message(chat_id, message_id, setup_filter_text(), setup_filter_menu())
            elif data == "toggle_pause":
                IS_PAUSED = not IS_PAUSED
                save_settings()
                edit_message(chat_id, message_id, startup_text(), main_menu())
            elif data == "toggle_liquid":
                CHECK_LIQUID = not CHECK_LIQUID; save_settings(); edit_message(chat_id, message_id, setup_filter_text(), setup_filter_menu())
            elif data == "toggle_monochrome":
                CHECK_MONOCHROME = not CHECK_MONOCHROME; save_settings(); edit_message(chat_id, message_id, setup_filter_text(), setup_filter_menu())
            elif data == "choose_color": edit_message(chat_id, message_id, "🎨 Выбери цвет фона для поиска:", choose_color_menu())
            elif data.startswith("set_color_"):
                color_val = data.replace("set_color_", "")
                TARGET_BACKDROP = None if color_val == "None" else color_val
                save_settings(); edit_message(chat_id, message_id, setup_filter_text(), setup_filter_menu())
            elif data == "set_discount":
                awaiting_discount = True; awaiting_deadline = time.monotonic() + 120
                edit_message(chat_id, message_id, "📉 Введи новую минимальную скидку в процентах (например 20).\nЭто число от 0 до 100.", {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "setup_filter"}]]})
            elif data == "set_interval":
                awaiting_interval = True; awaiting_deadline = time.monotonic() + 120
                edit_message(chat_id, message_id, "⏱ Введи новый интервал проверки в секундах (например 60).\nМинимум 10, максимум 3600.", {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "setup_filter"}]]})
            elif data == "maxprice":
                awaiting_max_price = True; awaiting_deadline = time.monotonic() + 120
                edit_message(chat_id, message_id, "🔝 Введи новую максимальную цену в TON (например 25).\nЧтобы отключить лимит, напиши «без лимита».", {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "setup_filter"}]]})
    return offset


# ---------------- ЛОГИКА ФИЛЬТРАЦИИ ----------------

def is_candidate(gift):
    price = gift.get("price")
    return (
        gift.get("status") == "forsale"
        and not gift.get("auction")
        and not gift.get("dutchAuctionData")
        and not gift.get("premarketData")
        and isinstance(price, (int, float))
        and not isinstance(price, bool)
    )

def gift_link(gift):
    slug = re.sub(r"[^A-Za-z0-9]", "", str(gift.get("name", "")))
    if not slug: slug = str(gift.get("gift_id", "unknown"))
    return f"https://t.me/nft/{slug}-{gift.get('gift_num', '')}"

def tonnel_link(gift):
    gift_id = gift.get("gift_id")
    return f"https://t.me/tonnel_network_bot/gift?startapp={gift_id}" if gift_id else ""

def get_color_family(text):
    text_lower = text.lower()
    matched_families = set()
    if "monochrome" in text_lower: return "monochrome"
    for family, keywords in COLOR_FAMILIES.items():
        for kw in keywords:
            if kw in text_lower:
                matched_families.add(family)
                break
    if len(matched_families) == 1: return list(matched_families)[0]
    return None

def is_monochrome(model_str, backdrop_str):
    model_lower = model_str.lower(); backdrop_lower = backdrop_str.lower()
    if "monochrome" in model_lower or "monochrome" in backdrop_lower: return True
    fam_model = get_color_family(model_str); fam_backdrop = get_color_family(backdrop_str)
    if fam_model and fam_backdrop and fam_model == fam_backdrop: return True
    for color in ["black", "white", "red", "blue", "green", "yellow", "purple", "orange", "pink", "gray"]:
        if color in model_lower and color in backdrop_lower: return True
    return False

def is_liquid_and_rare(gift):
    name = str(gift.get("name", "")); model = str(gift.get("model", "")); backdrop = str(gift.get("backdrop", ""))
    
    if CHECK_MONOCHROME and not is_monochrome(model, backdrop):
        return False, "не монохром (цвета не совпадают)"

    if TARGET_BACKDROP is not None and TARGET_BACKDROP.lower() not in backdrop.lower():
        return False, f"фон не совпадает ({TARGET_BACKDROP})"

    if CHECK_LIQUID:
        cache_key = (name, model, backdrop)
        now = time.time()
        if cache_key in LIQUIDITY_CACHE:
            cached = LIQUIDITY_CACHE[cache_key]
            if now - cached["ts"] < CACHE_TTL:
                if not cached["is_liquid"]: return False, f"неликвид (всего {cached['count']} лотов)"
                return True, "ок"
        try:
            similar_lots = getGifts(gift_name=name, model=model, backdrop=backdrop, sort="price_asc", limit=10)
            count = len(similar_lots) if similar_lots else 0
            is_liq = count >= 2
            LIQUIDITY_CACHE[cache_key] = {"is_liquid": is_liq, "count": count, "ts": now}
            if not is_liq: return False, f"неликвид (всего {count} лотов)"
        except Exception as e:
            print(f"[Liquidity] Ошибка проверки: {e}")
            pass 
    return True, "ок"

def get_reference_price(gift):
    cache_key = (gift.get("name"), gift.get("model"), gift.get("backdrop"), gift.get("symbol"))
    now = time.time()
    if cache_key in REFERENCE_CACHE:
        cached = REFERENCE_CACHE[cache_key]
        if now - cached["ts"] < CACHE_TTL: return cached["price"]

    valid_prices = []
    try:
        others = getGifts(gift_name=gift.get("name"), model=gift.get("model"), backdrop=gift.get("backdrop"), symbol=gift.get("symbol"), limit=10, sort="price_asc")
        valid_prices = [o.get("price") for o in (others or []) if o.get("gift_id") != gift.get("gift_id") and is_candidate(o)]
    except Exception: pass
    if not valid_prices:
        try:
            others = getGifts(gift_name=gift.get("name"), model=gift.get("model"), backdrop=gift.get("backdrop"), limit=10, sort="price_asc")
            valid_prices = [o.get("price") for o in (others or []) if o.get("gift_id") != gift.get("gift_id") and is_candidate(o)]
        except Exception: pass
    if not valid_prices:
        try:
            others = getGifts(gift_name=gift.get("name"), model=gift.get("model"), limit=10, sort="price_asc")
            valid_prices = [o.get("price") for o in (others or []) if o.get("gift_id") != gift.get("gift_id") and is_candidate(o)]
        except Exception: pass
    if not valid_prices:
        try:
            others = getGifts(gift_name=gift.get("name"), limit=10, sort="price_asc")
            valid_prices = [o.get("price") for o in (others or []) if o.get("gift_id") != gift.get("gift_id") and is_candidate(o)]
        except Exception: pass
    if not valid_prices: return None
    ref_price = min(valid_prices)
    REFERENCE_CACHE[cache_key] = {"price": ref_price, "ts": now}
    return ref_price

def get_portals_price(gift_name, model, backdrop):
    if not PORTALS_AVAILABLE or not PORTALS_AUTH: return None
    
    cache_key = f"{gift_name}_{model}_{backdrop}"
    now = time.time()
    if cache_key in PORTALS_CACHE:
        cached = PORTALS_CACHE[cache_key]
        if now - cached["ts"] < 120: return cached["price"]

    try:
        async def _fetch():
            listings = await portals.search(gift_name=gift_name, model=model, backdrop=backdrop, sort="price_asc", limit=5, authData=PORTALS_AUTH)
            if listings:
                return min(float(g.price) for g in listings)
            return None
        
        price = asyncio.run(_fetch())
        if price:
            PORTALS_CACHE[cache_key] = {"price": price, "ts": now}
        return price
    except Exception as e:
        print(f"[Portals] Ошибка получения цены: {e}")
        return None

def analyze(gift):
    if not is_candidate(gift): return None, "не обычный лот в продаже"
    price = gift["price"]
    if price < MIN_PRICE: return None, f"цена ниже {MIN_PRICE:g} TON"
    if MAX_PRICE is not None and price > MAX_PRICE: return None, "цена выше максимума"

    liquid_ok, liquid_reason = is_liquid_and_rare(gift)
    if not liquid_ok: return None, liquid_reason

    tonnel_ref_price = get_reference_price(gift)
    if not tonnel_ref_price or tonnel_ref_price <= 0: return None, "не найден эталон на Tonnel"

    portals_price = get_portals_price(gift.get("name"), gift.get("model"), gift.get("backdrop"))

    total_price = price * BUYER_FEE
    discount = (tonnel_ref_price - price) / tonnel_ref_price * 100
    
    best_sell_price = tonnel_ref_price
    best_sell_platform = "Tonnel"
    
    if portals_price and portals_price > best_sell_price:
        best_sell_price = portals_price
        best_sell_platform = "Portals"
        
    profit = best_sell_price - total_price

    if discount < MIN_DISCOUNT and profit <= 0:
        return None, f"нет выгоды (скидка {discount:.1f}%)"
    if profit <= 0:
        return None, f"нет чистой прибыли после комиссии"

    name = str(gift.get("name", "Подарок"))
    number = gift.get("gift_num", "?")
    model = gift.get("model", "?")
    backdrop = gift.get("backdrop", "?")
    symbol = gift.get("symbol", "?")

    text = (
        "💎 TONNEL HUNTER 6.4\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 {name} #{number}\n"
        f"🧩 Модель: {model}\n"
        f"🎨 Фон: {backdrop}\n"
        f"🔹 Символ: {symbol}\n\n"
        "💰 ЦЕНА И ВЫГОДА\n"
        f"• Цена лота: {price:g} TON\n"
        f"• С комиссией: {total_price:.2f} TON\n"
        f"• Флор на Tonnel: {tonnel_ref_price:g} TON\n"
    )
    
    if portals_price:
        text += f"• Флор на Portals: {portals_price:g} TON\n"
    else:
        text += "• Флор на Portals: нет данных\n"

    text += f"• Скидка: {discount:.1f}%\n"

    if best_sell_platform == "Portals" and portals_price:
        text += (
            f"\n🚀 АРБИТРАЖ!\n"
            f"🛒 Купить тут: {price:g} TON\n"
            f"💰 Продать на Portals: {portals_price:g} TON\n"
            f"📈 Профит: ~{profit:.2f} TON\n"
        )
    else:
        text += (
            f"• Чистая разница: {profit:.2f} TON\n"
        )

    text += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Проверь стакан вручную перед покупкой!"
    )

    buttons = []
    marketplace_url = tonnel_link(gift)
    if marketplace_url:
        buttons.append({"text": "🛒 Купить на Tonnel", "url": marketplace_url})
        
    search_query = name.replace(" ", "%20")
    if model:
        search_query += f"%20{model.replace(' ', '%20')}"
    
    portals_web_url = f"https://portals-market.com/?search={search_query}"
    buttons.append({"text": "🔍 Открыть на Portals", "url": portals_web_url})

    buttons.append({"text": "🎁 Подарок в TG", "url": gift_link(gift)})

    return (text, {"inline_keyboard": [buttons]}, {"name": name, "num": number, "price": price, "discount": discount}), "подходит"


# ---------------- MAIN ----------------

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Не заданы BOT_TOKEN или CHAT_ID.")

    # ВОТ ЭТА СТРОКА ДОБАВЛЯЕТ КОМАНДЫ В МЕНЮ TELEGRAM
    set_bot_commands()

    portals_status = "ВКЛ" if PORTALS_AVAILABLE and PORTALS_AUTH else "ВЫКЛ"
    print(f"TONNEL HUNTER 6.4 запущен. Portals: {portals_status}.")
    send_message(startup_text(), main_menu())

    global seen_listings
    pending = deque()
    offset = 0
    next_scan = 0.0

    while True:
        offset = handle_updates(offset)

        if IS_PAUSED:
            time.sleep(1)
            continue

        if time.monotonic() >= next_scan:
            try:
                latest = getGifts(limit=30, sort="latest") or []
                stats["received"] += len(latest)
                stats["last_check"] = datetime.now().strftime("%H:%M:%S")

                added = 0
                for gift in latest:
                    gift_id = gift.get("gift_id")
                    price = gift.get("price")
                    key = (gift_id, price)
                    if gift_id is None or key in seen_listings:
                        continue
                    seen_listings.add(key)
                    pending.append(gift)
                    added += 1

                checked = 0
                while pending and checked < MAX_CHECKS_PER_CYCLE:
                    if IS_PAUSED:
                        break
                        
                    gift = pending.popleft()
                    checked += 1
                    stats["checked"] += 1
                    try:
                        result, reason = analyze(gift)
                        label = f"{gift.get('name', '?')} #{gift.get('gift_num', '?')}"
                        if result:
                            text, keyboard, deal_data = result
                            if send_message(text, keyboard):
                                stats["deals"] += 1
                                recent_deals.appendleft(deal_data)
                                print(f"[НАХОДКА] {label}")
                        else:
                            print(f"[Пропуск] {label}: {reason}")
                    except Exception as exc:
                        pending.appendleft(gift)
                        stats["errors"] += 1
                        break
                    time.sleep(1)

            except Exception as exc:
                stats["errors"] += 1
                print("[Ошибка Tonnel]:", repr(exc))

            next_scan = time.monotonic() + CHECK_EVERY

        time.sleep(1)

if __name__ == "__main__":
    main()
