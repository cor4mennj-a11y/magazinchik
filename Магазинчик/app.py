import os
import json
import sqlite3
import uuid

import psycopg
import requests

from psycopg.rows import dict_row
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


# =========================================================
# НАСТРОЙКИ
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL"
)

# Старый файл SQLite — только для возможного переноса данных
LEGACY_SQLITE_DATABASE = os.path.join(
    BASE_DIR,
    "shop.db"
)

UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "static",
    "uploads"
)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "temporary-secret-key"
)

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = "1417232861"

ALLOWED_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp"
}

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# =========================================================
# POSTGRESQL
# =========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "В Render не задана переменная DATABASE_URL"
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
        sslmode="require"
    )


# =========================================================
# ФОТОГРАФИИ
# =========================================================

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

    filepath = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    file.save(filepath)

    return f"/static/uploads/{filename}"


def delete_uploaded_file(image_path):
    if not image_path:
        return

    # Удаляем только файлы из нашей папки загрузок
    if not image_path.startswith("/static/uploads/"):
        return

    filename = os.path.basename(image_path)

    filepath = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
    except Exception as e:
        print("Ошибка удаления фотографии:", e)


# =========================================================
# JINJA FILTER
# =========================================================

@app.template_filter("from_json")
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


# =========================================================
# ПЕРЕНОС СТАРОЙ SQLITE-БАЗЫ
# =========================================================

def old_table_exists(old_conn, table_name):
    result = old_conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,)
    ).fetchone()

    return result is not None


def old_value(row, key, default=None):
    if key not in row.keys():
        return default

    value = row[key]

    return default if value is None else value


