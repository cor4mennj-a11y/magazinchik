import os
import json
import sqlite3
import uuid

import requests

from functools import wraps
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    flash
)
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)
from werkzeug.utils import secure_filename


# ==================================================
# НАСТРОЙКИ
# ==================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE = os.path.join(BASE_DIR, "shop.db")

UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "static",
    "uploads"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "temporary-secret-key"
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1417232861"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# ==================================================
# БАЗА SQLITE
# ==================================================

def get_db():
    conn = sqlite3.connect(
        DATABASE,
        timeout=20
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            address TEXT,
            items TEXT,
            total REAL DEFAULT 0,
            status TEXT DEFAULT 'Новый',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price REAL NOT NULL DEFAULT 0,
            image TEXT DEFAULT '',
            emoji TEXT DEFAULT '🌸',
            available INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Начальные товары добавляем, только если каталог пуст.
    product_count = conn.execute(
        "SELECT COUNT(*) AS count FROM products"
    ).fetchone()["count"]

    if product_count == 0:
        conn.executemany("""
            INSERT INTO products
                (name, description, price, image, emoji, available)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (
                "Красные розы",
                "Красивый букет из свежих красных роз.",
                3500,
                "/static/image/rose.png.jpg",
                "🌹",
                1
            ),
            (
                "Весенний букет",
                "Нежный букет из весенних цветов.",
                2500,
                "",
                "🌷",
                1
            ),
            (
                "Нежный букет",
                "Идеальный подарок для любимого человека.",
                3000,
                "",
                "🌸",
                1
            ),
            (
                "Большой букет",
                "Роскошный букет для особого случая.",
                5000,
                "",
                "💐",
                1
            )
        ])

    # Первый администратор берётся из Environment Render.
    admin_username = os.environ.get("ADMIN_USERNAME")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if admin_username and admin_password:
        existing = conn.execute(
            "SELECT id FROM admins WHERE username = ?",
            (admin_username,)
        ).fetchone()

        if not existing:
            conn.execute("""
                INSERT INTO admins (username, password_hash)
                VALUES (?, ?)
            """, (
                admin_username,
                generate_password_hash(admin_password)
            ))

    conn.commit()
    conn.close()


# ==================================================
# ФОТОГРАФИИ
# ==================================================

def allowed_file(filename):
    if not filename or "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def save_uploaded_file(file):
    if not file or not file.filename:
        return ""

    if not allowed_file(file.filename):
        return ""

    original_name = secure_filename(file.filename)

    if not original_name or "." not in original_name:
        return ""

    extension = original_name.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"

    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    return f"/static/uploads/{filename}"


def delete_uploaded_file(image_path):
    if not image_path:
        return

    # Не удаляем фотографии, которые находятся вне uploads.
    if not image_path.startswith("/static/uploads/"):
        return

    filename = os.path.basename(image_path)
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
    except OSError as error:
        print("Не удалось удалить фотографию:", error)


# ==================================================
# ШАБЛОНЫ
# ==================================================

@app.template_filter("from_json")
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


# ==================================================
# ЗАЩИТА АДМИНКИ
# ==================================================

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("admin_login"))

        return view(*args, **kwargs)

    return wrapped_view


# ==================================================
# ГЛАВНАЯ СТРАНИЦА
# ==================================================

@app.route("/")
def home():
    conn = get_db()

    try:
        products = conn.execute("""
            SELECT *
            FROM products
            WHERE available = 1
            ORDER BY id
        """).fetchall()
    finally:
        conn.close()

    return render_template("index.html", products=products)


# ==================================================
# ОФОРМЛЕНИЕ ЗАКАЗА
# ==================================================

@app.route("/send-order", methods=["POST"])
def send_order():
    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "success": False,
                "error": "Данные заказа не получены"
            }), 400

        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        address = str(data.get("address", "")).strip()
        items = data.get("items", [])

        if not name or not phone or not address:
            return jsonify({
                "success": False,
                "error": "Заполните имя, телефон и адрес"
            }), 400

        if not isinstance(items, list) or not items:
            return jsonify({
                "success": False,
                "error": "Корзина пуста"
            }), 400

        try:
            total = float(data.get("total", 0))
            if total < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Некорректная сумма заказа"
            }), 400

        # Сначала сохраняем заказ.
        conn = get_db()

        try:
            cursor = conn.execute("""
                INSERT INTO orders
                    (name, phone, address, items, total, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name,
                phone,
                address,
                json.dumps(items, ensure_ascii=False),
                total,
                "Новый"
            ))

            order_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        # Формируем уведомление для Telegram.
        order_text = (
            f"🌸 НОВЫЙ ЗАКАЗ №{order_id}!\n\n"
            f"👤 Имя: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"📍 Адрес: {address}\n\n"
            "🛍 Товары:\n"
        )

        for item in items:
            if not isinstance(item, dict):
                continue

            order_text += (
                f"• {item.get('name', 'Товар')} — "
                f"{item.get('quantity', 0)} шт. × "
                f"{item.get('price', 0)} ₽\n"
            )

        order_text += f"\n💰 Итого: {total:g} ₽"

        # Если Telegram не настроен или недоступен,
        # сохранённый заказ всё равно остаётся в базе.
        if TELEGRAM_BOT_TOKEN:
            try:
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
                    "Telegram status:",
                    response.status_code
                )

                if not response.ok:
                    print("Telegram response:", response.text)

            except requests.RequestException as error:
                print("Ошибка Telegram:", error)

        return jsonify({"success": True})

    except Exception as error:
        print("Ошибка оформления заказа:", str(error))

        return jsonify({
            "success": False,
            "error": "Не удалось сохранить заказ. Попробуйте позже."
        }), 500


