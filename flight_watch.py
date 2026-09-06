# -*- coding: utf-8 -*-
"""
Мониторинг цен на авиабилеты Москва -> Бангкок.
Источник цен: Travelpayouts Data API (кэш Aviasales, без скрейпинга сайта).

Как запустить:

    python flight_watch.py

Что делает:
    1. Спрашивает у API самые дешёвые билеты MOW -> BKK на каждый день
       выбранного диапазона (по умолчанию — 1-я неделя ноября 2026).
    2. Печатает таблицу «дата -> минимальная цена» в консоль.
    3. Если нашёлся билет дешевле порога THRESHOLD_RUB — шлёт уведомление
       в Telegram (если заданы TELEGRAM_TOKEN и TELEGRAM_CHAT_ID; если нет —
       просто отмечает находку в консоли).

Никаких сторонних библиотек — только стандартная библиотека Python.
"""
import os
import json
import urllib.parse
import urllib.request
import truststore
from datetime import date, timedelta

truststore.inject_into_ssl()

# ==========================================================================
#  НАСТРОЙКИ — меняй здесь
# ==========================================================================

TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
ORIGIN = "MOW"      # Москва (код города, охватывает все аэропорты)
DEST = "BKK"        # Бангкок (код города, охватывает Suvarnabhumi + Don Mueang)
CURRENCY = "rub"    # валюта цен

# Диапазон дат вылета — 1-я неделя ноября 2026
DATE_FROM = date(2026, 11, 1)
DATE_TO = date(2026, 11, 7)

# Обратный билет: None = только «туда» (one-way).
# Если нужен туда-обратно — впиши дату, напр. date(2026, 11, 14)
RETURN_DATE = None

DIRECT_ONLY = False  # True = только прямые рейсы (в Бангкок из Москвы прямых сейчас почти нет)

# Порог «дешёвого» билета в рублях — если цена ниже, шлём уведомление
THRESHOLD_RUB = 45000

# Telegram (пока пусто — заполним на следующем шаге)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = "145383436"

# ==========================================================================
#  Код ниже трогать не нужно
# ==========================================================================

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def fetch_cheapest_for_day(depart: date):
    """Запрашивает у API самый дешёвый билет на конкретную дату вылета.
    Возвращает dict с данными билета или None, если ничего не нашлось."""
    params = {
        "origin": ORIGIN,
        "destination": DEST,
        "departure_at": depart.isoformat(),   # YYYY-MM-DD
        "currency": CURRENCY,
        "sorting": "price",
        "direct": "true" if DIRECT_ONLY else "false",
        "limit": 30,
        "page": 1,
        "one_way": "false" if RETURN_DATE else "true",
        "token": TOKEN,
    }
    if RETURN_DATE:
        params["return_at"] = RETURN_DATE.isoformat()

    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Access-Token": TOKEN})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ошибка запроса на {depart}]: {e}")
        return None

    if not payload.get("success", False):
        print(f"  [API вернул ошибку на {depart}]: {payload}")
        return None

    data = payload.get("data") or []
    if not data:
        return None

    # data уже отсортирована по цене (sorting=price), берём первый
    cheapest = min(data, key=lambda t: t.get("price", 10**9))
    return cheapest


def format_ticket(t):
    """Красивая строчка про билет."""
    price = t.get("price")
    airline = t.get("airline", "?")
    transfers = t.get("transfers", "?")
    dep = t.get("departure_at", "")[:16].replace("T", " ")
    link_path = t.get("link", "")
    link = ("https://www.aviasales.ru" + link_path) if link_path else ""
    stops = "прямой" if transfers == 0 else f"{transfers} перес."
    line = f"{price} {CURRENCY.upper()} | {airline} | {stops} | вылет {dep}"
    return line, link


def send_telegram(text):
    """Отправляет сообщение в Telegram, если заданы креды."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"[не удалось отправить в Telegram]: {e}")
        return False


def main():
    print(f"Проверяю билеты {ORIGIN} -> {DEST} "
          f"({DATE_FROM} .. {DATE_TO}), валюта {CURRENCY.upper()}\n")

    results = []   # (дата, билет)
    day = DATE_FROM
    while day <= DATE_TO:
        t = fetch_cheapest_for_day(day)
        if t:
            line, link = format_ticket(t)
            print(f"{day}:  {line}")
            if link:
                print(f"            {link}")
            results.append((day, t))
        else:
            print(f"{day}:  нет данных")
        day += timedelta(days=1)

    if not results:
        print("\nНичего не нашлось. Возможно, кэш API пуст по этому маршруту "
              "на эти даты (данные копятся из поисков людей на Aviasales).")
        return

    # Самый дешёвый за весь диапазон
    best_day, best = min(results, key=lambda r: r[1].get("price", 10**9))
    best_line, best_link = format_ticket(best)
    print("\n" + "=" * 60)
    print(f"САМЫЙ ДЕШЁВЫЙ: {best_day}  ->  {best_line}")
    if best_link:
        print(best_link)
    print("=" * 60)

    # Уведомление, если ниже порога
    if best["price"] <= THRESHOLD_RUB:
        msg = (f"✈️ Дешёвый билет {ORIGIN}->{DEST}!\n"
               f"{best_day}: {best_line}\n{best_link}")
        if send_telegram(msg):
            print("\n📨 Отправлено уведомление в Telegram.")
        else:
            print(f"\n💡 Цена {best['price']} {CURRENCY.upper()} ниже порога "
                  f"{THRESHOLD_RUB} — но Telegram не настроен, уведомление не ушло.")
    else:
        print(f"\nВсё дороже порога {THRESHOLD_RUB} {CURRENCY.upper()} — "
              f"уведомление не шлём.")


if __name__ == "__main__":
    main()