def migrate_legacy_sqlite():
    """
    Если старый shop.db сохранился, переносим данные
    в PostgreSQL. Каждая таблица переносится только тогда,
    когда соответствующая таблица PostgreSQL пуста.
    """

    if not os.path.isfile(LEGACY_SQLITE_DATABASE):
        print("Старый shop.db не найден. Продолжаем без переноса.")
        return

    old_conn = None
    pg_conn = None

    try:
        old_conn = sqlite3.connect(
            LEGACY_SQLITE_DATABASE
        )

        old_conn.row_factory = sqlite3.Row

        pg_conn = get_db()


        specifications = {
            "admins": {
                "columns": [
                    "id",
                    "username",
                    "password_hash"
                ],
                "values": lambda row: (
                    old_value(row, "id"),
                    old_value(row, "username", ""),
                    old_value(row, "password_hash", "")
                )
            },

            "orders": {
                "columns": [
                    "id",
                    "name",
                    "phone",
                    "address",
                    "items",
                    "total",
                    "status",
                    "created_at"
                ],
                "values": lambda row: (
                    old_value(row, "id"),
                    old_value(row, "name", ""),
                    old_value(row, "phone", ""),
                    old_value(row, "address", ""),
                    old_value(row, "items", "[]"),
                    old_value(row, "total", 0),
                    old_value(row, "status", "Новый"),
                    old_value(
                        row,
                        "created_at",
                        "1970-01-01 00:00:00"
                    )
                )
            },

            "products": {
                "columns": [
                    "id",
                    "name",
                    "description",
                    "price",
                    "image",
                    "emoji",
                    "available",
                    "created_at"
                ],
                "values": lambda row: (
                    old_value(row, "id"),
                    old_value(row, "name", "Без названия"),
                    old_value(row, "description", ""),
                    old_value(row, "price", 0),
                    old_value(row, "image", ""),
                    old_value(row, "emoji", "🌸"),
                    old_value(row, "available", 1),
                    old_value(
                        row,
                        "created_at",
                        "1970-01-01 00:00:00"
                    )
                )
            }
        }


        for table_name, spec in specifications.items():
            try:
                if not old_table_exists(
                    old_conn,
                    table_name
                ):
                    continue

                count = pg_conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table_name}"
                ).fetchone()["count"]

                # Не дублируем данные в уже заполненной таблице
                if count > 0:
                    continue

                rows = old_conn.execute(
                    f"SELECT * FROM {table_name}"
                ).fetchall()

                if not rows:
                    continue

                columns = spec["columns"]

                column_sql = ", ".join(columns)
                placeholders = ", ".join(
                    ["%s"] * len(columns)
                )

                insert_sql = (
                    f"INSERT INTO {table_name} "
                    f"({column_sql}) "
                    f"VALUES ({placeholders})"
                )

                for row in rows:
                    pg_conn.execute(
                        insert_sql,
                        spec["values"](row)
                    )

                pg_conn.commit()

                # Обновляем счётчик BIGSERIAL после переноса ID
                pg_conn.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence(
                            '{table_name}',
                            'id'
                        ),
                        (
                            SELECT MAX(id)
                            FROM {table_name}
                        ),
                        true
                    )
                    """
                )

                pg_conn.commit()

                print(
                    f"Перенесено записей из SQLite, "
                    f"таблица {table_name}: {len(rows)}"
                )

            except Exception as e:
                pg_conn.rollback()

                print(
                    f"Ошибка переноса таблицы {table_name}:",
                    str(e)
                )

    except Exception as e:
        print("Ошибка чтения старой базы:", str(e))

    finally:
        if old_conn is not None:
            old_conn.close()

        if pg_conn is not None:
            pg_conn.close()


# =========================================================
# СОЗДАНИЕ ТАБЛИЦ POSTGRESQL
# =========================================================

def init_db():
    conn = get_db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id BIGSERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)

        conn.execute("""
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

        conn.execute("""
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

        conn.commit()

    finally:
        conn.close()


    # Если старый shop.db ещё доступен, попробуем его перенести
    migrate_legacy_sqlite()


    conn = get_db()

    try:
        product_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM products
            """
        ).fetchone()["count"]

        # Начальные товары создаются только для пустого каталога
        if product_count == 0:
            default_products = [
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
            ]

            conn.executemany(
                """
                INSERT INTO products
                (
                    name,
                    description,
                    price,
                    image,
                    emoji,
                    available
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                default_products
            )

        # Создаём первого администратора из Render Environment
        admin_username = os.environ.get("ADMIN_USERNAME")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_username and admin_password:
            existing_admin = conn.execute(
                """
                SELECT id
                FROM admins
                WHERE username = %s
                """,
                (admin_username,)
            ).fetchone()

            if not existing_admin:
                conn.execute(
                    """
                    INSERT INTO admins
                    (username, password_hash)
                    VALUES (%s, %s)
                    """,
                    (
                        admin_username,
                        generate_password_hash(admin_password)
                    )
                )

        conn.commit()

    finally:
        conn.close()


# =========================================================
# ЗАЩИТА АДМИНКИ
# =========================================================

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("admin_login"))

        return view(*args, **kwargs)

    return wrapped_view


# =========================================================
# ГЛАВНАЯ СТРАНИЦА
# =========================================================

@app.route("/")
def home():
    conn = get_db()

    try:
        products = conn.execute(
            """
            SELECT *
            FROM products
            WHERE available = 1
            ORDER BY id
            """
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "index.html",
        products=products
    )


# =========================================================
# ОТПРАВКА ЗАКАЗА
# =========================================================

@app.route("/send-order", methods=["POST"])
def send_order():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "Данные заказа не получены"
            }), 400

        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        address = str(data.get("address", "")).strip()
        items = data.get("items", [])
        total = data.get("total", 0)

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
            total = float(total)
            if total < 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "error": "Некорректная сумма заказа"
            }), 400

        conn = get_db()

        try:
            result = conn.execute(
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
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    name,
                    phone,
                    address,
                    json.dumps(items, ensure_ascii=False),
                    total,
                    "Новый"
                )
            ).fetchone()

            order_id = result["id"]
            conn.commit()

        finally:
            conn.close()

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

        if not TELEGRAM_BOT_TOKEN:
            # Заказ уже сохранён в PostgreSQL.
            return jsonify({
                "success": True,
                "telegram_sent": False,
                "message": "Заказ сохранён, но Telegram не настроен"
            })

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

            if response.ok:
                return jsonify({
                    "success": True,
                    "telegram_sent": True
                })

            print("Telegram response:", response.text)

        except requests.RequestException as e:
            print("Telegram error:", str(e))

        # Заказ сохранён, поэтому не сообщаем покупателю,
        # что сам заказ не прошёл, если не доставилось только уведомление.
        return jsonify({
            "success": True,
            "telegram_sent": False,
            "message": "Заказ сохранён"
        })

    except Exception as e:
        print("Ошибка оформления заказа:", str(e))

        return jsonify({
            "success": False,
            "error": "Не удалось сохранить заказ. Попробуйте позже."
        }), 500


# =========================================================
# ВХОД В АДМИНКУ
# =========================================================

@app.route("/admin/login", methods=["GET", "POST"])
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

        try:
            admin = conn.execute(
                """
                SELECT *
                FROM admins
                WHERE username = %s
                """,
                (username,)
            ).fetchone()

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

        new_orders_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM orders
            WHERE status = 'Новый'
            """
        ).fetchone()["count"]

        today_orders = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM orders
            WHERE created_at::date = CURRENT_DATE
            """
        ).fetchone()["count"]

        today_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE created_at::date = CURRENT_DATE
              AND COALESCE(status, '') <> 'Отменён'
            """
        ).fetchone()["total"]

        month_orders = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM orders
            WHERE date_trunc('month', created_at)
                = date_trunc('month', CURRENT_TIMESTAMP::timestamp)
            """
        ).fetchone()["count"]

        month_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS total
            FROM orders
            WHERE date_trunc('month', created_at)
                = date_trunc('month', CURRENT_TIMESTAMP::timestamp)
              AND COALESCE(status, '') <> 'Отменён'
            """
        ).fetchone()["total"]

        revenue_result = conn.execute(
            """
            SELECT
                COALESCE(SUM(total), 0) AS total,
                COUNT(*) AS count
            FROM orders
            WHERE COALESCE(status, '') <> 'Отменён'
            """
        ).fetchone()

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


