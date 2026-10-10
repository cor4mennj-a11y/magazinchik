
import os
import json
import uuid
from functools import wraps
from decimal import Decimal, InvalidOperation

import psycopg
import requests

from psycopg.rows import dict_row
from psycopg.errors import UniqueViolation
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    flash,
    make_response,
)
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)
from werkzeug.utils import secure_filename


# =========================================================
# НАСТРОЙКИ
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get("DATABASE_URL")
SECRET_KEY = os.environ.get("SECRET_KEY")

app.secret_key = SECRET_KEY or os.urandom(32).hex()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1417232861"

UPLOAD_FOLDER = os.path.join(
    BASE_DIR, "static", "uploads"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# =========================================================
# ПОДКЛЮЧЕНИЕ SUPABASE / POSTGRESQL
# =========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "В Render не задана переменная DATABASE_URL"
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=15,
        sslmode="require",
    )


# =========================================================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# =========================================================

def init_db():
    conn = get_db()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    price NUMERIC(12, 2) NOT NULL DEFAULT 0,
                    image TEXT DEFAULT '',
                    emoji TEXT DEFAULT '🌸',
                    available INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    address TEXT,
                    items TEXT,
                    total NUMERIC(12, 2) DEFAULT 0,
                    status TEXT DEFAULT 'Новый',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()

        # Начальные товары добавляются только в пустой каталог.
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM products"
        ).fetchone()["count"]

        if count == 0:
            default_products = [
                (
                    "Красные розы",
                    "Красивый букет из свежих красных роз.",
                    3500,
                    "/static/image/rose.png.jpg",
                    "🌹",
                    1,
                ),
                (
                    "Весенний букет",
                    "Нежный букет из весенних цветов.",
                    2500,
                    "",
                    "🌷",
                    1,
                ),
                (
                    "Нежный букет",
                    "Идеальный подарок для любимого человека.",
                    3000,
                    "",
                    "🌸",
                    1,
                ),
                (
                    "Большой букет",
                    "Роскошный букет для особого случая.",
                    5000,
                    "",
                    "💐",
                    1,
                ),
            ]

            # В psycopg executemany вызывается у курсора.
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO products
                    (name, description, price, image, emoji, available)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, default_products)

            conn.commit()

        # Создаём администратора, если его ещё нет.
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")

        if username and password:
            existing = conn.execute(
                "SELECT id FROM admins WHERE username = %s",
                (username,),
            ).fetchone()

            if not existing:
                conn.execute("""
                    INSERT INTO admins (username, password_hash)
                    VALUES (%s, %s)
                """, (
                    username,
                    generate_password_hash(password),
                ))
                conn.commit()

        print("Supabase: таблицы проверены, база готова.")

    finally:
        conn.close()


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def allowed_file(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def save_uploaded_file(file):
    if not file or not file.filename:
        return ""

    if not allowed_file(file.filename):
        return ""

    safe_name = secure_filename(file.filename)

    if "." not in safe_name:
        return ""

    extension = safe_name.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"

    file.save(os.path.join(UPLOAD_FOLDER, filename))

    return f"/static/uploads/{filename}"


def delete_uploaded_file(image_path):
    if not image_path or not image_path.startswith(
        "/static/uploads/"
    ):
        return

    filename = os.path.basename(image_path)
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
    except OSError as exc:
        print("Ошибка удаления фото:", exc)


@app.template_filter("from_json")
def from_json(value):
    try:
        return json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped_view


def render_admin_error(message, endpoint="admin_products"):
    flash(message, "error")
    return redirect(url_for(endpoint))


# =========================================================
# ГЛАВНАЯ СТРАНИЦА
# =========================================================

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


@app.route("/robots.txt")
def robots_txt():
    response = make_response("User-agent: *\nAllow: /\n")
    response.mimetype = "text/plain"
    return response


# =========================================================
# СОХРАНЕНИЕ ЗАКАЗА
# =========================================================

@app.route("/send-order", methods=["POST"])
def send_order():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "Не удалось получить данные заказа",
        }), 400

    name = str(data.get("name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    address = str(data.get("address", "")).strip()
    items = data.get("items", [])

    if not name or not phone or not address:
        return jsonify({
            "success": False,
            "error": "Заполните имя, телефон и адрес",
        }), 400

    if not isinstance(items, list) or not items:
        return jsonify({
            "success": False,
            "error": "Корзина пуста",
        }), 400

    try:
        # Пересчитываем сумму по данным корзины.
        # Для настоящей оплаты цены необходимо проверять
        # по каталогу на сервере.
        total = Decimal("0.00")
        clean_items = []

        for item in items:
            if not isinstance(item, dict):
                continue

            try:
                quantity = int(item.get("quantity", 1))
                price = Decimal(str(item.get("price", 0)))
            except (ValueError, TypeError, InvalidOperation):
                continue

            if quantity < 1 or quantity > 100:
                continue

            if not price.is_finite() or price < 0:
                continue

            clean_items.append({
                "name": str(item.get("name", "Товар"))[:200],
                "quantity": quantity,
                "price": float(price),
            })
            total += price * quantity

        if not clean_items:
            return jsonify({
                "success": False,
                "error": "В корзине нет корректных товаров",
            }), 400

        conn = get_db()

        try:
            result = conn.execute("""
                INSERT INTO orders
                (name, phone, address, items, total, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                name,
                phone,
                address,
                json.dumps(clean_items, ensure_ascii=False),
                total,
                "Новый",
            )).fetchone()

            order_id = result["id"]
            conn.commit()

        finally:
            conn.close()

    except Exception as exc:
        print("Ошибка сохранения заказа:", repr(exc))
        return jsonify({
            "success": False,
            "error": "Не удалось сохранить заказ. Попробуйте позже.",
        }), 500

    # Уведомление в Telegram отправляется после сохранения заказа.
    order_text = (
        f"🌸 НОВЫЙ ЗАКАЗ №{order_id}!\n\n"
        f"👤 Имя: {name}\n"
        f"📞 Телефон: {phone}\n"
        f"📍 Адрес: {address}\n\n"
        "🛍 Товары:\n"
    )

    for item in clean_items:
        order_text += (
            f"• {item['name']} — {item['quantity']} шт. × "
            f"{item['price']:g} ₽\n"
        )

    order_text += f"\n💰 Итого: {total:g} ₽"

    telegram_sent = False

    if TELEGRAM_BOT_TOKEN:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": order_text,
                },
                timeout=10,
            )

            telegram_sent = response.ok

            if not response.ok:
                print("Telegram error:", response.text)

        except requests.RequestException as exc:
            print("Telegram connection error:", exc)

    return jsonify({
        "success": True,
        "order_id": order_id,
        "telegram_sent": telegram_sent,
        "message": "Заказ сохранён",
    })


# =========================================================
# ВХОД В АДМИНКУ
# =========================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            return render_template(
                "admin_login.html",
                error="Заполни логин и пароль.",
            )

        conn = get_db()

        try:
            admin = conn.execute("""
                SELECT *
                FROM admins
                WHERE username = %s
            """, (username,)).fetchone()
        finally:
            conn.close()

        if admin and check_password_hash(
            admin["password_hash"], password
        ):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            return redirect(url_for("admin_panel"))

        return render_template(
            "admin_login.html",
            error="Неверный логин или пароль",
        )

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# =========================================================
# ГЛАВНАЯ АДМИНКИ И СТАТИСТИКА
# =========================================================

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
            WHERE created_at::date = CURRENT_DATE
        """).fetchone()["count"]

        today_revenue = conn.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE created_at::date = CURRENT_DATE
              AND COALESCE(status, '') <> 'Отменён'
        """).fetchone()["total"]

        month_orders = conn.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE date_trunc('month', created_at)
                = date_trunc('month', CURRENT_TIMESTAMP)
        """).fetchone()["count"]

        month_revenue = conn.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE date_trunc('month', created_at)
                = date_trunc('month', CURRENT_TIMESTAMP)
              AND COALESCE(status, '') <> 'Отменён'
        """).fetchone()["total"]

        revenue = conn.execute("""
            SELECT
                COALESCE(SUM(total), 0) AS total,
                COUNT(*) AS count
            FROM orders
            WHERE COALESCE(status, '') <> 'Отменён'
        """).fetchone()

        total_revenue = revenue["total"]
        average_check = (
            total_revenue / revenue["count"]
            if revenue["count"] else 0
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
        average_check=average_check,
    )


# =========================================================
# ЗАКАЗЫ
# =========================================================

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
    methods=["POST"],
)
@admin_required
def update_order_status(order_id):
    allowed_statuses = [
        "Новый",
        "В обработке",
        "Готов",
        "Доставляется",
        "Выполнен",
        "Отменён",
    ]

    status = request.form.get("status", "Новый")

    if status not in allowed_statuses:
        status = "Новый"

    conn = get_db()

    try:
        conn.execute("""
            UPDATE orders
            SET status = %s
            WHERE id = %s
        """, (status, order_id))
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("admin_orders"))


# =========================================================
# СПИСОК ТОВАРОВ
# =========================================================

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
        products=products,
    )


# =========================================================
# ДОБАВЛЕНИЕ ТОВАРА
# =========================================================

@app.route("/admin/products/add", methods=["POST"])
@admin_required
def add_product():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    emoji = request.form.get("emoji", "🌸").strip() or "🌸"

    try:
        price = Decimal(
            request.form.get("price", "0").replace(",", ".")
        )
        if not price.is_finite() or price < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return render_admin_error("Укажи корректную цену.")

    available = 1 if request.form.get("available") == "on" else 0

    if not name:
        return render_admin_error("Укажи название товара.")

    image = ""
    uploaded_file = request.files.get("image")

    if uploaded_file and uploaded_file.filename:
        image = save_uploaded_file(uploaded_file)

        if not image:
            return render_admin_error(
                "Формат фото должен быть JPG, JPEG, PNG или WEBP."
            )

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO products
            (name, description, price, image, emoji, available)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (name, description, price, image, emoji, available))
        conn.commit()
    finally:
        conn.close()

    flash("Товар успешно добавлен!", "success")
    return redirect(url_for("admin_products"))


# =========================================================
# РЕДАКТИРОВАНИЕ ТОВАРА
# =========================================================

@app.route(
    "/admin/products/<int:product_id>/edit",
    methods=["GET", "POST"],
)
@admin_required
def edit_product(product_id):
    conn = get_db()

    try:
        product = conn.execute("""
            SELECT *
            FROM products
            WHERE id = %s
        """, (product_id,)).fetchone()

        if not product:
            flash("Товар не найден.", "error")
            return redirect(url_for("admin_products"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            emoji = request.form.get("emoji", "🌸").strip() or "🌸"

            try:
                price = Decimal(
                    request.form.get("price", "0").replace(",", ".")
                )
                if not price.is_finite() or price < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                flash("Укажи корректную цену.", "error")
                return redirect(url_for(
                    "edit_product", product_id=product_id
                ))

            if not name:
                flash("Укажи название товара.", "error")
                return redirect(url_for(
                    "edit_product", product_id=product_id
                ))

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

                if not new_image:
                    flash(
                        "Формат фото должен быть JPG, JPEG, PNG или WEBP.",
                        "error",
                    )
                    return redirect(url_for(
                        "edit_product", product_id=product_id
                    ))

                delete_uploaded_file(image)
                image = new_image

            conn.execute("""
                UPDATE products
                SET name = %s,
                    description = %s,
                    price = %s,
                    image = %s,
                    emoji = %s,
                    available = %s
                WHERE id = %s
            """, (
                name,
                description,
                price,
                image,
                emoji,
                available,
                product_id,
            ))
            conn.commit()

            flash("Товар успешно изменён!", "success")
            return redirect(url_for("admin_products"))

    finally:
        conn.close()

    return render_template(
        "admin_edit_product.html",
        product=product,
    )


# =========================================================
# УДАЛЕНИЕ ТОВАРА
# =========================================================

@app.route(
    "/admin/products/<int:product_id>/delete",
    methods=["POST"],
)
@admin_required
def delete_product(product_id):
    conn = get_db()

    try:
        product = conn.execute("""
            SELECT image
            FROM products
            WHERE id = %s
        """, (product_id,)).fetchone()

        if product:
            conn.execute(
                "DELETE FROM products WHERE id = %s",
                (product_id,),
            )
            conn.commit()
            delete_uploaded_file(product["image"])
    finally:
        conn.close()

    flash("Товар удалён.", "success")
    return redirect(url_for("admin_products"))


# =========================================================
# АДМИНИСТРАТОРЫ
# =========================================================

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

    return render_template(
        "admin_admins.html",
        admins=admins,
    )


@app.route("/admin/add-admin", methods=["POST"])
@admin_required
def add_admin():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_admin_error(
            "Заполни логин и пароль.",
            "admin_admins",
        )

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO admins (username, password_hash)
            VALUES (%s, %s)
        """, (
            username,
            generate_password_hash(password),
        ))
        conn.commit()

    except UniqueViolation:
        conn.rollback()
        return render_admin_error(
            "Такой логин уже существует.",
            "admin_admins",
        )

    finally:
        conn.close()

    flash("Администратор добавлен.", "success")
    return redirect(url_for("admin_admins"))


@app.route(
    "/admin/delete-admin/<int:admin_id>",
    methods=["POST"],
)
@admin_required
def delete_admin(admin_id):
    if admin_id == session.get("admin_id"):
        return render_admin_error(
            "Нельзя удалить текущий аккаунт.",
            "admin_admins",
        )

    conn = get_db()

    try:
        conn.execute(
            "DELETE FROM admins WHERE id = %s",
            (admin_id,),
        )
        conn.commit()
    finally:
        conn.close()

    flash("Администратор удалён.", "success")
    return redirect(url_for("admin_admins"))


# =========================================================
# ОБРАБОТКА СЛИШКОМ БОЛЬШИХ ФОТО
# =========================================================

@app.errorhandler(413)
def too_large(error):
    flash(
        "Фотография слишком большая. Максимальный размер — 8 МБ.",
        "error",
    )
    return redirect(url_for("admin_products"))


# =========================================================
# ПРОВЕРКА ЗАПУСКА
# =========================================================

@app.route("/health")
def health():
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok", "database": "connected"})
    except Exception as exc:
        print("Health check error:", repr(exc))
        return jsonify({"status": "error", "database": "unavailable"}), 500


# Создаём таблицы при старте приложения.
init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
