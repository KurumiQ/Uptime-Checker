import sqlite3
from datetime import datetime, timedelta
import hashlib

DB_FILE = "monitor.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Sites table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            monitor_type TEXT DEFAULT 'https',
            check_interval INTEGER DEFAULT 20,
            group_name TEXT DEFAULT 'General',
            notes TEXT,
            created_at TEXT,
            position INTEGER DEFAULT 0,
            last_checked TEXT DEFAULT 'Never',
            last_status TEXT DEFAULT 'Unknown',
            status_class TEXT DEFAULT 'badge-warn',
            last_latency INTEGER DEFAULT 0
        )
    """)
    
    # History logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER,
            timestamp TEXT,
            status_msg TEXT,
            status_class TEXT,
            latency INTEGER DEFAULT 0,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE CASCADE
        )
    """)

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            status_page_id INTEGER DEFAULT NULL,
            FOREIGN KEY (status_page_id) REFERENCES status_pages (id) ON DELETE SET NULL
        )
    """)

    # Status Pages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS status_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            custom_css TEXT DEFAULT ''
        )
    """)

    # Mapping table between Status Pages and Monitors
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS status_page_monitors (
            status_page_id INTEGER,
            site_id INTEGER,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (status_page_id) REFERENCES status_pages (id) ON DELETE CASCADE,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE CASCADE
        )
    """)
    
    # Safe migration checks for older existing databases
    try:
        cursor.execute("ALTER TABLE status_page_monitors ADD COLUMN position INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column already exists

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN status_page_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    
    # Ensure default admin account exists
    admin_check = cursor.execute("SELECT * FROM users WHERE role = 'admin'").fetchone()
    if not admin_check:
        default_pass = hash_password("admin123")
        cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')", ("admin", default_pass))
        conn.commit()
        
    conn.close()

def get_all_sites():
    conn = get_db_connection()
    sites = conn.execute("SELECT * FROM sites ORDER BY group_name ASC, name ASC").fetchall()
    conn.close()
    return [dict(s) for s in sites]

def get_existing_groups():
    conn = get_db_connection()
    groups = conn.execute("SELECT DISTINCT group_name FROM sites WHERE group_name IS NOT NULL AND group_name != '' ORDER BY group_name ASC").fetchall()
    conn.close()
    return [g["group_name"] for g in groups]

def add_site_db(name, url, monitor_type, interval, group_name, notes):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    max_pos = conn.execute("SELECT MAX(position) FROM sites").fetchone()[0]
    new_pos = (max_pos or 0) + 1
    conn.execute("""
        INSERT INTO sites (name, url, monitor_type, check_interval, group_name, notes, created_at, position) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, url, monitor_type, interval, group_name or "General", notes[:100] if notes else "", now, new_pos))
    conn.commit()
    conn.close()

