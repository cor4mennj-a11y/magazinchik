from flask import Flask, render_template, request, jsonify
import os
import requests

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1417232861"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/send-order", methods=["POST"])
def send_order():
    try:
        data = request.get_json()

        name = data.get("name", "")
        phone = data.get("phone", "")
        address = data.get("address", "")
        items = data.get("items", [])
        total = data.get("total", 0)

        order_text = "🌸 НОВЫЙ ЗАКАЗ!\n\n"
        order_text += f"👤 Имя: {name}\n"
        order_text += f"📞 Телефон: {phone}\n"
        order_text += f"📍 Адрес: {address}\n\n"

        order_text += "🛍 Товары:\n"

        for item in items:
            order_text += (
                f"• {item['name']} — "
                f"{item['quantity']} шт. × "
                f"{item['price']} ₽\n"
            )

        order_text += f"\n💰 Итого: {total} ₽"

        # Проверяем, есть ли токен
        if not TELEGRAM_BOT_TOKEN:
            return jsonify({
                "success": False,
                "error": "TELEGRAM_BOT_TOKEN не найден на сервере"
            }), 500

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": order_text
            },
            timeout=10
        )

        print("TELEGRAM STATUS:", response.status_code)
        print("TELEGRAM RESPONSE:", response.text)

        if response.ok:
            return jsonify({
                "success": True
            })

        return jsonify({
            "success": False,
            "error": response.text
        }), 500

    except Exception as e:
        print("ERROR:", str(e))

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
