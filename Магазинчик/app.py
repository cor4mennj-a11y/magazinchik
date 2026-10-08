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

import os
import requests
import sqlite3
import json
import uuid

from functools import wraps
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)
from werkzeug.utils import secure_filename


app = Flask(__name__)


# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = "1417232861"


app.secret_key = os.environ.get(
    "SECRET_KEY",
    "temporary-secret-key"
)


DATABASE = "shop.db"


# Папка, куда будут сохраняться фотографии
UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "static",
    "uploads"
)


app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Максимальный размер фотографии — 8 МБ
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


# Разрешённые форматы фотографий
ALLOWED_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp"
}


# Создаём папку для загрузок
os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def allowed_file(filename):

    if not filename:
        return False

    if "." not in filename:
        return False

    extension = (
        filename.rsplit(".", 1)[1]
        .lower()
    )

    return extension in ALLOWED_EXTENSIONS


def save_uploaded_file(file):

    if not file:
        return ""

    if not file.filename:
        return ""

    if not allowed_file(file.filename):
        return ""

    original_name = secure_filename(
        file.filename
    )

    if not original_name:
        return ""

    extension = (
        original_name.rsplit(".", 1)[1]
        .lower()
    )

    filename = (
        f"{uuid.uuid4().hex}.{extension}"
    )

    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename
    )

    file.save(filepath)

    return f"/static/uploads/{filename}"


def delete_uploaded_file(image_path):

    if not image_path:
        return

    # Удаляем только фотографии,
    # загруженные в нашу папку.
    if not image_path.startswith(
        "/static/uploads/"
    ):
        return

    filename = os.path.basename(
        image_path
    )

    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename
    )

    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
    except Exception as e:
        print(
            "Не удалось удалить фотографию:",
            e
        )


# =========================
# JSON FILTER
# =========================

@app.template_filter("from_json")
def from_json(value):

    try:
        return json.loads(value)

    except (
        TypeError,
        json.JSONDecodeError
    ):

        return []


# =========================
# DATABASE
# =========================

def get_db():

    conn = sqlite3.connect(
        DATABASE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    # На всякий случай ещё раз создаём папку
    os.makedirs(
        UPLOAD_FOLDER,
        exist_ok=True
    )

    conn = get_db()


    # АДМИНИСТРАТОРЫ

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)


    # ЗАКАЗЫ

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


    # ТОВАРЫ

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


    # ДОБАВЛЯЕМ НАЧАЛЬНЫЕ ТОВАРЫ,
    # ЕСЛИ БАЗА ПУСТАЯ

    product_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM products
        """
    ).fetchone()["count"]


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
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_products
        )


    # СОЗДАНИЕ ПЕРВОГО АДМИНА

    admin_username = os.environ.get(
        "ADMIN_USERNAME"
    )

    admin_password = os.environ.get(
        "ADMIN_PASSWORD"
    )


    if (
        admin_username
        and admin_password
    ):

        existing_admin = conn.execute(
            """
            SELECT id
            FROM admins
            WHERE username = ?
            """,
            (admin_username,)
        ).fetchone()


        if not existing_admin:

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
                    admin_username,
                    generate_password_hash(
                        admin_password
                    )
                )
            )


    conn.commit()

    conn.close()


# =========================
# ПРОВЕРКА АДМИНА
# =========================

def admin_required(view):

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        if "admin_id" not in session:

            return redirect(
                url_for("admin_login")
            )

        return view(
            *args,
            **kwargs
        )

    return wrapped_view


# =========================
# ГЛАВНАЯ
# =========================

@app.route("/")
def home():

    conn = get_db()


    products = conn.execute(
        """
        SELECT *
        FROM products
        WHERE available = 1
        ORDER BY id
        """
    ).fetchall()


    conn.close()


    return render_template(
        "index.html",
        products=products
    )


# =========================
# ОТПРАВКА ЗАКАЗА
# =========================

@app.route(
    "/send-order",
    methods=["POST"]
)
def send_order():

    try:

        data = request.get_json()


        if not data:

            return jsonify({
                "success": False,
                "error": "Данные заказа не получены"
            }), 400


        name = data.get(
            "name",
            ""
        )

        phone = data.get(
            "phone",
            ""
        )

        address = data.get(
            "address",
            ""
        )

        items = data.get(
            "items",
            []
        )

        total = data.get(
            "total",
            0
        )


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


        if not TELEGRAM_BOT_TOKEN:

            return jsonify({
                "success": False,
                "error": "TELEGRAM_BOT_TOKEN не найден на сервере"
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
# LOGIN
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


        if (
            admin
            and check_password_hash(
                admin["password_hash"],
                password
            )
        ):

            session["admin_id"] = (
                admin["id"]
            )

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
# LOGOUT
# =========================

@app.route("/admin/logout")
def admin_logout():

    session.clear()


    return redirect(
        url_for("admin_login")
    )


# =========================
# ГЛАВНАЯ АДМИНКИ
# =========================

@app.route("/admin")
@admin_required
def admin_panel():

    conn = get_db()


    orders_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orders
        """
    ).fetchone()["count"]


    products_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM products
        """
    ).fetchone()["count"]


    admins_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM admins
        """
    ).fetchone()["count"]


    new_orders_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orders
        WHERE status = 'Новый'
        """
    ).fetchone()["count"]


    conn.close()


    return render_template(
        "admin.html",
        orders_count=orders_count,
        products_count=products_count,
        admins_count=admins_count,
        new_orders_count=new_orders_count
    )


# =========================
# ЗАКАЗЫ
# =========================

@app.route("/admin/orders")
@admin_required
def admin_orders():

    conn = get_db()


    orders = conn.execute(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        """
    ).fetchall()


    conn.close()


    return render_template(
        "admin_orders.html",
        orders=orders
    )


