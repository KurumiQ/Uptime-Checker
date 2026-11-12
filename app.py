from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import requests
import socket
import subprocess
import platform
import threading
import time
import db

app = Flask(__name__)
app.secret_key = "super_secret_uptime_key_change_me"
db.init_db()

def check_target(url, monitor_type):
    start_time = time.time()
    try:
        if monitor_type in ["http", "https"]:
            if not url.startswith("http://") and not url.startswith("https://"):
                prefix = "https://" if monitor_type == "https" else "http://"
                url = prefix + url
            response = requests.get(url, timeout=4)
            latency = int((time.time() - start_time) * 1000)
            if response.status_code == 200:
                return "UP (200)", "badge-up", latency
            else:
                return f"WARN ({response.status_code})", "badge-warn", latency
                
        elif monitor_type == "tcp":
            if ":" not in url:
                return "INVALID TCP FORMAT", "badge-down", 0
            host, port_str = url.rsplit(":", 1)
            port = int(port_str)
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            latency = int((time.time() - start_time) * 1000)
            sock.close()
            
            if result == 0:
                return "PORT OPEN", "badge-up", latency
            else:
                return "PORT CLOSED", "badge-down", 0
                
        elif monitor_type == "ping":
            target = url.replace("https://", "").replace("http://", "").split("/")[0]
            param = "-n" if platform.system().lower() == "windows" else "-c"
            command = ["ping", param, "1", target]
            
            output = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            latency = int((time.time() - start_time) * 1000)
            if output.returncode == 0:
                return "PING OK", "badge-up", latency
            else:
                return "PING FAILED", "badge-down", 0
    except Exception:
        pass
        
    return "DOWN / UNREACHABLE", "badge-down", 0

def background_scheduler():
    last_checked_map = {}
    while True:
        time.sleep(1) # Base tick loop
        try:
            sites = db.get_all_sites()
            current_time = time.time()
            
            for site in sites:
                site_id = site["id"]
                interval = site["check_interval"]
                last_run = last_checked_map.get(site_id, 0)
                
                # Enforce single check execution per site interval
                if current_time - last_run >= interval:
                    last_checked_map[site_id] = current_time
                    msg, cls, latency = check_target(site["url"], site["monitor_type"])
                    db.update_site_status(site_id, msg, cls, latency)
                    break # Process one check per tick to avoid double-firing
        except Exception as e:
            print(f"Background worker error: {e}")

