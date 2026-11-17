
    try:
        price = Decimal(
            request.form.get("price", "0").replace(",", ".")
        )
        if not price.is_finite() or price < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return render_admin_error("Укажи корректную цену.")

    if not name:
        return render_admin_error("Укажи название товара.")

    available = 1 if request.form.get("available") == "on" else 0

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

@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
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
                return redirect(url_for("edit_product", product_id=product_id))

            if not name:
                flash("Укажи название товара.", "error")
                return redirect(url_for("edit_product", product_id=product_id))

            available = 1 if request.form.get("available") == "on" else 0
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
                    return redirect(url_for("edit_product", product_id=product_id))

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
                name, description, price,
                image, emoji, available, product_id,
            ))
            conn.commit()

            flash("Товар успешно изменён!", "success")
            return redirect(url_for("admin_products"))

    finally:
        conn.close()

    return render_template("admin_edit_product.html", product=product)


# =========================================================
# УДАЛЕНИЕ ТОВАРА
# =========================================================

@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
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

    return render_template("admin_admins.html", admins=admins)


@app.route("/admin/add-admin", methods=["POST"])
@admin_required
def add_admin():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_admin_error("Заполни логин и пароль.", "admin_admins")

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO admins (username, password_hash)
            VALUES (%s, %s)
        """, (username, generate_password_hash(password)))
        conn.commit()
