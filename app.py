from flask import Flask, jsonify, render_template, request, redirect, session, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime
import os
import json
import sqlite3
import logging

# --- Tillåt OAuth utan https (lokalt) ---
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# --- Konfiguration ---
CONFIG_FILE = "config.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
BYPASS_AE_FILTER = True
DB_NAME = "chat_archive.db"

# --- Läs config.json om den finns ---
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE) as f:
        user_config = json.load(f)
else:
    user_config = None

# --- Appen ---
app = Flask(__name__)
app.secret_key = "PiaRocks2025SuperSecureMagicKey"

# --- Databas ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_chat (
            id TEXT PRIMARY KEY,
            author TEXT,
            message TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(chat_id, author, message, timestamp):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO live_chat (id, author, message, timestamp)
        VALUES (?, ?, ?, ?)
    """, (chat_id, author, message, timestamp))
    conn.commit()
    conn.close()

# --- Hitta aktiv stream ---
def get_live_chat_id(credentials):
    youtube = build("youtube", "v3", credentials=credentials)
    try:
        req = youtube.liveBroadcasts().list(
            part="snippet",
            broadcastStatus="active",
            broadcastType="all"
        )
        resp = req.execute()
        items = resp.get("items", [])
        if not items:
            logging.warning("Ingen aktiv livestream hittades 😢")
            return None
        return items[0]["snippet"].get("liveChatId")
    except Exception as e:
        logging.error(f"Kunde inte hämta liveChatId: {e}")
        return None

# --- Hämta kommentarer ---
def fetch_live_chat():
    if "credentials" not in session:
        logging.warning("Ingen OAuth-token – logga in först.")
        return []

    credentials = Credentials(**session["credentials"])
    youtube = build("youtube", "v3", credentials=credentials)

    live_chat_id = get_live_chat_id(credentials)
    if not live_chat_id:
        return []

    try:
        request = youtube.liveChatMessages().list(
            liveChatId=live_chat_id,
            part="snippet,authorDetails",
            maxResults=20
        )
        response = request.execute()

        comments = []
        for item in response.get("items", []):
            message = item["snippet"]["displayMessage"]
            if BYPASS_AE_FILTER or "Æ" in message:
                chat_id = item["id"]
                author = item["authorDetails"]["displayName"]
                timestamp = item["snippet"]["publishedAt"]
                logging.info(f"Sparar: {author} → {message}")
                save_to_db(chat_id, author, message, timestamp)
                comments.append({"author": author, "message": message, "timestamp": timestamp})
        return comments
    except Exception as e:
        logging.error(f"API-fel vid hämtning: {e}")
        return []

# --- ROUTES ---

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        data = {
            "channel_id": request.form["channel_id"],
            "client_id": request.form["client_id"],
            "client_secret": request.form["client_secret"]
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)

        global user_config
        user_config = data  # Uppdatera direkt i minnet!

        return render_template("setup_done.html")

    return render_template("setup.html")

@app.route("/")
def index():
    if not user_config:
        return redirect(url_for("setup"))

    if "credentials" not in session:
        return render_template("logged_out.html")

    comments = fetch_live_chat()
    error = None
    if not comments:
        error = "Livechatten kunde inte hämtas. Är streamen igång?"
    return render_template("index.html", comments=comments, error=error)


@app.route("/login")
def login():
    if not user_config:
        return redirect(url_for("setup"))
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": user_config["client_id"],
                "client_secret": user_config["client_secret"],
                "redirect_uris": [url_for("oauth2callback", _external=True)],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True)
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    if not user_config:
        return redirect(url_for("setup"))
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": user_config["client_id"],
                "client_secret": user_config["client_secret"],
                "redirect_uris": [url_for("oauth2callback", _external=True)],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True)
    )
    flow.fetch_token(authorization_response=request.url)
    session["credentials"] = {
        "token": flow.credentials.token,
        "refresh_token": flow.credentials.refresh_token,
        "token_uri": flow.credentials.token_uri,
        "client_id": flow.credentials.client_id,
        "client_secret": flow.credentials.client_secret,
        "scopes": flow.credentials.scopes
    }
    return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/fetch_comments")
def fetch_comments():
    return jsonify(fetch_live_chat())

@app.route("/archive")
def archive():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT author, message, timestamp FROM live_chat ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"author": r[0], "message": r[1], "timestamp": r[2]} for r in rows])

# --- Starta app ---
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5003, debug=True)