def delete_site_db(site_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    conn.execute("DELETE FROM history WHERE site_id = ?", (site_id,))
    conn.commit()
    conn.close()

def get_site_by_id(site_id):
    conn = get_db_connection()
    site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    conn.close()
    return dict(site) if site else None

def update_site_db(site_id, name, new_url, monitor_type, interval, group_name, notes):
    conn = get_db_connection()
    conn.execute("""
        UPDATE sites SET name = ?, url = ?, monitor_type = ?, check_interval = ?, group_name = ?, notes = ? WHERE id = ?
    """, (name, new_url, monitor_type, interval, group_name or "General", notes[:100] if notes else "", site_id))
    conn.commit()
    conn.close()

def update_site_status(site_id, status_msg, status_class, latency):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    conn.execute("""
        UPDATE sites SET last_checked = ?, last_status = ?, status_class = ?, last_latency = ? WHERE id = ?
    """, (now, status_msg, status_class, latency, site_id))
    
    conn.execute("""
        INSERT INTO history (site_id, timestamp, status_msg, status_class, latency) VALUES (?, ?, ?, ?, ?)
    """, (site_id, now, status_msg, status_class, latency))
    
    conn.commit()
    conn.close()

def get_recent_bars(site_id, limit=20):
    conn = get_db_connection()
    bars = conn.execute("""
        SELECT timestamp, status_class FROM history 
        WHERE site_id = ? 
        ORDER BY id DESC LIMIT ?
    """, (site_id, limit)).fetchall()
    conn.close()
    return [{"status_class": b["status_class"], "timestamp": b["timestamp"]} for b in reversed(bars)]

def get_site_history_filtered(site_id, timeframe):
    conn = get_db_connection()
    site = conn.execute("SELECT created_at FROM sites WHERE id = ?", (site_id,)).fetchone()
    created_at_str = site["created_at"] if site and site["created_at"] else "2026-01-01 00:00:00"
    
    try:
        created_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        created_dt = datetime(2026, 1, 1)

    now = datetime.now()
    
    if timeframe == "5h":
        delta = timedelta(hours=5)
        num_bins = 10
    elif timeframe == "1d":
        delta = timedelta(days=1)
        num_bins = 24
    elif timeframe == "1w":
        delta = timedelta(weeks=1)
        num_bins = 14
    elif timeframe == "1m":
        delta = timedelta(days=30)
        num_bins = 30
    elif timeframe == "1y":
        delta = timedelta(days=365)
        num_bins = 12
    else:
        delta = timedelta(days=1)
        num_bins = 24
        
    cutoff_time = max(now - delta, created_dt)
    cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")
    
    logs = conn.execute("""
        SELECT timestamp, status_msg, status_class, latency 
        FROM history WHERE site_id = ? AND timestamp >= ? 
        ORDER BY timestamp ASC
    """, (site_id, cutoff_str)).fetchall()
    conn.close()
    
    if not logs:
        return []
        
    parsed_logs = []
    for l in logs:
        try:
            dt = datetime.strptime(l["timestamp"], "%Y-%m-%d %H:%M:%S")
            parsed_logs.append({
                "dt": dt,
                "timestamp": l["timestamp"],
                "status_class": l["status_class"],
                "latency": l["latency"]
            })
        except Exception:
            continue
            
    if not parsed_logs:
        return []

    actual_span = now - cutoff_time
    bin_duration = actual_span / num_bins if actual_span.total_seconds() > 0 else timedelta(hours=1)
    
    bins = []
    for i in range(num_bins):
        bin_start = cutoff_time + i * bin_duration
        bin_end = bin_start + bin_duration
        
        if timeframe in ["5h", "1d"]:
            label = bin_end.strftime("%H:%M")
        elif timeframe == "1w":
            label = bin_end.strftime("%m-%d %H:%S")
        elif timeframe == "1m":
            label = bin_end.strftime("%m-%d")
        else:
            label = bin_end.strftime("%Y-%m")

        bins.append({
            "start": bin_start,
            "end": bin_end,
            "latencies": [],
            "timestamp": label
        })
        
    for log in parsed_logs:
        for b in bins:
            if b["start"] <= log["dt"] < b["end"] or (b == bins[-1] and log["dt"] <= b["end"]):
                if log["latency"] > 0:
                    b["latencies"].append(log["latency"])
                break
                
    aggregated = []
    for b in bins:
        avg_lat = int(sum(b["latencies"]) / len(b["latencies"])) if b["latencies"] else 0
        aggregated.append({
            "timestamp": b["timestamp"],
            "latency": avg_lat
        })
        
    return aggregated

def get_site_statistics(site_id):
    conn = get_db_connection()
    logs = conn.execute("SELECT timestamp, status_class, latency FROM history WHERE site_id = ?", (site_id,)).fetchall()
    conn.close()

    if not logs:
        return {"uptime_pct": 100.0, "avg_latency": 0, "time_since_down": "No data yet"}

    total_checks = len(logs)
    up_checks = sum(1 for l in logs if l["status_class"] == "badge-up")
    uptime_pct = round((up_checks / total_checks) * 100, 2)

    valid_latencies = [l["latency"] for l in logs if l["latency"] > 0]
    avg_latency = int(sum(valid_latencies) / len(valid_latencies)) if valid_latencies else 0

    last_down_time = None
    for l in reversed(logs):
        if l["status_class"] != "badge-up":
            last_down_time = l["timestamp"]
            break

    if last_down_time:
        try:
            down_dt = datetime.strptime(last_down_time, "%Y-%m-%d %H:%M:%S")
            diff = datetime.now() - down_dt
            days = diff.days
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            parts = []
            if days > 0: parts.append(f"{days}d")
            if hours > 0: parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            time_since_down = " ".join(parts) + " ago"
        except Exception:
            time_since_down = last_down_time
    else:
        time_since_down = "Never down!"

    return {
        "uptime_pct": uptime_pct,
        "avg_latency": avg_latency,
        "time_since_down": time_since_down
    }

# User management helpers
def get_all_users():
    conn = get_db_connection()
    users = conn.execute("""
        SELECT u.id, u.username, u.role, u.status_page_id, sp.title as status_page_title 
        FROM users u 
        LEFT JOIN status_pages sp ON u.status_page_id = sp.id
    """).fetchall()
    conn.close()
    return [dict(u) for u in users]

def get_user_by_id(user_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None

def authenticate_user(username, password):
    conn = get_db_connection()
    p_hash = hash_password(password)
    user = conn.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?", (username, p_hash)).fetchone()
    conn.close()
    return dict(user) if user else None

def add_user_db(username, password, role, status_page_id):
    conn = get_db_connection()
    p_hash = hash_password(password)
    try:
        conn.execute("INSERT INTO users (username, password_hash, role, status_page_id) VALUES (?, ?, ?, ?)", 
                     (username, p_hash, role, status_page_id if status_page_id else None))
        conn.commit()
        success = True
    except Exception:
        success = False
    conn.close()
    return success

def update_user_db(user_id, username, password, role, status_page_id):
    conn = get_db_connection()
    try:
        if password:
            p_hash = hash_password(password)
            conn.execute("UPDATE users SET username = ?, password_hash = ?, role = ?, status_page_id = ? WHERE id = ?", 
                         (username, p_hash, role, status_page_id if status_page_id else None, user_id))
        else:
            conn.execute("UPDATE users SET username = ?, role = ?, status_page_id = ? WHERE id = ?", 
                         (username, role, status_page_id if status_page_id else None, user_id))
        conn.commit()
        success = True
    except Exception:
        success = False
    conn.close()
    return success

def delete_user_db(user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()

# Status Page helpers
def get_all_status_pages():
    conn = get_db_connection()
    pages = conn.execute("SELECT * FROM status_pages ORDER BY title ASC").fetchall()
    conn.close()
    return [dict(p) for p in pages]

def get_status_page_by_slug(slug):
    conn = get_db_connection()
    page = conn.execute("SELECT * FROM status_pages WHERE slug = ?", (slug,)).fetchone()
    if not page:
        conn.close()
        return None
    page_dict = dict(page)
    monitors = conn.execute("""
        SELECT s.*, spm.position as custom_pos FROM sites s
        JOIN status_page_monitors spm ON s.id = spm.site_id
        WHERE spm.status_page_id = ?
        ORDER BY spm.position ASC, s.name ASC
    """, (page_dict["id"],)).fetchall()
    conn.close()
    page_dict["monitors"] = [dict(m) for m in monitors]
    return page_dict

def add_status_page_db(title, slug, description, custom_css, monitor_ids):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO status_pages (title, slug, description, custom_css) VALUES (?, ?, ?, ?)", 
                       (title, slug, description, custom_css))
        page_id = cursor.lastrowid
        for idx, m_id in enumerate(monitor_ids):
            cursor.execute("INSERT INTO status_page_monitors (status_page_id, site_id, position) VALUES (?, ?, ?)", (page_id, m_id, idx))
        conn.commit()
        success = True
    except Exception:
        success = False
    conn.close()
    return success

def update_status_page_db(page_id, title, description, custom_css, monitor_ids):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE status_pages SET title = ?, description = ?, custom_css = ? WHERE id = ?", 
                       (title, description, custom_css, page_id))
        cursor.execute("DELETE FROM status_page_monitors WHERE status_page_id = ?", (page_id,))
        for idx, m_id in enumerate(monitor_ids):
            cursor.execute("INSERT INTO status_page_monitors (status_page_id, site_id, position) VALUES (?, ?, ?)", (page_id, m_id, idx))
        conn.commit()
        success = True
    except Exception:
        success = False
    conn.close()
    return success

def delete_status_page_db(page_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM status_pages WHERE id = ?", (page_id,))
    conn.execute("DELETE FROM status_page_monitors WHERE status_page_id = ?", (page_id,))
    conn.commit()
    conn.close()