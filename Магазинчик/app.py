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
