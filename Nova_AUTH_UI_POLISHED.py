from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import requests
import os
import time
import secrets
import re
from urllib.parse import quote_plus

import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-secret-key")

OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY")
DATABASE = "nova.db"
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

SYSTEM_PROMPT = """You are Nova, a friendly, helpful AI assistant.
Be clear, useful, honest, and concise when appropriate. Remember the conversation context provided to you.
You can help with web research, coding, and understanding images when those tools are enabled.
Never claim you searched the web or saw an image unless the current request actually provided those capabilities.
If the user asks who created you, who made you, or who your creator is, answer: "I was created by @Zyvenix-wy, a YouTube creator who plays Roblox, using Termux." 
Do not invent a different creator.
"""

def db():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection

def setup_database():
    c = db()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            recovery_code TEXT NOT NULL,
            created INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory TEXT NOT NULL,
            created INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            login_time INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            username TEXT NOT NULL,
            code TEXT NOT NULL,
            purpose TEXT NOT NULL,
            expires INTEGER NOT NULL
        )
    """)
    # Upgrade older Nova databases without deleting existing data.
    user_columns = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    if "email" not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL")

    chat_columns = [row[1] for row in c.execute("PRAGMA table_info(chats)").fetchall()]
    if "user_id" not in chat_columns:
        c.execute("ALTER TABLE chats ADD COLUMN user_id INTEGER")
    if "title" not in chat_columns:
        c.execute("ALTER TABLE chats ADD COLUMN title TEXT")
        c.execute("UPDATE chats SET title='New chat' WHERE title IS NULL")
    if "created" not in chat_columns:
        c.execute("ALTER TABLE chats ADD COLUMN created INTEGER")
        c.execute("UPDATE chats SET created=? WHERE created IS NULL", (int(time.time()),))

    message_columns = [row[1] for row in c.execute("PRAGMA table_info(messages)").fetchall()]
    if "chat_id" not in message_columns:
        c.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER")
    if "role" not in message_columns:
        c.execute("ALTER TABLE messages ADD COLUMN role TEXT")
    if "content" not in message_columns:
        c.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    if "created" not in message_columns:
        c.execute("ALTER TABLE messages ADD COLUMN created INTEGER")

    memory_columns = [row[1] for row in c.execute("PRAGMA table_info(memories)").fetchall()]
    if "user_id" not in memory_columns:
        c.execute("ALTER TABLE memories ADD COLUMN user_id INTEGER")
    if "memory" not in memory_columns:
        c.execute("ALTER TABLE memories ADD COLUMN memory TEXT")
    if "created" not in memory_columns:
        c.execute("ALTER TABLE memories ADD COLUMN created INTEGER")
    c.commit()
    c.close()

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    c = db()
    user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    c.close()
    return user

def create_chat(user_id, title="New chat"):
    c = db()
    cur = c.execute(
        "INSERT INTO chats (user_id,title,created) VALUES (?,?,?)",
        (user_id, title, int(time.time()))
    )
    chat_id = cur.lastrowid
    c.commit()
    c.close()
    return chat_id

setup_database()

BASE_CSS = """
*{box-sizing:border-box}
:root{--bg:#070b14;--panel:rgba(16,23,38,.82);--line:rgba(255,255,255,.10);--text:#f5f8ff;--muted:#9aa8bd;--blue:#4da3ff;--purple:#8b5cf6}
html,body{min-height:100%;margin:0}
body{background:radial-gradient(circle at 15% 10%,rgba(77,163,255,.16),transparent 32%),radial-gradient(circle at 85% 90%,rgba(139,92,246,.14),transparent 35%),linear-gradient(135deg,#050812,#0b1020 55%,#080b14);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif}
button,input{font:inherit}
a{color:#8cc7ff;text-decoration:none}
a:hover{text-decoration:underline}
.page{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:22px;position:relative;overflow:hidden}
.page:before,.page:after{content:"";position:absolute;border-radius:50%;filter:blur(50px);pointer-events:none}
.page:before{width:180px;height:180px;background:rgba(64,156,255,.16);top:8%;left:8%;animation:floatGlow 6s ease-in-out infinite}
.page:after{width:220px;height:220px;background:rgba(139,92,246,.13);bottom:4%;right:5%;animation:floatGlow 7s ease-in-out infinite reverse}
.card{position:relative;z-index:1;width:min(440px,100%);padding:30px;border:1px solid var(--line);border-radius:26px;background:linear-gradient(145deg,rgba(20,29,48,.91),rgba(10,15,27,.88));box-shadow:0 24px 80px rgba(0,0,0,.48),0 0 45px rgba(77,163,255,.08);backdrop-filter:blur(20px);animation:cardIn .5s ease}
.logo-orb{width:76px;height:76px;margin:0 auto 16px;border-radius:50%;background:radial-gradient(circle at 30% 25%,#fff 0 7%,#aee0ff 8%,#55b0ff 36%,#347cff 68%,#163b9e 100%);box-shadow:0 0 18px #55b0ff,0 0 50px rgba(77,163,255,.45);position:relative;animation:orbFloat 3s ease-in-out infinite}
.logo-orb:before,.logo-orb:after{content:"";position:absolute;top:28px;width:7px;height:10px;background:#07111f;border-radius:50%}
.logo-orb:before{left:22px}.logo-orb:after{right:22px}
.logo-mouth{position:absolute;left:50%;top:46px;width:17px;height:9px;border:2px solid #07111f;border-top:0;border-radius:0 0 14px 14px;transform:translateX(-50%)}
h1{text-align:center;margin:0;font-size:30px;letter-spacing:-.6px}
.brand{font-size:13px;text-align:center;color:#7fbfff;margin:7px 0 4px;font-weight:700}
.sub{text-align:center;color:var(--muted);margin:0 0 20px;line-height:1.45}
label{display:block;margin:16px 0 7px;color:#d8e2f1;font-size:14px;font-weight:650}
.field{position:relative}
input{width:100%;padding:14px 15px;border-radius:13px;border:1px solid rgba(255,255,255,.12);background:rgba(4,8,16,.72);color:#fff;outline:none;transition:.2s}
input::placeholder{color:#68778d}
input:focus{border-color:rgba(77,163,255,.8);box-shadow:0 0 0 4px rgba(77,163,255,.11)}
.btn{width:100%;padding:14px 16px;margin-top:20px;border:0;border-radius:14px;background:linear-gradient(135deg,#3185ff,#7657ed);color:#fff;font-weight:800;cursor:pointer;box-shadow:0 9px 25px rgba(65,113,255,.25);transition:transform .16s,box-shadow .16s,filter .16s}
.btn:hover{transform:translateY(-1px);filter:brightness(1.08);box-shadow:0 12px 30px rgba(65,113,255,.34)}
.btn:active{transform:translateY(1px)}
.link{text-align:center;margin-top:20px;color:#9aa8bd;font-size:14px;line-height:1.8}
.link a{font-weight:700}
.divider{height:1px;background:var(--line);margin:20px 0}
.error{background:rgba(190,45,65,.13);border:1px solid rgba(255,93,115,.22);color:#ffb8c2;padding:12px 13px;border-radius:12px;margin:12px 0;line-height:1.4}
.success{background:rgba(45,190,100,.12);border:1px solid rgba(78,220,130,.2);color:#a8f0bf;padding:12px 13px;border-radius:12px;margin:12px 0;line-height:1.4}
.hint{text-align:center;color:#74849a;font-size:12px;margin-top:14px}
@keyframes cardIn{from{opacity:0;transform:translateY(14px) scale(.98)}to{opacity:1;transform:none}}
@keyframes orbFloat{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-7px) rotate(2deg)}}
@keyframes floatGlow{0%,100%{transform:translate(0,0)}50%{transform:translate(18px,-15px)}}
@media(max-width:520px){.page{padding:14px}.card{padding:24px 20px;border-radius:22px}h1{font-size:27px}.logo-orb{width:68px;height:68px}.logo-orb:before,.logo-orb:after{top:25px}.logo-orb:before{left:20px}.logo-orb:after{right:20px}.logo-mouth{top:41px}}
"""

AUTH_HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · Nova AI</title>
<style>""" + BASE_CSS + """</style>
</head>
<body>
<div class="page">
  <div class="card">
    <div class="logo-orb"><span class="logo-mouth"></span></div>
    <h1>Nova AI</h1>
    <div class="brand">✦ Your little AI companion</div>
    <p class="sub">{{ heading }}</p>

    {% if error %}<div class="error">⚠️ {{ error }}</div>{% endif %}
    {% if success %}<div class="success">✓ {{ success }}</div>{% endif %}

    <form method="post">
    {% if signup %}
      <label>Email or iCloud email</label>
      <input type="email" name="email" required autocomplete="email" placeholder="you@example.com">

      <label>Username</label>
      <input name="username" required minlength="3" maxlength="30" autocomplete="username" placeholder="Choose a username">

      <label>Password</label>
      <input type="password" name="password" required minlength="6" autocomplete="new-password" placeholder="Create a password">

      <button class="btn" type="submit">Create my Nova account →</button>
      <div class="hint">Your account and chats are saved securely on this Nova server.</div>

    {% elif forgot %}
      <label>Email</label>
      <input type="email" name="email" required autocomplete="email" placeholder="The email on your account">

      <label>Username</label>
      <input name="username" required autocomplete="username" placeholder="Your Nova username">

      <label>Verification code</label>
      <input name="code" inputmode="numeric" maxlength="6" placeholder="6-digit code from your email">

      <label>New password</label>
      <input type="password" name="password" required minlength="6" autocomplete="new-password" placeholder="Choose a new password">

      <button class="btn" type="submit">Send code / Reset password →</button>

    {% else %}
      <label>Email or username</label>
      <input name="login" required autocomplete="username" placeholder="Enter email or username">

      <label>Password</label>
      <input type="password" name="password" required autocomplete="current-password" placeholder="Enter your password">

      <button class="btn" type="submit">Sign in to Nova →</button>
    {% endif %}
    </form>

    <div class="link">
    {% if signup %}
      Already have an account? <a href="{{ url_for('login') }}">Sign in</a>
    {% elif forgot %}
      Remembered your password? <a href="{{ url_for('login') }}">Sign in</a>
    {% else %}
      New to Nova? <a href="{{ url_for('signup') }}">Create an account</a><br>
      <a href="{{ url_for('forgot_password') }}">Forgot your password?</a>
    {% endif %}
    </div>
  </div>
</div>
</body>
</html>"""


CHAT_HTML = r"""<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Nova AI</title>
<style>
*{box-sizing:border-box}html,body{height:100%}body{margin:0;color:#f7faff;font-family:Inter,system-ui,Arial,sans-serif;background:#050812;overflow:hidden}
button,input,textarea,select{font:inherit}button{cursor:pointer}a{color:#8bc8ff}
body:before{content:"";position:fixed;inset:-25%;pointer-events:none;background:radial-gradient(circle at 15% 10%,rgba(67,130,255,.25),transparent 25%),radial-gradient(circle at 85% 20%,rgba(170,80,255,.16),transparent 22%),radial-gradient(circle at 50% 100%,rgba(0,220,255,.10),transparent 30%);filter:blur(10px);animation:bg 16s ease-in-out infinite alternate}
.app{height:100%;display:flex;position:relative}.drawer-overlay{display:none}.side{width:275px;padding:14px;background:rgba(8,12,22,.82);border-right:1px solid #26334a;backdrop-filter:blur(22px);display:flex;flex-direction:column;gap:8px;z-index:10}
.brand{display:flex;align-items:center;gap:11px;padding:5px 7px 14px}.brandname{font-size:22px;font-weight:900;letter-spacing:.2px}.brand small{color:#8290a8}.user{padding:10px;border:1px solid #26334a;background:rgba(255,255,255,.035);border-radius:15px;color:#8996ac;font-size:12px}.user b{color:#fff}
.nav{display:grid;gap:7px}.nav button,.sidebtn{border:1px solid transparent;background:transparent;color:#bfcbe0;padding:11px 12px;border-radius:13px;text-align:left;transition:.2s}.nav button:hover,.sidebtn:hover{background:#131d2c;border-color:#2d3e58;color:#fff}.nav button.active{background:linear-gradient(135deg,#1a3c70,#17243a);border-color:#3769a5;color:#fff;box-shadow:0 0 25px rgba(60,130,255,.12)}
.chatlabel{margin:10px 8px 2px;color:#64728a;font-size:11px;letter-spacing:1.2px;text-transform:uppercase}.chats{overflow:auto;flex:1}.chatitem{padding:10px;border-radius:11px;color:#aab7cc;margin:3px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.chatitem:hover{background:#141e2e;color:#fff}.chatitem.active{background:#202e43;color:#fff}.sidebottom{display:grid;gap:7px}
.main{flex:1;min-width:0;display:flex;flex-direction:column}.top{height:72px;display:flex;align-items:center;gap:12px;padding:0 16px;border-bottom:1px solid #26334a;background:rgba(5,8,18,.68);backdrop-filter:blur(20px);z-index:5}.title{font-size:18px;font-weight:900}.status{font-size:11px;color:#73e0a0;margin-top:2px}.topspacer{flex:1}.topbtn{border:1px solid #2b3950;background:#101827;color:#d9e4f7;border-radius:12px;padding:9px 11px}
.nova-orb{width:39px;height:39px;border-radius:50%;position:relative;background:radial-gradient(circle at 32% 27%,#fff 0 6%,#a7e8ff 8%,#65baff 35%,#367eff 68%,#173b9c 100%);box-shadow:0 0 12px #5ac1ff,0 0 28px rgba(55,157,255,.7),0 0 48px rgba(55,157,255,.28);animation:float 2.8s ease-in-out infinite,glow 2.2s ease-in-out infinite;flex:none}.nova-orb .eye{position:absolute;top:13px;width:5px;height:7px;background:#07111f;border-radius:50%;animation:blink 4.5s infinite}.nova-orb .left{left:10px}.nova-orb .right{right:10px}.nova-orb .mouth{position:absolute;left:50%;top:22px;width:12px;height:7px;border:2px solid #07111f;border-top:0;border-radius:0 0 12px 12px;transform:translateX(-50%)}.nova-orb.thinking{animation:float .75s ease-in-out infinite,glow .5s ease-in-out infinite}
.view{display:none;flex:1;min-height:0}.view.active{display:flex}.chatview{flex-direction:column}.messages{flex:1;overflow:auto;padding:26px;display:flex;flex-direction:column;gap:14px}.msg{max-width:min(900px,90%);padding:14px 16px;border-radius:18px;line-height:1.55;white-space:pre-wrap;word-break:break-word;box-shadow:0 10px 30px #0003;animation:in .2s ease-out}.usermsg{align-self:flex-end;background:linear-gradient(135deg,#438fff,#2568dd);border-bottom-right-radius:6px}.aimsg{align-self:flex-start;background:linear-gradient(135deg,#202d40,#121a27);border:1px solid #2b3b53;border-bottom-left-radius:6px}.msg img{max-width:320px;max-height:280px;border-radius:14px;margin-top:7px;display:block}.thinkingmsg{align-self:flex-start;color:#a9b9d2;background:#101a29;border:1px solid #2b3b53;border-radius:18px;padding:11px 14px;animation:pulse 1s infinite}.dots{display:inline-flex;gap:4px;margin-left:5px}.dots i{width:5px;height:5px;border-radius:50%;background:#7db6ff;animation:dot 1s infinite}.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}.sourcebox{margin-top:9px;padding-top:8px;border-top:1px solid #34445c;font-size:11px;color:#8fa2bd}.sourcebox a{color:#8bc8ff;text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sourcebox b{color:#b9c8df}@keyframes pulse{50%{opacity:.65}}@keyframes dot{0%,100%{transform:translateY(0);opacity:.45}50%{transform:translateY(-3px);opacity:1}}.empty{margin:auto;color:#738198;text-align:center}
.toolrow{display:flex;gap:7px;padding:0 16px 8px;background:rgba(5,8,18,.9);overflow:auto}.chip{white-space:nowrap;border:1px solid #2a3850;background:#101827;color:#b9c7dc;border-radius:999px;padding:7px 11px;font-size:12px}.chip.active{border-color:#4d91ff;background:#172b4b;color:#fff}.composer{padding:10px 14px 14px;border-top:1px solid #26334a;background:rgba(5,8,18,.92);backdrop-filter:blur(20px)}.composebox{display:flex;gap:8px;align-items:flex-end}.composer textarea{flex:1;min-height:54px;max-height:150px;resize:none;padding:14px;border-radius:18px;border:1px solid #34445e;background:#0d1623;color:#fff;outline:none}.composer textarea:focus{border-color:#5e97ff;box-shadow:0 0 0 3px rgba(76,145,255,.12)}.send{width:76px;height:54px;border:0;border-radius:16px;background:linear-gradient(135deg,#55a0ff,#2869e7);color:#fff;font-weight:900}.attach{width:54px;height:54px;border:1px solid #34445e;background:#101927;color:#fff;border-radius:16px}.preview{display:none;align-items:center;gap:10px;padding:8px 4px}.preview.show{display:flex}.preview img{width:52px;height:52px;object-fit:cover;border-radius:12px;border:1px solid #35465f}.preview span{font-size:12px;color:#9eacc1}.preview button{margin-left:auto;border:0;background:transparent;color:#b8c6da}
.pageview{width:100%;overflow:auto;padding:24px}.hero{max-width:980px;margin:0 auto 18px;padding:24px;border:1px solid #293b55;border-radius:24px;background:linear-gradient(135deg,rgba(27,55,94,.72),rgba(10,16,27,.82));box-shadow:0 18px 55px #0004}.hero h2{margin:0 0 7px;font-size:28px}.hero p{margin:0;color:#9caac0}.panel{max-width:980px;margin:0 auto;padding:16px;border:1px solid #26364d;border-radius:19px;background:rgba(11,17,27,.84)}.row{display:flex;gap:8px}.field,.select{width:100%;padding:12px;border-radius:12px;border:1px solid #34435b;background:#0d1622;color:#fff;outline:none}.primary{border:0;border-radius:12px;padding:12px 16px;background:linear-gradient(135deg,#4e94ff,#2868e6);color:#fff;font-weight:900}.results{display:grid;gap:10px;margin-top:14px}.result{padding:14px;border:1px solid #29394f;border-radius:15px;background:#101a28}.result a{color:#8ed2ff;font-weight:800;text-decoration:none}.result p{margin:6px 0 0;color:#8e9cb1;font-size:13px}.media-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:14px}.media-card{border:1px solid #2a3b52;background:#0f1927;border-radius:16px;padding:12px}.media-card h3{margin:0 0 6px;font-size:14px}.media-card p{margin:0;color:#8795aa;font-size:12px}.media-card a{display:block;margin-top:10px}.codearea{width:100%;min-height:350px;resize:vertical;padding:16px;border-radius:15px;border:1px solid #2d3b50;background:#070c14;color:#e0ecff;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.55;outline:none}.codeactions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.tiny{color:#718097;font-size:12px}
@keyframes float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-5px) rotate(2deg)}}@keyframes glow{0%,100%{filter:brightness(1)}50%{filter:brightness(1.25)}}@keyframes blink{0%,45%,48%,100%{transform:scaleY(1)}46%,47%{transform:scaleY(.12)}}@keyframes in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}@keyframes bg{from{transform:translate3d(-1%,0,0)}to{transform:translate3d(1%,1%,0)}}
@media(max-width:700px){.drawer-overlay{display:block;position:fixed;inset:0;background:rgba(0,0,0,.52);backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:opacity .2s;z-index:9}.drawer-overlay.show{opacity:1;pointer-events:auto}.side{position:absolute;left:0;height:100%;transform:translateX(-101%);transition:transform .24s cubic-bezier(.22,.61,.36,1);box-shadow:18px 0 45px rgba(0,0,0,.45);touch-action:pan-y;z-index:10;will-change:transform}.side.open{transform:translateX(0)}.messages{padding:14px}.pageview{padding:12px}.row{flex-direction:column}.top{padding:0 10px}.topbtn{padding:8px}.msg{max-width:94%}}
</style></head>
<body><div class="app">
<div class="drawer-overlay" id="drawerOverlay" onclick="closeSide()"></div><aside class="side" id="sidebar"><div class="brand"><div class="nova-orb"><span class="eye left"></span><span class="eye right"></span><span class="mouth"></span></div><div><div class="brandname">Nova</div><small>your little AI buddy</small></div></div>
<div class="user">Signed in as <b>{{ username }}</b><br>Role: {{ role }}</div>
<div class="nav"><button class="active" onclick="showView('chat',this)">💬 Chat</button><button onclick="showView('web',this)">🌐 Web & media</button><button onclick="showView('code',this)">💻 Code Lab</button></div>
<div class="chatlabel">Your chats</div><button class="sidebtn" onclick="newChat()">＋ New chat</button><div id="chatList" class="chats"></div><div class="sidebottom"><button class="sidebtn" onclick="clearCurrent()">🗑 Clear chat</button><a class="sidebtn" href="/logout">🚪 Log out</a></div></aside>
<main class="main"><header class="top"><button class="topbtn" onclick="toggleSide()">☰</button><div id="novaOrb" class="nova-orb"><span class="eye left"></span><span class="eye right"></span><span class="mouth"></span></div><div><div id="chatTitle" class="title">Nova AI</div><div class="status">● Online</div></div><div class="topspacer"></div><button class="topbtn" onclick="newChat()">＋</button></header>
<section id="chatView" class="view active chatview"><div id="messages" class="messages"><div class="empty">✨ Welcome to Nova<br><span class="tiny">Ask anything, search the web, upload a photo, or build code.</span></div></div>
<div class="toolrow"><button id="webToggle" class="chip active" onclick="showView('web')">🌐 Web always on</button><button class="chip" onclick="showView('web')">🔎 Search</button><button class="chip" onclick="showView('code')">💻 Code Lab</button></div>
<div class="composer"><div id="preview" class="preview"><img id="previewImg"><span id="previewName"></span><button onclick="clearUpload()">✕</button></div><div class="composebox"><input id="fileInput" type="file" accept="image/*,video/*" hidden onchange="handleUpload(event)"><button class="attach" onclick="document.getElementById('fileInput').click()">📎</button><textarea id="input" placeholder="Message Nova... (or upload a photo/video)" rows="1"></textarea><button class="send" onclick="sendMessage()">Send</button></div></div></section>
<section id="webView" class="view pageview"><div class="hero"><h2>🌐 Nova Web & Media</h2><p>Search current information, then jump straight to video and image searches.</p></div><div class="panel"><div class="row"><input id="webQuery" class="field" placeholder="Search the web..."><button class="primary" onclick="runWebSearch()">Search</button></div><div class="media-grid"><div class="media-card"><h3>▶️ Videos</h3><p>Search YouTube for this topic.</p><a id="videoLink" target="_blank">Open video search</a></div><div class="media-card"><h3>🖼️ Photos</h3><p>Search image results for this topic.</p><a id="imageLink" target="_blank">Open photo search</a></div><div class="media-card"><h3>🔍 AI web research</h3><p>Ask Nova to summarize current results with links.</p><a href="#" onclick="askWebFromBox();return false">Ask Nova</a></div></div><div id="webResults" class="results"><div class="empty">Search results will appear here.</div></div></div></section>
<section id="codeView" class="view pageview"><div class="hero"><h2>💻 Nova Code Lab</h2><p>Generate code, explain it, fix it, and download it. Nova will not execute arbitrary code on your phone.</p></div><div class="panel"><div class="row"><select id="codeLanguage" class="select"><option>Python</option><option>JavaScript</option><option>HTML/CSS</option><option>Lua</option><option>SQL</option><option>Bash</option><option>Java</option><option>C++</option></select><input id="codePrompt" class="field" placeholder="What should I build?"><button class="primary" onclick="generateCode()">Generate</button></div><textarea id="codeOutput" class="codearea" placeholder="Your generated code will appear here..."></textarea><div class="codeactions"><button class="chip" onclick="copyCode()">📋 Copy</button><button class="chip" onclick="downloadCode()">⬇️ Download</button><button class="chip" onclick="explainCode()">💡 Explain</button></div></div></section>
</main></div>
<script>
let currentChatId=null,webMode=true,imageData=null,uploadImages=[],uploadName='';
let drawerTouchStartX=0,drawerTouchStartY=0,drawerTouching=false;
function setSide(open){const side=document.getElementById('sidebar'),overlay=document.getElementById('drawerOverlay');side.classList.toggle('open',open);overlay.classList.toggle('show',open)}
function toggleSide(){setSide(!document.getElementById('sidebar').classList.contains('open'))}
function closeSide(){setSide(false)}
function initDrawerGestures(){
  const side=document.getElementById('sidebar');
  let startX=0,startY=0,tracking=false;
  document.addEventListener('touchstart',e=>{
    if(!e.touches || !e.touches[0]) return;
    const t=e.touches[0];
    startX=t.clientX; startY=t.clientY;
    tracking=(startX<90 || side.classList.contains('open'));
  },{passive:true});
  document.addEventListener('touchend',e=>{
    if(!tracking || !e.changedTouches || !e.changedTouches[0])return;
    tracking=false;
    const t=e.changedTouches[0],dx=t.clientX-startX,dy=t.clientY-startY;
    if(Math.abs(dx)<55 || Math.abs(dx)<Math.abs(dy)*1.15)return;
    const open=side.classList.contains('open');
    if(!open && startX<90 && dx>55)setSide(true);
    else if(open && dx<-55)setSide(false);
  },{passive:true});
}
initDrawerGestures();
function showView(name,btn){document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));document.getElementById(name+'View').classList.add('active');document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));if(btn)btn.classList.add('active');closeSide();}
function toggleWeb(){webMode=true;document.getElementById('webToggle').textContent='🌐 Web always on';document.getElementById('webToggle').classList.add('active');}
function addMessage(role,text,img,sources,sourceLabel){const box=document.getElementById('messages');const e=box.querySelector('.empty');if(e)e.remove();const d=document.createElement('div');d.className='msg '+(role==='user'?'usermsg':'aimsg');if(text){const t=document.createElement('div');t.textContent=text;d.appendChild(t)}if(img){const im=document.createElement('img');im.src=img;d.appendChild(im)}if(role!=='user'){const sb=document.createElement('div');sb.className='sourcebox';const label=sourceLabel||'Nova AI';let html='<b>Source: '+label+'</b>';if(Array.isArray(sources)&&sources.length){html+='<div style="margin-top:5px">';sources.forEach(x=>{html+='<a target="_blank" rel="noopener" href="'+x.url.replace(/"/g,'&quot;')+'">↗ '+(x.title||x.url).replace(/</g,'&lt;')+'</a>'});html+='</div>'}sb.innerHTML=html;d.appendChild(sb)}box.appendChild(d);box.scrollTop=box.scrollHeight}
async function loadChats(openLatest=true){const r=await fetch('/api/chats');if(!r.ok){location.href='/login';return}const chats=await r.json();const list=document.getElementById('chatList');list.innerHTML='';chats.forEach(c=>{const d=document.createElement('div');d.className='chatitem'+(c.id===currentChatId?' active':'');d.textContent=c.title||'New chat';d.onclick=()=>loadChat(c.id);list.appendChild(d)});if(openLatest&&chats.length&&!currentChatId)await loadChat(chats[0].id)}
async function loadChat(id){currentChatId=id;const r=await fetch('/api/chat/'+id);if(!r.ok)return;const data=await r.json();document.getElementById('chatTitle').textContent=data.title||'Nova AI';const box=document.getElementById('messages');box.innerHTML='';data.messages.forEach(m=>addMessage(m.role,m.content));await loadChats(false)}
async function newChat(){const r=await fetch('/api/new-chat',{method:'POST'});const data=await r.json();currentChatId=data.id;document.getElementById('messages').innerHTML='<div class="empty">✨ New Nova chat</div>';document.getElementById('chatTitle').textContent='Nova AI';await loadChats(false)}
async function clearCurrent(){if(!currentChatId)return;if(!confirm('Clear this chat?'))return;await fetch('/api/chat/'+currentChatId+'/clear',{method:'POST'});await loadChat(currentChatId)}
function resizeImageData(img){const max=1600,scale=Math.min(1,max/Math.max(img.width,img.height));const c=document.createElement('canvas');c.width=Math.max(1,Math.round(img.width*scale));c.height=Math.max(1,Math.round(img.height*scale));c.getContext('2d').drawImage(img,0,0,c.width,c.height);return c.toDataURL('image/jpeg',.82)}
function handleUpload(ev){
  const f=ev.target.files[0]; if(!f)return;
  uploadImages=[]; imageData=null; uploadName=f.name;
  if(f.type.startsWith('image/')){
    const reader=new FileReader();
    reader.onload=()=>{const img=new Image();img.onload=()=>{imageData=resizeImageData(img);uploadImages=[imageData];document.getElementById('previewImg').src=imageData;document.getElementById('previewName').textContent=f.name;document.getElementById('preview').classList.add('show')};img.src=reader.result};
    reader.readAsDataURL(f);
    return;
  }
  if(f.type.startsWith('video/')){
    const url=URL.createObjectURL(f), video=document.createElement('video');
    video.muted=true; video.playsInline=true; video.preload='metadata'; video.src=url;
    video.onloadedmetadata=async()=>{
      const duration=video.duration||1, times=[0,Math.max(0.1,duration*.5),Math.max(0.1,duration-.1)];
      const frames=[];
      for(const t of times){
        await new Promise(resolve=>{video.currentTime=t;video.onseeked=resolve});
        const c=document.createElement('canvas'),max=1280,scale=Math.min(1,max/Math.max(video.videoWidth,video.videoHeight));
        c.width=Math.max(1,Math.round(video.videoWidth*scale));c.height=Math.max(1,Math.round(video.videoHeight*scale));
        c.getContext('2d').drawImage(video,0,0,c.width,c.height);frames.push(c.toDataURL('image/jpeg',.78));
      }
      uploadImages=frames; imageData=frames[0]||null;
      document.getElementById('previewImg').src=imageData||'';document.getElementById('previewName').textContent=f.name+' • 3 frames';document.getElementById('preview').classList.add('show');
      URL.revokeObjectURL(url);
    };
    video.onerror=()=>{URL.revokeObjectURL(url);alert('Nova could not read that video. Try another video.');};
    return;
  }
  alert('Please choose a photo or video.');
}
function clearUpload(){imageData=null;uploadImages=[];uploadName='';document.getElementById('fileInput').value='';document.getElementById('preview').classList.remove('show')}
async function sendMessage(){
  const input=document.getElementById('input');const text=input.value.trim();
  if(!text&&!uploadImages.length)return;
  if(!currentChatId)await newChat();
  const sentImages=uploadImages.slice(), sentPreview=imageData;
  const automaticPrompt=!text && sentImages.length ? 'Please look at this uploaded '+(uploadName.toLowerCase().match(/\.(mp4|mov|webm|mkv|avi)$/)?'video':'photo')+' and respond naturally like you would to someone who just sent it. Describe what you can see, point out anything interesting, and mention uncertainty when needed.' : text;
  input.value='';clearUpload();addMessage('user',text,sentPreview);
  const orb=document.getElementById('novaOrb');orb.classList.add('thinking');const box=document.getElementById('messages');const thinking=document.createElement('div');thinking.className='thinkingmsg';thinking.innerHTML='Nova is looking at it<span class="dots"><i></i><i></i><i></i></span>';box.appendChild(thinking);box.scrollTop=box.scrollHeight;
  try{
    const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:currentChatId,message:automaticPrompt,web:true,images:sentImages,preview:sentPreview})});
    const data=await r.json();thinking.remove();addMessage('assistant',data.reply||data.error||'No response.',null,data.sources||[],data.source_label||'OpenRouter AI');
  }catch(e){thinking.remove();addMessage('assistant','I had trouble connecting to Nova.')}
  finally{orb.classList.remove('thinking');await loadChats(false)}
}
async function runWebSearch(){const q=document.getElementById('webQuery').value.trim();if(!q)return;document.getElementById('videoLink').href='https://www.youtube.com/results?search_query='+encodeURIComponent(q);document.getElementById('imageLink').href='https://www.google.com/search?tbm=isch&q='+encodeURIComponent(q);const box=document.getElementById('webResults');box.innerHTML='<div class="empty">Nova is searching...</div>';const r=await fetch('/api/web-search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});const data=await r.json();box.innerHTML='';(data.results||[]).forEach(x=>{const d=document.createElement('div');d.className='result';d.innerHTML='<a target="_blank"></a><p></p>';d.querySelector('a').textContent=x.title;d.querySelector('a').href=x.url;d.querySelector('p').textContent=x.snippet||'';box.appendChild(d)});if(!data.results?.length)box.innerHTML='<div class="result">No direct results returned. Use the video/photo buttons or turn Web ON in Chat.</div>'}
async function askWebFromBox(){const q=document.getElementById('webQuery').value.trim();if(!q)return;showView('chat');document.getElementById('input').value='Search the web for '+q+' and summarize the useful results with links.';webMode=true;await sendMessage()}
async function generateCode(){const p=document.getElementById('codePrompt').value.trim();if(!p)return;const lang=document.getElementById('codeLanguage').value;const out=document.getElementById('codeOutput');out.value='Generating...';document.getElementById('novaOrb').classList.add('thinking');try{const r=await fetch('/api/code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p,language:lang})});const d=await r.json();out.value=d.reply||d.error||'No code returned.'}catch(e){out.value='Code generation failed.'}finally{document.getElementById('novaOrb').classList.remove('thinking')}}
async function explainCode(){const code=document.getElementById('codeOutput').value;if(!code)return;showView('chat');document.getElementById('input').value='Explain this code clearly:\n\n'+code;await sendMessage()}
async function copyCode(){const v=document.getElementById('codeOutput').value;if(v)await navigator.clipboard.writeText(v)}
function downloadCode(){const v=document.getElementById('codeOutput').value;if(!v)return;const lang=document.getElementById('codeLanguage').value.toLowerCase();let ext='txt';if(lang.includes('python'))ext='py';else if(lang.includes('javascript'))ext='js';else if(lang.includes('html'))ext='html';else if(lang.includes('sql'))ext='sql';else if(lang.includes('lua'))ext='lua';else if(lang.includes('bash'))ext='sh';else if(lang.includes('java'))ext='java';else if(lang.includes('c++'))ext='cpp';const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([v],{type:'text/plain'}));a.download='nova_code.'+ext;a.click();URL.revokeObjectURL(a.href)}
document.getElementById('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});document.getElementById('webQuery').addEventListener('keydown',e=>{if(e.key==='Enter')runWebSearch()});document.getElementById('codePrompt').addEventListener('keydown',e=>{if(e.key==='Enter')generateCode()});loadChats();
</script></body></html>"""

@app.route("/")
def home():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user = current_user()
    return render_template_string(
        CHAT_HTML,
        username=user["username"],
        role=user["role"]
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        login_value = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        c = db()
        user = c.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?) OR lower(email)=lower(?)",
            (login_value, login_value)
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            session.clear()
            error = "Incorrect email/username or password."
            c.close()
        else:
            session.clear()
            session["user_id"] = user["id"]
            c.execute(
                "INSERT INTO sessions_log (user_id,login_time) VALUES (?,?)",
                (user["id"], int(time.time()))
            )
            c.commit()
            c.close()
            return redirect(url_for("home"))
    return render_template_string(
        AUTH_HTML, title="Login", heading="Log in to Nova",
        signup=False, forgot=False, error=error, success=None
    )

@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            error = "Please enter a valid email address."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            c = db()
            existing = c.execute("SELECT username,email FROM users WHERE username=? OR lower(email)=lower(?)", (username,email)).fetchone()
            if existing:
                c.close()
                error = "That username or email is already in use. Try a different one or log in."
            else:
                try:
                    cur = c.execute("INSERT INTO users (username,email,password_hash,role,recovery_code,created) VALUES (?,?,?,?,?,?)", (username,email,generate_password_hash(password),"user","",int(time.time())))
                    user_id = cur.lastrowid
                    c.commit(); c.close()
                    chat_id = create_chat(user_id)
                    session.clear(); session["user_id"] = user_id; session["chat_id"] = chat_id
                    return redirect(url_for("home"))
                except sqlite3.IntegrityError:
                    c.rollback(); c.close()
                    error = "That username or email is already in use. Try a different one."
                except Exception:
                    c.rollback(); c.close(); app.logger.exception("Signup error")
                    error = "Could not create the account. Please try again."
    return render_template_string(AUTH_HTML,title="Sign up",heading="Create your Nova account",signup=True,forgot=False,error=error,success=None)

def send_reset_code(email, code):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return False, "Email sending is not configured. Set the SMTP settings first."
    msg = EmailMessage()
    msg["Subject"] = "Nova AI verification code"
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.set_content(f"Your Nova AI password-reset verification code is: {code}\n\nIt expires in 10 minutes. If you did not request this, ignore this email.")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls(); server.login(SMTP_USER, SMTP_PASSWORD); server.send_message(msg)
        return True, None
    except Exception:
        app.logger.exception("Reset email failed")
        return False, "Nova could not send the email. Check the SMTP settings."

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    error = None; success = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        code = request.form.get("code", "").strip()
        password = request.form.get("password", "")
        c = db(); user = c.execute("SELECT id FROM users WHERE username=? AND lower(email)=lower(?)", (username,email)).fetchone(); c.close()
        if not user:
            error = "No account matches that email and username."
        elif not code:
            code = f"{secrets.randbelow(1000000):06d}"
            c = db(); c.execute("DELETE FROM email_codes WHERE email=? AND purpose='reset'",(email,)); c.execute("INSERT INTO email_codes(email,username,code,purpose,expires) VALUES(?,?,?,?,?)",(email,username,code,"reset",int(time.time())+600)); c.commit(); c.close()
            ok,msg=send_reset_code(email,code)
            if ok: success="A 6-digit verification code was sent to your email. Enter it above with your new password."
            else:
                c=db(); c.execute("DELETE FROM email_codes WHERE email=? AND purpose='reset'",(email,)); c.commit(); c.close(); error=msg
        elif len(code)!=6 or not code.isdigit():
            error="Enter the 6-digit verification code."
        elif len(password)<6:
            error="New password must be at least 6 characters."
        else:
            c=db(); row=c.execute("SELECT id FROM email_codes WHERE email=? AND username=? AND code=? AND purpose='reset' AND expires>=? ORDER BY id DESC LIMIT 1",(email,username,code,int(time.time()))).fetchone()
            if not row:
                c.close(); error="That verification code is incorrect or expired."
            else:
                c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(password),user["id"])); c.execute("DELETE FROM email_codes WHERE email=? AND purpose='reset'",(email,)); c.commit(); c.close(); success="Password reset successfully. You can now log in."
    return render_template_string(AUTH_HTML,title="Forgot password",heading="Reset your Nova password",signup=False,forgot=True,error=error,success=success)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/api/chats")
def api_chats():
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    c = db()
    rows = c.execute(
        "SELECT id,title,created FROM chats WHERE user_id=? ORDER BY id DESC",
        (user["id"],)
    ).fetchall()
    c.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/new-chat", methods=["POST"])
def api_new_chat():
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    chat_id = create_chat(user["id"])
    session["chat_id"] = chat_id
    return jsonify({"id": chat_id})

@app.route("/api/chat/<int:chat_id>")
def api_chat(chat_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    c = db()
    chat = c.execute(
        "SELECT * FROM chats WHERE id=? AND user_id=?",
        (chat_id, user["id"])
    ).fetchone()
    if not chat:
        c.close()
        return jsonify({"error": "Chat not found"}), 404
    messages = c.execute(
        "SELECT role,content FROM messages WHERE chat_id=? ORDER BY id",
        (chat_id,)
    ).fetchall()
    c.close()
    return jsonify({
        "id": chat["id"],
        "title": chat["title"],
        "messages": [dict(m) for m in messages]
    })

@app.route("/api/chat/<int:chat_id>/clear", methods=["POST"])
def api_clear(chat_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    c = db()
    c.execute(
        """DELETE FROM messages
           WHERE chat_id=? AND chat_id IN
           (SELECT id FROM chats WHERE id=? AND user_id=?)""",
        (chat_id, chat_id, user["id"])
    )
    c.commit()
    c.close()
    return jsonify({"ok": True})


def openrouter_request(messages, web=False, timeout=90):
    key = os.environ.get("OPENROUTER_KEY")
    if not key:
        return None, "OpenRouter is not configured. Set OPENROUTER_KEY in this Termux session."
    payload = {"model": "openrouter/free", "messages": messages}
    if web:
        payload["plugins"] = [{"id": "web", "max_results": 8}]
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","HTTP-Referer":"http://127.0.0.1:10000","X-Title":"Nova AI"},
            json=payload, timeout=timeout)
        result=response.json()
        if response.status_code!=200:
            return None,"OpenRouter error: "+str(result)
        return result["choices"][0]["message"].get("content", ""),None
    except Exception as exc:
        return None,"Connection error: "+str(exc)

def live_web_search(query, limit=5):
    """Fetch real public search results so Nova can ground web answers."""
    try:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Nova AI)"}, timeout=12)
        if r.status_code != 200:
            return []
        html = r.text
        results = []
        # DuckDuckGo HTML result links have class result__a.
        for m in re.finditer(r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I|re.S):
            href = m.group(1)
            title = re.sub(r'<.*?>', '', m.group(2))
            title = re.sub(r'&(?:amp|quot|lt|gt);', lambda x: {'&amp;':'&','&quot;':'"','&lt;':'<','&gt;':'>'}.get(x.group(0), x.group(0)), title).strip()
            # DuckDuckGo may wrap links in redirect URLs.
            if href.startswith('//duckduckgo.com/l/?') and 'uddg=' in href:
                from urllib.parse import parse_qs, urlparse
                href = parse_qs(urlparse('https:' + href).query).get('uddg', [href])[0]
            if href.startswith('http') and not any(x.get('url') == href for x in results):
                results.append({'title': title, 'url': href})
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []

@app.route("/api/ask", methods=["POST"])
def api_ask():
    user=current_user()
    if not user:return jsonify({"reply":"Please log in first."}),401
    data=request.get_json(silent=True) or {}; message=data.get("message","").strip(); chat_id=data.get("chat_id")
    images=data.get("images") or []
    image=data.get("image")
    if image and not images: images=[image]
    images=[x for x in images if isinstance(x,str) and x.startswith("data:image/")][:4]
    if not message and not images:return jsonify({"reply":"Please type a message or upload a photo/video."})
    try: chat_id=int(chat_id)
    except (TypeError,ValueError): chat_id=None
    c=db()
    if chat_id is None: chat_id=create_chat(user["id"])
    else:
        chat=c.execute("SELECT id FROM chats WHERE id=? AND user_id=?",(chat_id,user["id"])).fetchone()
        if not chat:c.close();return jsonify({"reply":"That chat does not belong to your account."}),403
    stored_text=message if message else "[Photo uploaded]"
    c.execute("INSERT INTO messages (chat_id,role,content,created) VALUES (?,?,?,?)",(chat_id,"user",stored_text,int(time.time())))
    count=c.execute("SELECT COUNT(*) AS n FROM messages WHERE chat_id=?",(chat_id,)).fetchone()["n"]
    if count==1:
        c.execute("UPDATE chats SET title=? WHERE id=? AND user_id=?",(stored_text[:45].replace("\n"," "),chat_id,user["id"]))
    history_rows=c.execute("SELECT role,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 30",(chat_id,)).fetchall()
    memory_rows=c.execute("SELECT memory FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 20",(user["id"],)).fetchall()
    c.commit(); c.close()
    history=[{"role":"system","content":SYSTEM_PROMPT}]
    if memory_rows:
        memory_text="\n".join("- "+r["memory"] for r in reversed(memory_rows))
        history.append({"role":"system","content":"Useful saved memories about this user:\n"+memory_text})
    for row in reversed(history_rows): history.append({"role":row["role"],"content":row["content"]})
    # Replace the last stored text-only user turn with a multimodal turn when a photo is attached.
    if images and history and history[-1]["role"]=="user":
        content=[{"type":"text","text":message or "Please inspect the uploaded media and respond naturally like you would to someone who just sent it. Describe what you can see, and if multiple frames are provided, explain what changes between them."}]
        for img in images:
            content.append({"type":"image_url","image_url":{"url":img}})
        history[-1]["content"]=content
    sources=[]
    # Nova has web access by default for every question.
    sources = live_web_search(message or "latest information", 6)
    source_text = "\n".join(f"- {x['title']} — {x['url']}" for x in sources)
    if source_text:
        history.insert(1,{"role":"system","content":"You have REAL web search results below. Use them to ground your answer. Do not invent sources. If the results do not answer the question, say so.\n\nWEB SOURCES:\n"+source_text})
    else:
        history.insert(1,{"role":"system","content":"Nova attempted a live web search, but no results were retrieved. Do not claim that you found or verified anything online."})
    reply,err=openrouter_request(history,web=False,timeout=120)
    if err: reply=err
    c=db();c.execute("INSERT INTO messages (chat_id,role,content,created) VALUES (?,?,?,?)",(chat_id,"assistant",reply,int(time.time())));c.commit();c.close()
    return jsonify({"reply":reply,"chat_id":chat_id,"sources":sources,"source_label":"Web search" if sources else ("Image/video analysis" if images else "OpenRouter AI")})

@app.route("/api/web-search", methods=["POST"])
def api_web_search():
    user=current_user()
    if not user:return jsonify({"error":"Not logged in"}),401
    q=(request.get_json(silent=True) or {}).get("query","").strip()
    if not q:return jsonify({"results":[]})
    # Let OpenRouter's online router do the live research, then ask it for compact JSON-like results.
    prompt=("Search the web for: "+q+". Return up to 6 useful results. For each result give title, URL, and a one-sentence snippet. "
            "Return ONLY a JSON array of objects with keys title,url,snippet. Do not invent URLs.")
    reply,err=openrouter_request([{"role":"system","content":"You are a web research helper. Use the web tool when available."},{"role":"user","content":prompt}],web=True,timeout=120)
    if err:return jsonify({"results":[],"error":err})
    # Best-effort JSON extraction; if the model adds prose, show the text as one result rather than fabricating links.
    import json
    try:
        raw=reply.strip(); start=raw.find('['); end=raw.rfind(']')
        arr=json.loads(raw[start:end+1]) if start>=0 and end>start else []
        results=[x for x in arr if isinstance(x,dict) and x.get('url')][:8]
        if not results: results=[{"title":"Nova web research","url":"https://www.google.com/search?q="+requests.utils.quote(q),"snippet":reply[:500]}]
    except Exception:
        results=[{"title":"Nova web research","url":"https://www.google.com/search?q="+requests.utils.quote(q),"snippet":reply[:500]}]
    return jsonify({"results":results})

@app.route("/api/code", methods=["POST"])
def api_code():
    user=current_user()
    if not user:return jsonify({"error":"Not logged in"}),401
    data=request.get_json(silent=True) or {};prompt=data.get("prompt","").strip();language=data.get("language","Python").strip() or "Python"
    if not prompt:return jsonify({"error":"Describe what you want to build."}),400
    system="""You are Nova Code Lab, a helpful coding assistant. Generate clean, runnable code. Return the code in exactly one fenced code block, followed by a very short explanation. Do not generate malware, credential stealers, destructive payloads, or code intended to bypass security. Prefer simple code for the stated platform."""
    code_request=f"Language/platform: {language}\nBuild this:\n{prompt}"
    reply,err=openrouter_request([{"role":"system","content":system},{"role":"user","content":code_request}],web=False,timeout=120)
    if err:return jsonify({"error":err}),502
    return jsonify({"reply":reply})

@app.route("/api/memory", methods=["GET", "POST", "DELETE"])
def api_memory():
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401

    c = db()

    if request.method == "GET":
        rows = c.execute(
            "SELECT id,memory,created FROM memories WHERE user_id=? ORDER BY id DESC",
            (user["id"],)
        ).fetchall()
        c.close()
        return jsonify([dict(r) for r in rows])

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        memory = data.get("memory", "").strip()
        if not memory:
            c.close()
            return jsonify({"error": "Memory is empty"}), 400
        c.execute(
            "INSERT INTO memories (user_id,memory,created) VALUES (?,?,?)",
            (user["id"], memory, int(time.time()))
        )
        c.commit()
        c.close()
        return jsonify({"ok": True})

    memory_id = request.args.get("id")
    c.execute(
        "DELETE FROM memories WHERE id=? AND user_id=?",
        (memory_id, user["id"])
    )
    c.commit()
    c.close()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