bg_thread = threading.Thread(target=background_scheduler, daemon=True)
bg_thread.start()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = db.authenticate_user(username, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["status_page_id"] = user["status_page_id"]
            
            if user["role"] == "user":
                if user["status_page_id"]:
                    sp = db.get_all_status_pages()
                    target_page = next((p for p in sp if p["id"] == user["status_page_id"]), None)
                    if target_page:
                        return redirect(url_for("public_status_page", slug=target_page["slug"]))
                return "No status page assigned to your account. Contact Admin.", 403
                
            return redirect(url_for("home"))
        else:
            return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") == "user":
        return "Unauthorized", 403
        
    groups = db.get_existing_groups()
    users = db.get_all_users()
    status_pages = db.get_all_status_pages()
    all_sites = db.get_all_sites()
    return render_template("index.html", groups=groups, users=users, status_pages=status_pages, all_sites=all_sites)

@app.route("/status/<slug>")
def public_status_page(slug):
    page = db.get_status_page_by_slug(slug)
    if not page:
        return "Status page not found", 404
        
    if session.get("role") == "user" and session.get("status_page_id") != page["id"]:
        return "Unauthorized access to this status page", 403
        
    for m in page["monitors"]:
        m["bars"] = db.get_recent_bars(m["id"], limit=24)
        m["stats"] = db.get_site_statistics(m["id"])
        
    is_admin = session.get("role") == "admin"
    all_sites = db.get_all_sites() if is_admin else []
    return render_template("status_page.html", page=page, is_admin=is_admin, all_sites=all_sites)

@app.route("/api/status")
def api_status():
    if "user_id" not in session or session.get("role") == "user":
        return jsonify({"error": "Unauthorized"}), 401
    sites = db.get_all_sites()
    groups = db.get_existing_groups()
    data = []
    for site in sites:
        bars = db.get_recent_bars(site["id"], limit=24)
        stats = db.get_site_statistics(site["id"])
        data.append({
            "id": site["id"],
            "name": site["name"],
            "url": site["url"],
            "monitor_type": site["monitor_type"].upper(),
            "check_interval": site["check_interval"],
            "group_name": site["group_name"] or "",
            "is_ungrouped": not site["group_name"],
            "notes": site["notes"],
            "last_status": site["last_status"],
            "status_class": site["status_class"],
            "last_checked": site["last_checked"],
            "last_latency": site["last_latency"],
            "uptime_pct": stats["uptime_pct"],
            "bars": bars
        })
    return jsonify({"sites": data, "groups": groups})

@app.route("/api/site/<int:site_id>")
def api_site_details(site_id):
    if "user_id" not in session or session.get("role") == "user":
        return jsonify({"error": "Unauthorized"}), 401
    site = db.get_site_by_id(site_id)
    if not site:
        return jsonify({"error": "Not found"}), 404
    timeframe = request.args.get("tf", "1d")
    history = db.get_site_history_filtered(site_id, timeframe)
    stats = db.get_site_statistics(site_id)
    groups = db.get_existing_groups()
    return jsonify({"site": site, "history": history, "stats": stats, "groups": groups})

@app.route("/group/update", methods=["POST"])
def update_group():
    if "user_id" not in session or session.get("role") == "user":
        return redirect(url_for("login"))
    old_name = request.form.get("old_name", "").strip()
    new_name = request.form.get("new_name", "").strip()
    description = request.form.get("description", "").strip()
    monitor_ids = request.form.getlist("monitor_ids")
    
    db.update_group_db(old_name, new_name, description, [int(m) for m in monitor_ids])
    return redirect(url_for("home"))

@app.route("/add", methods=["POST"])
def add_site():
    if "user_id" not in session or session.get("role") == "user": return redirect(url_for("login"))
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    monitor_type = request.form.get("monitor_type", "https")
    group_name = request.form.get("group_name", "").strip()
    notes = request.form.get("notes", "").strip()
    try: interval = int(request.form.get("interval", 20))
    except ValueError: interval = 20
        
    if name and url:
        db.add_site_db(name, url, monitor_type, interval, group_name, notes)
    return redirect(url_for("home"))

@app.route("/delete/<int:site_id>")
def delete_site(site_id):
    if "user_id" not in session or session.get("role") == "user": return redirect(url_for("login"))
    db.delete_site_db(site_id)
    return redirect(url_for("home"))

@app.route("/update/<int:site_id>", methods=["POST"])
def update_site(site_id):
    if "user_id" not in session or session.get("role") == "user": return redirect(url_for("login"))
    name = request.form.get("name", "").strip()
    new_url = request.form.get("url", "").strip()
    monitor_type = request.form.get("monitor_type", "https")
    group_name = request.form.get("group_name", "").strip()
    notes = request.form.get("notes", "").strip()
    try: interval = int(request.form.get("interval", 20))
    except ValueError: interval = 20
        
    if name and new_url:
        db.update_site_db(site_id, name, new_url, monitor_type, interval, group_name, notes)
    return redirect(url_for("home"))

@app.route("/user/add", methods=["POST"])
def add_user():
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "user")
    status_page_id = request.form.get("status_page_id")
    if username and password:
        db.add_user_db(username, password, role, status_page_id if status_page_id else None)
    return redirect(url_for("home"))

@app.route("/user/update/<int:user_id>", methods=["POST"])
def update_user(user_id):
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "user")
    status_page_id = request.form.get("status_page_id")
    if username:
        db.update_user_db(user_id, username, password, role, status_page_id if status_page_id else None)
    return redirect(url_for("home"))

@app.route("/user/delete/<int:user_id>")
def delete_user(user_id):
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    db.delete_user_db(user_id)
    return redirect(url_for("home"))

@app.route("/status-page/add", methods=["POST"])
def add_status_page():
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    title = request.form.get("title", "").strip()
    slug = request.form.get("slug", "").strip().lower().replace(" ", "-")
    description = request.form.get("description", "").strip()
    custom_css = request.form.get("custom_css", "").strip()
    footer_text = request.form.get("footer_text", "").strip()
    logo_url = request.form.get("logo_url", "").strip()
    monitor_ids = request.form.getlist("monitor_ids")
    if title and slug:
        db.add_status_page_db(title, slug, description, custom_css, footer_text, logo_url, [int(m) for m in monitor_ids])
    return redirect(url_for("home"))

@app.route("/status-page/update/<int:page_id>", methods=["POST"])
def update_status_page(page_id):
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    custom_css = request.form.get("custom_css", "").strip()
    footer_text = request.form.get("footer_text", "").strip()
    logo_url = request.form.get("logo_url", "").strip()
    monitor_ids = request.form.getlist("monitor_ids")
    if title:
        db.update_status_page_db(page_id, title, description, custom_css, footer_text, logo_url, [int(m) for m in monitor_ids])
    return redirect(request.referrer or url_for("home"))

@app.route("/status-page/delete/<int:page_id>")
def delete_status_page(page_id):
    if "user_id" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    db.delete_status_page_db(page_id)
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)