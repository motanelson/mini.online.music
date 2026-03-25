from flask import Flask, request, redirect, send_from_directory
import sqlite3
import hashlib
import secrets
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

DB = "minimusic.db"
AUDIO_FOLDER = "audios"
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15MB

os.makedirs(AUDIO_FOLDER, exist_ok=True)

# ---------- DB ----------
def get_db():
    return sqlite3.connect(DB, timeout=10, check_same_thread=False)

def init_db():
    with get_db() as db:
        c = db.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            password TEXT,
            approved INTEGER DEFAULT 0,
            activation_key TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            audio TEXT
        )
        """)

# ---------- UTIL ----------
def sanitize(text):
    return text.replace("<", "").replace(">", "")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_key():
    return secrets.token_hex(16)

# ---------- USERS ----------
def create_user(url, password):
    key = generate_key()

    with get_db() as db:
        c = db.cursor()
        c.execute(
            "INSERT INTO users (url, password, activation_key) VALUES (?, ?, ?)",
            (url, hash_password(password), key)
        )
        user_id = c.lastrowid

    link = f"http://127.0.0.1:5000/activate/{user_id}/{key}"

    with open("approve.txt", "a") as f:
        f.write(f"{url}|||{link}\n")

def check_user(url, password):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT id, password, approved FROM users WHERE url=?", (url,))
        row = c.fetchone()

    if row:
        if row[1] != hash_password(password):
            return "wrong_pass", None
        if row[2] == 0:
            return "not_approved", None
        return "ok", row[0]

    return "not_exist", None

def get_all_users():
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT id, url FROM users WHERE approved=1")
        return c.fetchall()

# ---------- AUDIO ----------
def save_audio(file):
    if file:
        name = secure_filename(file.filename.lower())

        if not name.endswith((".mp3", ".wav", ".ogg", ".flac")):
            return None

        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)

        if size > MAX_FILE_SIZE:
            return None

        with get_db() as db:
            c = db.cursor()
            c.execute("SELECT MAX(id) FROM posts")
            max_id = c.fetchone()[0]
            audio_id = (max_id or 0) + 1

        ext = name.split(".")[-1]
        filename = f"{audio_id}.{ext}"

        path = os.path.join(AUDIO_FOLDER, filename)
        file.save(path)

        return filename

    return None

@app.route("/audio/<filename>")
def get_audio(filename):
    return send_from_directory(AUDIO_FOLDER, filename)

# ---------- POSTS ----------
def save_post(user_id, message, audio):
    with get_db() as db:
        c = db.cursor()
        c.execute(
            "INSERT INTO posts (user_id, message, audio) VALUES (?, ?, ?)",
            (user_id, message, audio)
        )

def load_posts(user_id, page, per_page=5):
    offset = (page - 1) * per_page

    with get_db() as db:
        c = db.cursor()
        c.execute(
            "SELECT message, audio FROM posts WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, per_page, offset)
        )
        return c.fetchall()

def count_posts(user_id):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (user_id,))
        return c.fetchone()[0]

# ---------- ROUTES ----------

@app.route("/")
def home():
    users = get_all_users()

    html = """
    <body style="background:#0f0f0f;color:white;font-family:sans-serif;">
    <h1>MiniOnlineMusic 🎵</h1>
    <a href="/register">➕ Registar</a>
    <h2>Artistas</h2>
    """

    for uid, url in users:
        html += f'<a href="/user/{uid}">@{url}</a><br>'

    html += "</body>"
    return html

@app.route("/register", methods=["GET", "POST"])
def register():
    msg = ""

    if request.method == "POST":
        url = sanitize(request.form.get("url", ""))
        password = request.form.get("password", "")

        if url and password:
            try:
                create_user(url, password)
                msg = "Registado! Aguarda aprovação."
            except:
                msg = "Já existe"

    return f"""
    <body style="background:#0f0f0f;color:white;">
    <a href="/">⬅</a>
    <h2>Registar</h2>
    <form method="POST">
        <input name="url"><br>
        <input type="password" name="password"><br>
        <button>Registar</button>
    </form>
    <p>{msg}</p>
    </body>
    """

@app.route("/activate/<int:user_id>/<key>")
def activate(user_id, key):
    with get_db() as db:
        c = db.cursor()
        c.execute("SELECT activation_key FROM users WHERE id=?", (user_id,))
        row = c.fetchone()

        if row and row[0] == key:
            c.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
            db.commit()
            return "Conta ativada!"

    return "Link inválido"

@app.route("/user/<int:user_id>", methods=["GET", "POST"])
def user_page(user_id):
    page = request.args.get("page", 1, type=int)
    error = ""

    if request.method == "POST":
        url = sanitize(request.form.get("url", ""))
        msg = sanitize(request.form.get("message", ""))
        password = request.form.get("password", "")
        file = request.files.get("audio")

        if url and msg and password:
            res, uid = check_user(url, password)

            if res == "ok":
                if uid != user_id:
                    error = "❌ Só podes postar no teu perfil!"
                else:
                    aud = save_audio(file)
                    save_post(user_id, msg, aud)
                    return redirect(f"/user/{user_id}?page={page}")

            else:
                error = "Erro autenticação"

    posts = load_posts(user_id, page)
    total = count_posts(user_id)
    total_pages = (total + 4) // 5 if total else 1

    html = f"""
    <body style="background:#0f0f0f;color:white;font-family:sans-serif;">
    <a href="/">⬅ Voltar</a>

    <h2>Artista #{user_id}</h2>

    <form method="POST" enctype="multipart/form-data">
        <input name="url"><br>
        <input type="password" name="password"><br>
        <textarea name="message" placeholder="descrição da música"></textarea><br>
        <input type="file" name="audio"><br>
        <button>Upload</button>
    </form>

    <p>{error}</p>
    <hr>
    """

    for msg, aud in posts:
        html += f"<p>{msg}</p>"

        if aud:
            html += f"""
            <audio controls>
                <source src="/audio/{aud}">
            </audio><br>
            <a href="/audio/{aud}" download>⬇ Download</a>
            """

        html += "<hr>"

    html += f"Página {page}/{total_pages}"

    html += "</body>"
    return html

# ---------- START ----------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False)