# =========================
# СТАТУС ЗАКАЗА
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
        url_for("admin_orders")
    )


# =========================
# ТОВАРЫ
# =========================

@app.route("/admin/products")
@admin_required
def admin_products():

    conn = get_db()


    products = conn.execute(
        """
        SELECT *
        FROM products
        ORDER BY id
        """
    ).fetchall()


    conn.close()


    return render_template(
        "admin_products.html",
        products=products
    )


# =========================
# ДОБАВИТЬ ТОВАР
# =========================

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
    ).strip()


    if not emoji:

        emoji = "🌸"


    # ЦЕНА

    try:

        price = float(
            request.form.get(
                "price",
                "0"
            ).replace(",", ".")
        )

    except ValueError:

        price = 0


    # ДОСТУПНОСТЬ

    available = (
        1
        if request.form.get(
            "available"
        ) == "on"
        else 0
    )


    # ФОТО

    image = ""


    uploaded_file = request.files.get(
        "image"
    )


    if uploaded_file and uploaded_file.filename:

        image = save_uploaded_file(
            uploaded_file
        )


    if not name:

        return redirect(
            url_for("admin_products")
        )


    conn = get_db()


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
        VALUES (?, ?, ?, ?, ?, ?)
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

    conn.close()


    flash(
        "Товар успешно добавлен!",
        "success"
    )


    return redirect(
        url_for("admin_products")
    )


# =========================
# РЕДАКТИРОВАНИЕ ТОВАРА
# =========================

@app.route(
    "/admin/products/<int:product_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def edit_product(product_id):

    conn = get_db()


    product = conn.execute(
        """
        SELECT *
        FROM products
        WHERE id = ?
        """,
        (product_id,)
    ).fetchone()


    if not product:

        conn.close()


        return redirect(
            url_for("admin_products")
        )


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
        ).strip()


        if not emoji:

            emoji = "🌸"


        # ЦЕНА

        try:

            price = float(
                request.form.get(
                    "price",
                    "0"
                ).replace(",", ".")
            )

        except ValueError:

            price = 0


        # ДОСТУПНОСТЬ

        available = (
            1
            if request.form.get(
                "available"
            ) == "on"
            else 0
        )


        # ТЕКУЩЕЕ ФОТО

        image = product["image"] or ""


        # УДАЛЕНИЕ ФОТО

        remove_image = (
            request.form.get(
                "remove_image"
            ) == "on"
        )


        if remove_image:

            delete_uploaded_file(
                image
            )

            image = ""


        # НОВОЕ ФОТО

        uploaded_file = request.files.get(
            "image"
        )


        if (
            uploaded_file
            and uploaded_file.filename
        ):

            new_image = save_uploaded_file(
                uploaded_file
            )


            if new_image:

                # Удаляем старую загруженную
                # фотографию
                delete_uploaded_file(
                    image
                )

                image = new_image


        if name:

            conn.execute(
                """
                UPDATE products
                SET
                    name = ?,
                    description = ?,
                    price = ?,
                    image = ?,
                    emoji = ?,
                    available = ?
                WHERE id = ?
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


        conn.close()


        flash(
            "Товар успешно изменён!",
            "success"
        )


        return redirect(
            url_for("admin_products")
        )


    conn.close()


    return render_template(
        "admin_edit_product.html",
        product=product
    )


# =========================
# УДАЛИТЬ ТОВАР
# =========================

@app.route(
    "/admin/products/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):

    conn = get_db()


    product = conn.execute(
        """
        SELECT image
        FROM products
        WHERE id = ?
        """,
        (product_id,)
    ).fetchone()


    if product:

        delete_uploaded_file(
            product["image"]
        )


    conn.execute(
        """
        DELETE FROM products
        WHERE id = ?
        """,
        (product_id,)
    )


    conn.commit()

    conn.close()


    flash(
        "Товар удалён.",
        "success"
    )


    return redirect(
        url_for("admin_products")
    )


# =========================
# АДМИНИСТРАТОРЫ
# =========================

@app.route("/admin/admins")
@admin_required
def admin_admins():

    conn = get_db()


    admins = conn.execute(
        """
        SELECT id, username
        FROM admins
        ORDER BY id
        """
    ).fetchall()


    conn.close()


    return render_template(
        "admin_admins.html",
        admins=admins
    )


# =========================
# ДОБАВИТЬ АДМИНИСТРАТОРА
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
            url_for("admin_admins")
        )


    password_hash = (
        generate_password_hash(
            password
        )
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
        url_for("admin_admins")
    )


# =========================
# УДАЛИТЬ АДМИНИСТРАТОРА
# =========================

@app.route(
    "/admin/delete-admin/<int:admin_id>",
    methods=["POST"]
)
@admin_required
def delete_admin(admin_id):

    if admin_id == session.get(
        "admin_id"
    ):

        return redirect(
            url_for("admin_admins")
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
        url_for("admin_admins")
    )


# =========================
# ОШИБКА СЛИШКОМ БОЛЬШОГО ФАЙЛА
# =========================

@app.errorhandler(413)
def too_large(e):

    flash(
        "Фотография слишком большая. Максимальный размер — 8 МБ.",
        "error"
    )


    return redirect(
        url_for("admin_products")
    )


# =========================
# ЗАПУСК
# =========================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
