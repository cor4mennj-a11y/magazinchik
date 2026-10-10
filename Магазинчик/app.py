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