# ==================================================
# ВХОД И ВЫХОД АДМИНИСТРАТОРА
# ==================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()

        try:
            admin = conn.execute("""
                SELECT *
                FROM admins
                WHERE username = ?
            """, (username,)).fetchone()
        finally:
            conn.close()

        if admin and check_password_hash(
            admin["password_hash"],
            password
        ):
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]

            return redirect(url_for("admin_panel"))

        return render_template(
            "admin_login.html",
            error="Неверный логин или пароль"
        )

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ==================================================
# СТАТИСТИКА АДМИНКИ
# ==================================================

@app.route("/admin")
@admin_required
def admin_panel():
    conn = get_db()

    try:
        orders_count = conn.execute(
            "SELECT COUNT(*) AS count FROM orders"
        ).fetchone()["count"]

        products_count = conn.execute(
            "SELECT COUNT(*) AS count FROM products"
        ).fetchone()["count"]

        admins_count = conn.execute(
            "SELECT COUNT(*) AS count FROM admins"
        ).fetchone()["count"]

        new_orders_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE status = 'Новый'
        """).fetchone()["count"]

        today_orders = conn.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE date(created_at) = date('now')
        """).fetchone()["count"]

        today_revenue = conn.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE date(created_at) = date('now')
              AND COALESCE(status, '') <> 'Отменён'
        """).fetchone()["total"]

        month_orders = conn.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE strftime('%Y-%m', created_at)
                = strftime('%Y-%m', 'now')
        """).fetchone()["count"]

        month_revenue = conn.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE strftime('%Y-%m', created_at)
                = strftime('%Y-%m', 'now')
              AND COALESCE(status, '') <> 'Отменён'
        """).fetchone()["total"]

        revenue_result = conn.execute("""
            SELECT
                COALESCE(SUM(total), 0) AS total,
                COUNT(*) AS count
            FROM orders
            WHERE COALESCE(status, '') <> 'Отменён'
        """).fetchone()

        total_revenue = revenue_result["total"]

        average_check = (
            total_revenue / revenue_result["count"]
            if revenue_result["count"] else 0
        )

    finally:
        conn.close()

    return render_template(
        "admin.html",
        orders_count=orders_count,
        products_count=products_count,
        admins_count=admins_count,
        new_orders_count=new_orders_count,
        today_orders=today_orders,
        today_revenue=today_revenue,
        month_orders=month_orders,
        month_revenue=month_revenue,
        total_revenue=total_revenue,
        average_check=average_check
    )


# ==================================================
# ЗАКАЗЫ И ИХ СТАТУСЫ
# ==================================================

@app.route("/admin/orders")
@admin_required
def admin_orders():
    conn = get_db()

    try:
        orders = conn.execute("""
            SELECT *
            FROM orders
            ORDER BY id DESC
        """).fetchall()
    finally:
        conn.close()

    return render_template("admin_orders.html", orders=orders)


@app.route(
    "/admin/order/<int:order_id>/status",
    methods=["POST"]
)
@admin_required
def update_order_status(order_id):
    status = request.form.get("status", "Новый")

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

    try:
        conn.execute("""
            UPDATE orders
            SET status = ?
            WHERE id = ?
        """, (status, order_id))

        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("admin_orders"))


# ==================================================
# СПИСОК ТОВАРОВ
# ==================================================

@app.route("/admin/products")
@admin_required
def admin_products():
    conn = get_db()

    try:
        products = conn.execute("""
            SELECT *
            FROM products
            ORDER BY id
        """).fetchall()
    finally:
        conn.close()

    return render_template(
        "admin_products.html",
        products=products
    )


# ==================================================
# ДОБАВЛЕНИЕ ТОВАРА
# ==================================================

@app.route("/admin/products/add", methods=["POST"])
@admin_required
def add_product():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    emoji = request.form.get("emoji", "🌸").strip() or "🌸"

    try:
        price = float(
            request.form.get("price", "0").replace(",", ".")
        )

        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("Укажи корректную цену.", "error")
        return redirect(url_for("admin_products"))

    available = (
        1 if request.form.get("available") == "on" else 0
    )

    image = ""
    uploaded_file = request.files.get("image")

    if uploaded_file and uploaded_file.filename:
        image = save_uploaded_file(uploaded_file)

        if not image:
            flash(
                "Формат фото должен быть JPG, JPEG, PNG или WEBP.",
                "error"
            )

    if not name:
        flash("Укажи название товара.", "error")
        return redirect(url_for("admin_products"))

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO products
                (name, description, price, image, emoji, available)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            name,
            description,
            price,
            image,
            emoji,
            available
        ))

        conn.commit()
    finally:
        conn.close()

    flash("Товар успешно добавлен!", "success")
    return redirect(url_for("admin_products"))


# ==================================================
# РЕДАКТИРОВАНИЕ ТОВАРА
# ==================================================

@app.route(
    "/admin/products/<int:product_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def edit_product(product_id):
    conn = get_db()

    try:
        product = conn.execute("""
            SELECT *
            FROM products
            WHERE id = ?
        """, (product_id,)).fetchone()

        if not product:
            return redirect(url_for("admin_products"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            description = request.form.get(
                "description", ""
            ).strip()

            emoji = request.form.get("emoji", "🌸").strip() or "🌸"

            try:
                price = float(
                    request.form.get(
                        "price", "0"
                    ).replace(",", ".")
                )

                if price < 0:
                    raise ValueError
            except (TypeError, ValueError):
                flash("Укажи корректную цену.", "error")
                return redirect(
                    url_for(
                        "edit_product",
                        product_id=product_id
                    )
                )

            available = (
                1 if request.form.get("available") == "on" else 0
            )

            image = product["image"] or ""

            if request.form.get("remove_image") == "on":
                delete_uploaded_file(image)
                image = ""

            uploaded_file = request.files.get("image")

            if uploaded_file and uploaded_file.filename:
                new_image = save_uploaded_file(uploaded_file)

                if new_image:
                    delete_uploaded_file(image)
                    image = new_image
                else:
                    flash(
                        "Формат фото должен быть JPG, JPEG, PNG или WEBP.",
                        "error"
                    )

            if not name:
                flash("Укажи название товара.", "error")
                return redirect(
                    url_for(
                        "edit_product",
                        product_id=product_id
                    )
                )

            conn.execute("""
                UPDATE products
                SET
                    name = ?,
                    description = ?,
                    price = ?,
                    image = ?,
                    emoji = ?,
                    available = ?
                WHERE id = ?
            """, (
                name,
                description,
                price,
                image,
                emoji,
                available,
                product_id
            ))

            conn.commit()

            flash("Товар успешно изменён!", "success")
            return redirect(url_for("admin_products"))

    finally:
        conn.close()

    return render_template(
        "admin_edit_product.html",
        product=product
    )


# ==================================================
# УДАЛЕНИЕ ТОВАРА
# ==================================================

@app.route(
    "/admin/products/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):
    conn = get_db()

    try:
        product = conn.execute("""
            SELECT image
            FROM products
            WHERE id = ?
        """, (product_id,)).fetchone()

        if product:
            conn.execute("""
                DELETE FROM products
                WHERE id = ?
            """, (product_id,))

            conn.commit()
            delete_uploaded_file(product["image"])

    finally:
        conn.close()

    flash("Товар удалён.", "success")
    return redirect(url_for("admin_products"))


# ==================================================
# АДМИНИСТРАТОРЫ
# ==================================================

@app.route("/admin/admins")
@admin_required
def admin_admins():
    conn = get_db()

    try:
        admins = conn.execute("""
            SELECT id, username
            FROM admins
            ORDER BY id
        """).fetchall()
    finally:
        conn.close()

    return render_template("admin_admins.html", admins=admins)


@app.route("/admin/add-admin", methods=["POST"])
@admin_required
def add_admin():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Заполни логин и пароль.", "error")
        return redirect(url_for("admin_admins"))

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO admins (username, password_hash)
            VALUES (?, ?)
        """, (
            username,
            generate_password_hash(password)
        ))

        conn.commit()
        flash("Администратор добавлен.", "success")

    except sqlite3.IntegrityError:
        flash("Такой логин уже существует.", "error")

    finally:
        conn.close()

    return redirect(url_for("admin_admins"))


@app.route(
    "/admin/delete-admin/<int:admin_id>",
    methods=["POST"]
)
@admin_required
def delete_admin(admin_id):
    if admin_id == session.get("admin_id"):
        flash("Нельзя удалить текущий аккаунт.", "error")
        return redirect(url_for("admin_admins"))

    conn = get_db()

    try:
        conn.execute("""
            DELETE FROM admins
            WHERE id = ?
        """, (admin_id,))

        conn.commit()
    finally:
        conn.close()

    flash("Администратор удалён.", "success")
    return redirect(url_for("admin_admins"))


# ==================================================
# СЛИШКОМ БОЛЬШАЯ ФОТОГРАФИЯ
# ==================================================

@app.errorhandler(413)
def too_large(error):
    flash(
        "Фотография слишком большая. Максимальный размер — 8 МБ.",
        "error"
    )
    return redirect(url_for("admin_products"))


# ==================================================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# ==================================================

init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