# =========================================================
# СПИСОК ЗАКАЗОВ
# =========================================================

@app.route("/admin/orders")
@admin_required
def admin_orders():
    conn = get_db()

    try:
        orders = conn.execute(
            """
            SELECT *
            FROM orders
            ORDER BY id DESC
            """
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "admin_orders.html",
        orders=orders
    )


# =========================================================
# ИЗМЕНЕНИЕ СТАТУСА ЗАКАЗА
# =========================================================

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

    try:
        conn.execute(
            """
            UPDATE orders
            SET status = %s
            WHERE id = %s
            """,
            (status, order_id)
        )

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
        products = conn.execute(
            """
            SELECT *
            FROM products
            ORDER BY id
            """
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "admin_products.html",
        products=products
    )


# =========================================================
# ДОБАВЛЕНИЕ ТОВАРА
# =========================================================

@app.route(
    "/admin/products/add",
    methods=["POST"]
)
@admin_required
def add_product():
    name = request.form.get(
        "name",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    emoji = request.form.get(
        "emoji",
        "🌸"
    ).strip() or "🌸"

    try:
        price = float(
            request.form.get(
                "price",
                "0"
            ).replace(",", ".")
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
        conn.execute(
            """
            INSERT INTO products
            (
                name,
                description,
                price,
                image,
                emoji,
                available
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                name,
                description,
                price,
                image,
                emoji,
                available
            )
        )

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
    methods=["GET", "POST"]
)
@admin_required
def edit_product(product_id):
    conn = get_db()

    try:
        product = conn.execute(
            """
            SELECT *
            FROM products
            WHERE id = %s
            """,
            (product_id,)
        ).fetchone()

        if not product:
            return redirect(url_for("admin_products"))

        if request.method == "POST":
            name = request.form.get(
                "name",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()

            emoji = request.form.get(
                "emoji",
                "🌸"
            ).strip() or "🌸"

            try:
                price = float(
                    request.form.get(
                        "price",
                        "0"
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
                1
                if request.form.get("available") == "on"
                else 0
            )

            image = product["image"] or ""

            remove_image = (
                request.form.get("remove_image") == "on"
            )

            if remove_image:
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

            conn.execute(
                """
                UPDATE products
                SET
                    name = %s,
                    description = %s,
                    price = %s,
                    image = %s,
                    emoji = %s,
                    available = %s
                WHERE id = %s
                """,
                (
                    name,
                    description,
                    price,
                    image,
                    emoji,
                    available,
                    product_id
                )
            )

            conn.commit()

            flash("Товар успешно изменён!", "success")

            return redirect(url_for("admin_products"))

    finally:
        conn.close()

    return render_template(
        "admin_edit_product.html",
        product=product
    )


# =========================================================
# УДАЛЕНИЕ ТОВАРА
# =========================================================

@app.route(
    "/admin/products/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):
    conn = get_db()

    try:
        product = conn.execute(
            """
            SELECT image
            FROM products
            WHERE id = %s
            """,
            (product_id,)
        ).fetchone()

        if product:
            conn.execute(
                """
                DELETE FROM products
                WHERE id = %s
                """,
                (product_id,)
            )

            conn.commit()

            delete_uploaded_file(
                product["image"]
            )

    finally:
        conn.close()

    flash("Товар удалён.", "success")

    return redirect(url_for("admin_products"))


# =========================================================
# СПИСОК АДМИНИСТРАТОРОВ
# =========================================================

@app.route("/admin/admins")
@admin_required
def admin_admins():
    conn = get_db()

    try:
        admins = conn.execute(
            """
            SELECT id, username
            FROM admins
            ORDER BY id
            """
        ).fetchall()

    finally:
        conn.close()

    return render_template(
        "admin_admins.html",
        admins=admins
    )


# =========================================================
# ДОБАВЛЕНИЕ АДМИНИСТРАТОРА
# =========================================================

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
        flash("Заполни логин и пароль.", "error")

        return redirect(url_for("admin_admins"))

    conn = get_db()

    try:
        conn.execute(
            """
            INSERT INTO admins
            (
                username,
                password_hash
            )
            VALUES (%s, %s)
            """,
            (
                username,
                generate_password_hash(password)
            )
        )

        conn.commit()

        flash("Администратор добавлен.", "success")

    except psycopg.errors.UniqueViolation:
        conn.rollback()

        flash("Такой логин уже существует.", "error")

    finally:
        conn.close()

    return redirect(url_for("admin_admins"))


# =========================================================
# УДАЛЕНИЕ АДМИНИСТРАТОРА
# =========================================================

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
        conn.execute(
            """
            DELETE FROM admins
            WHERE id = %s
            """,
            (admin_id,)
        )

        conn.commit()

    finally:
        conn.close()

    flash("Администратор удалён.", "success")

    return redirect(url_for("admin_admins"))


# =========================================================
# ОШИБКА — ФОТО БОЛЬШЕ 8 МБ
# =========================================================

@app.errorhandler(413)
def too_large(error):
    flash(
        "Фотография слишком большая. Максимальный размер — 8 МБ.",
        "error"
    )

    return redirect(url_for("admin_products"))


# =========================================================
# ИНИЦИАЛИЗАЦИЯ
# =========================================================

init_db()


# =========================================================
# ЛОКАЛЬНЫЙ ЗАПУСК
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
