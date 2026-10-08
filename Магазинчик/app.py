```python
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import os
import requests
import sqlite3
import json
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1417232861"

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "temporary-secret-key"
)

DATABASE = "shop.db"


# =========================
# JSON-ФИЛЬТР ДЛЯ ШАБЛОНОВ
# =========================

@app.template_filter("from_json")
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


# =========================
# БАЗА ДАННЫХ
# =========================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    # Таблица администраторов
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    # Таблица заказов
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            address TEXT,
            items TEXT,
            total REAL,
            status TEXT DEFAULT 'Новый',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Первый администратор создаётся
    # из переменных Render
    admin_username = os.environ.get("ADMIN_USERNAME")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if admin_username and admin_password:

        existing_admin = conn.execute(
            """
            SELECT id
            FROM admins
            WHERE username = ?
            """,
            (admin_username,)
        ).fetchone()

        if not existing_admin:

            password_hash = generate_password_hash(
                admin_password
            )

            conn.execute(
                """
                INSERT INTO admins
                (username, password_hash)
                VALUES (?, ?)
                """,
                (
                    admin_username,
                    password_hash
                )
            )

    conn.commit()
    conn.close()


# =========================
# ПРОВЕРКА АВТОРИЗАЦИИ
# =========================

def admin_required(view):

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        if "admin_id" not in session:
            return redirect(
                url_for("admin_login")
            )

        return view(*args, **kwargs)

    return wrapped_view


# =========================
# ГЛАВНАЯ СТРАНИЦА
# =========================

@app.route("/")
def home():
    return render_template("index.html")


# =========================
# СОЗДАНИЕ ЗАКАЗА
# =========================

@app.route("/send-order", methods=["POST"])
def send_order():

    try:

        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "Данные заказа не получены"
            }), 400

        name = data.get("name", "")
        phone = data.get("phone", "")
        address = data.get("address", "")
        items = data.get("items", [])
        total = data.get("total", 0)

        # -------------------------
        # СОХРАНЯЕМ ЗАКАЗ В БАЗУ
        # -------------------------

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO orders
            (
                name,
                phone,
                address,
                items,
                total,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                phone,
                address,
                json.dumps(
                    items,
                    ensure_ascii=False
                ),
                total,
                "Новый"
            )
        )

        order_id = cursor.lastrowid

        conn.commit()
        conn.close()

        # -------------------------
        # TELEGRAM
        # -------------------------

        order_text = (
            f"🌸 НОВЫЙ ЗАКАЗ №{order_id}!\n\n"
        )

        order_text += (
            f"👤 Имя: {name}\n"
        )

        order_text += (
            f"📞 Телефон: {phone}\n"
        )

        order_text += (
            f"📍 Адрес: {address}\n\n"
        )

        order_text += "🛍 Товары:\n"

        for item in items:

            order_text += (
                f"• {item.get('name', '')} — "
                f"{item.get('quantity', 0)} шт. × "
                f"{item.get('price', 0)} ₽\n"
            )

        order_text += (
            f"\n💰 Итого: {total} ₽"
        )

        # -------------------------
        # ПРОВЕРЯЕМ TELEGRAM TOKEN
        # -------------------------

        if not TELEGRAM_BOT_TOKEN:

            return jsonify({
                "success": False,
                "error": (
                    "TELEGRAM_BOT_TOKEN "
                    "не найден на сервере"
                )
            }), 500

        telegram_url = (
            "https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            telegram_url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": order_text
            },
            timeout=10
        )

        print(
            "TELEGRAM STATUS:",
            response.status_code
        )

        print(
            "TELEGRAM RESPONSE:",
            response.text
        )

        if response.ok:

            return jsonify({
                "success": True
            })

        return jsonify({
            "success": False,
            "error": response.text
        }), 500

    except Exception as e:

        print(
            "ERROR:",
            str(e)
        )

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =========================
# ADMIN — ВХОД
# =========================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        admin = conn.execute(
            """
            SELECT *
            FROM admins
            WHERE username = ?
            """,
            (username,)
        ).fetchone()

        conn.close()

        if admin and check_password_hash(
            admin["password_hash"],
            password
        ):

            session["admin_id"] = admin["id"]

            session["admin_username"] = (
                admin["username"]
            )

            return redirect(
                url_for("admin_panel")
            )

        return render_template(
            "admin_login.html",
            error="Неверный логин или пароль"
        )

    return render_template(
        "admin_login.html"
    )


# =========================
# ADMIN — ВЫХОД
# =========================

@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin_login")
    )


# =========================
# ADMIN — ПАНЕЛЬ
# =========================

@app.route("/admin")
@admin_required
def admin_panel():

    conn = get_db()

    orders = conn.execute(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        """
    ).fetchall()

    admins = conn.execute(
        """
        SELECT id, username
        FROM admins
        ORDER BY id
        """
    ).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        orders=orders,
        admins=admins
    )


# =========================
# ADMIN — ИЗМЕНЕНИЕ СТАТУСА
# =========================

@app.route(
    "/admin/order/<int:order_id>/status",
    methods=["POST"]
)
@admin_required
def update_order_status(order_id):

    status = request.form.get(
        "status",
        "Новый"
    )

    allowed_statuses = [
        "Новый",
        "В обработке",
        "Готов",
        "Доставляется",
        "Выполнен",
        "Отменён"
    ]

    if status not in allowed_statuses:
        status = "Новый"

    conn = get_db()

    conn.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ?
        """,
        (
            status,
            order_id
        )
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================
# ADMIN — ДОБАВЛЕНИЕ
# АДМИНИСТРАТОРА
# =========================

@app.route(
    "/admin/add-admin",
    methods=["POST"]
)
@admin_required
def add_admin():

    username = request.form.get(
        "username",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    if not username or not password:

        return redirect(
            url_for("admin_panel")
        )

    password_hash = generate_password_hash(
        password
    )

    conn = get_db()

    try:

        conn.execute(
            """
            INSERT INTO admins
            (
                username,
                password_hash
            )
            VALUES (?, ?)
            """,
            (
                username,
                password_hash
            )
        )

        conn.commit()

    except sqlite3.IntegrityError:

        pass

    finally:

        conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================
# ADMIN — УДАЛЕНИЕ
# АДМИНИСТРАТОРА
# =========================

@app.route(
    "/admin/delete-admin/<int:admin_id>",
    methods=["POST"]
)
@admin_required
def delete_admin(admin_id):

    # Нельзя удалить самого себя
    if admin_id == session.get("admin_id"):

        return redirect(
            url_for("admin_panel")
        )

    conn = get_db()

    conn.execute(
        """
        DELETE FROM admins
        WHERE id = ?
        """,
        (admin_id,)
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# =========================

init_db()


# =========================
# ЗАПУСК
# =========================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
```
