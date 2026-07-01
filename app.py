from flask import Flask, render_template, request, redirect, session, url_for, flash, make_response
import sqlite3
import re
import os
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Malaysia timezone (UTC+8)
MALAYSIA_TZ = timezone(timedelta(hours=8))

def malaysia_now():
    """Return current datetime in Malaysia timezone."""
    return datetime.now(MALAYSIA_TZ)

# Multi-template/static support:
# This repo contains multiple nested versions of templates/static.
# Configure Flask to search all of them so routes like /login reliably find templates.
from jinja2 import ChoiceLoader, FileSystemLoader

repo_root = os.path.dirname(__file__)

# Template search order:
# 1. smart campus/templates  - admin panel pages (base.html, admin_*.html, etc.)
# 2. templates               - public website pages (index, facilities, bookings, contact)
# 3. smart-hub/templates     - student hub pages (dashboard, profile, register)
# 4. smart campus/smart campus/templates - extra admin pages (notifications, penalties, etc.)
template_paths = [
    os.path.join(repo_root, "smart campus", "templates"),
    os.path.join(repo_root, "templates"),
    os.path.join(repo_root, "smart-hub", "templates"),
    os.path.join(repo_root, "smart campus", "smart campus", "templates"),
]

template_paths = [p for p in template_paths if os.path.isdir(p)]

# Single unified static folder — all CSS/JS/images copied here.
main_static_folder = os.path.join(repo_root, "static")

app = Flask(
    __name__,
    template_folder=os.path.join(repo_root, "templates"),
    static_folder=main_static_folder,
)

# Override Jinja loader to search all template folders.
app.jinja_loader = ChoiceLoader([FileSystemLoader(p) for p in template_paths])


app.secret_key = "smartcampus_secret_key"



# ============================================================
# DATABASE
# ============================================================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Users table (combined: supports admin + student roles)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT DEFAULT 'student'
    )
    """)

    # Facilities table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS facilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        location TEXT,
        description TEXT,
        image_filenames TEXT DEFAULT '',
        category TEXT DEFAULT 'others',
        capacity INTEGER DEFAULT 0,
        view_360_filename TEXT DEFAULT ''
    )
    """)
    # Migrate existing DBs: add columns if they don't exist yet
    for col, definition in [
        ("category", "TEXT DEFAULT 'others'"),
        ("capacity", "INTEGER DEFAULT 0"),
        ("view_360_filename", "TEXT DEFAULT ''"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE facilities ADD COLUMN {col} {definition}")
        except Exception:
            pass  # Column already exists

    # Bookings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        facility_id INTEGER,
        date TEXT,
        time TEXT,
        status TEXT,
        is_approved INTEGER DEFAULT 0
    )
    """)

    # Booking reminder notifications (15 minutes before start)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS booking_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        booking_id INTEGER,
        remind_at TEXT,
        message TEXT,
        is_sent INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now', '+8 hours'))
    )
    """)


    # Today's Schedule table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS todays_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        time TEXT,
        title TEXT,
        location TEXT,
        notes TEXT
    )
    """)

    # Contact messages table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contact_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        message TEXT,
        created_at TEXT DEFAULT (datetime('now', '+8 hours'))
    )
    """)

    conn.commit()
    conn.close()


def seed_data():
    conn = get_db()
    cursor = conn.cursor()

    # Seed admin user
    cursor.execute("SELECT * FROM users WHERE username = ?", ("admin",))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (name, username, email, password, role) VALUES (?, ?, ?, ?, ?)",
            ("Administrator", "admin", "admin@smartcampus.com", generate_password_hash("admin123"), "admin")
        )

    # Seed sample facilities
    cursor.execute("SELECT * FROM facilities")
    if not cursor.fetchall():
        facilities = [
            ("Library Study Room", "Main Campus", "Quiet space", ""),
            ("Computer Lab", "Block A", "PCs available", ""),
            ("Sports Hall", "Sports Complex", "Indoor court", "")
        ]
        cursor.executemany(
            "INSERT INTO facilities (name, location, description, image_filenames) VALUES (?, ?, ?, ?)",
            facilities
        )

    conn.commit()
    conn.close()


init_db()
seed_data()


# ============================================================
# HELPERS
# ============================================================
def is_admin():
    return "user_id" in session and session.get("role") == "admin"

def is_logged_in():
    return "user_id" in session

def require_admin():
    """Guard for admin-only routes. Clears session and redirects to admin login if not an admin."""
    if "user_id" not in session:
        flash("Please log in as admin.")
        return redirect('/admin-login')
    if session.get("role") != "admin":
        session.clear()
        session.modified = True
        flash("Access denied. Please log in as admin.")
        return redirect('/admin-login')
    return None

def require_student():
    """Guard for student-only routes. Clears session and redirects to login if not a student."""
    if "user_id" not in session:
        flash("Please log in to continue.")
        return redirect('/login')
    if session.get("role") == "admin":
        session.clear()
        session.modified = True
        flash("Access denied. Please log in as a student.")
        return redirect('/login')
    return None


# ============================================================
# NOTIFICATIONS (in-app reminders)
# ============================================================

def parse_booking_datetime(date_str: str, time_str: str) -> datetime | None:
    """Parse booking date/time from DB into a datetime."""
    try:
        # Expected DB formats: date = YYYY-MM-DD, time = HH:MM
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def ensure_notification_scheduler():
    """Start a lightweight background loop once per process."""
    # Prevent multiple loops if Flask reloads in debug mode.
    if getattr(app, "_notification_scheduler_started", False):
        return
    app._notification_scheduler_started = True

    import threading

    import time as _time

    def worker():
        while True:
            try:
                now = malaysia_now()
                conn = get_db()
                cur = conn.cursor()
                rows = cur.execute(
                    """
                    SELECT id, user_id, booking_id, remind_at, message
                    FROM booking_notifications
                    WHERE is_sent=0 AND remind_at <= ?
                    """,
                    (now.strftime("%Y-%m-%d %H:%M"),)
                ).fetchall()

                for r in rows:
                    # Mark sent
                    cur.execute(
                        "UPDATE booking_notifications SET is_sent=1 WHERE id=?",
                        (r["id"],)
                    )
                    # In this simplified repo we show the reminder directly
                    # from booking_notifications (no separate user_notifications table).
                conn.commit()
                conn.close()
            except Exception:
                # Keep worker alive even if something fails.
                pass

            _time.sleep(30)

    t = threading.Thread(target=worker, daemon=True)
    t.start()


@app.route('/notifications')
def notifications():
    guard = require_student()
    if guard: return guard

    conn = get_db()
    user_id = session.get('user_id')
    notes = conn.execute(
        """
        SELECT id, booking_id, remind_at, message, is_sent, created_at
        FROM booking_notifications
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT 30
        """,
        (user_id,)
    ).fetchall()
    conn.close()
    name = session.get('username', 'User')
    notif_count = len([n for n in notes if not n['is_sent']])
    return render_template('notifications.html', notes=notes, name=name, notification_count=notif_count)


# ============================================================
# MAIN DASHBOARD ROUTES (scf hub - Harinitha)
# ============================================================
@app.route('/')
def index():
    today = malaysia_now().date().isoformat()
    conn = get_db()

    total_bookings = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    avg_booking_time = 120
    total_users = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='student'"
    ).fetchone()[0]

    facilities_raw = conn.execute("""
        SELECT f.id, f.name, f.description, f.category, f.capacity,
               COUNT(b.id) AS booked_today
        FROM facilities f
        LEFT JOIN bookings b
               ON b.facility_id = f.id
              AND b.date = ?
              AND b.status != 'cancelled'
        GROUP BY f.id
        ORDER BY f.id
        LIMIT 4
    """, (today,)).fetchall()
    conn.close()

    _icons = {
        'sport': '🏀', 'court': '🏀', 'badminton': '🏸', 'tennis': '🎾',
        'meeting': '🏢', 'conference': '🏢', 'study': '📚', 'library': '📖',
        'lab': '🔬', 'computer': '💻', 'hall': '🎟️', 'event': '🎟️',
        'seminar': '🎙️', 'auditorium': '🎭', 'gym': '🏋️', 'pool': '🏊',
    }

    def _icon(cat):
        c = (cat or '').lower()
        for k, v in _icons.items():
            if k in c:
                return v
        return '🏛️'

    def _badge(capacity, booked):
         # If capacity isn't set or is 0, consider it closed
         if not capacity:
             return "Closed"
         # Calc available slots
         avail = capacity - booked
         if avail <= 0:
             return 'Closed'
         else:
             return 'Open'

    total_capacity = sum(f['capacity'] or 0 for f in facilities_raw)

    facility_cards = [
        {
            'id': f['id'],
            'name': f['name'],
            'description': (f['description'] or '')[:60],
            'icon': _icon(f['category']),
            'badge': _badge(f['capacity'], f['booked_today']),
            'full': f['booked_today'] >= f['capacity'] if f['capacity'] else False,
        }
        for f in facilities_raw
    ]

    return render_template('index.html',
        total_bookings=total_bookings,
        spaces_available=total_capacity or len(facility_cards),
        total_users=total_users,
        facility_cards=facility_cards,
    )


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()
        if name and email and message:
            conn = get_db()
            conn.execute(
                "INSERT INTO contact_messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
                (name, email, message, malaysia_now().strftime("%Y-%m-%d %H:%M"))
            )
            conn.commit()
            conn.close()
            flash("Your message has been sent! We'll get back to you soon.")
        else:
            flash("Please fill in all fields.")
        return redirect(url_for('contact'))
    return render_template('contact.html')

@app.route('/facilities')
def facilities():
    conn = get_db()
    facilities_list = conn.execute("SELECT id, name, description, location, image_filenames, category FROM facilities ORDER BY id").fetchall()
    conn.close()
    return render_template('facilities.html', facilities=facilities_list)

@app.route('/bookings_360')
def bookings_360():
    conn = get_db()
    facilities = conn.execute("SELECT id, name FROM facilities ORDER BY id").fetchall()
    conn.close()
    return render_template('bookings.html', facilities=facilities)


@app.route('/bookings')
def my_bookings():
    """Student: view own bookings (uses smart-hub bookings.html via ChoiceLoader)."""
    guard = require_student()
    if guard: return guard
    conn = get_db()
    user_id = session.get('user_id')
    rows = conn.execute("""
        SELECT b.id, f.name AS facility_title, b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN facilities f ON b.facility_id = f.id
        WHERE b.user_id = ?
        ORDER BY b.id DESC
    """, (user_id,)).fetchall()
    notif_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (user_id,)
    ).fetchone()["cnt"]
    conn.close()
    # Build list of dicts with created_at fallback for the template
    bookings = [dict(r) | {'created_at': f"{r['date']} {r['time']}"} for r in rows]
    return render_template('smart_hub_bookings.html', name=session.get('user_name', 'User'), bookings=bookings, notification_count=notif_count)


@app.route('/booking/cancel/<int:id>', methods=['POST'])
def cancel_booking(id):
    guard = require_student()
    if guard: return guard
    conn = get_db()
    user_id = session.get('user_id')
    # Only allow cancellation of own bookings
    conn.execute(
        "UPDATE bookings SET status='canceled' WHERE id=? AND user_id=?",
        (id, user_id)
    )
    conn.commit()
    conn.close()
    flash("Booking cancelled.")
    return redirect('/bookings')

@app.route('/book/<int:facility_id>', methods=['GET', 'POST'])
def facility_booking(facility_id):
    if not session.get('user_id'):
        flash("Please log in to book a facility.")
        return redirect(url_for('login'))
    conn = get_db()
    facility_row = conn.execute(
        "SELECT id, name FROM facilities WHERE id=?", (facility_id,)
    ).fetchone()
    if facility_row is None:
        conn.close()
        flash("Facility not found.")
        return redirect('/dashboard')
    facility = facility_row["name"]

    # NOTE: This repo's facility booking page is a demo, but to support
    # booking reminders we must persist booking + create notification rows.
    if request.method == "POST":
        # Expect fields from templates/facility_booking.html
        booking_date = request.form.get("date")
        duration_from = request.form.get("duration_from")
        duration_to = request.form.get("duration_to")

        student_name = request.form.get("student_name")
        student_id = request.form.get("student_id")
        purpose = request.form.get("purpose")  # not stored currently

        # Basic validation
        if not booking_date or not duration_from:
            flash("Please provide booking date and start time")
            return redirect(url_for("facility_booking", facility_id=facility_id))

        # Resolve current logged-in user; if not logged in, best-effort by student_id
        user_id = session.get("user_id")
        if user_id is None:
            username = (student_id or student_name or "student").strip() or "student"
            existing = conn.execute(
                "SELECT id FROM users WHERE username=?", (username,)
            ).fetchone()
            if existing is None:
                hashed_pw = generate_password_hash("temp123")
                conn.execute(
                    "INSERT INTO users (name, username, email, password, role) VALUES (?, ?, ?, ?, ?)",
                    (student_name or username, username, f"{username}@local", hashed_pw, "student")
                )
                conn.commit()
                existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            user_id = existing["id"]

        booking_time = duration_from

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bookings (user_id, facility_id, date, time, status, is_approved) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, facility_id, booking_date, booking_time, "pending", 0)
        )
        booking_id = cur.lastrowid
        conn.commit()

        dt = datetime.strptime(f"{booking_date} {booking_time}", "%Y-%m-%d %H:%M")
        remind_at_dt = dt - timedelta(minutes=15)
        remind_at = remind_at_dt.strftime("%Y-%m-%d %H:%M")

        message = f"Reminder: your booking at {booking_time} for {facility} starts in 15 minutes."
        conn.execute(
            "INSERT INTO booking_notifications (user_id, booking_id, remind_at, message, is_sent, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, booking_id, remind_at, message, malaysia_now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        conn.close()

        flash("Booking submitted! You will receive a reminder 15 minutes before start.")
        return redirect('/bookings')

    conn.close()
    return render_template('facility_booking.html',
        facility=facility,
        facility_id=facility_id,
        user_name=session.get('user_name', ''),
        notification_count=session.get('notification_count', 0),
    )



# ============================================================
# STUDENT ROUTES (smartcampus  - Bavisshaa)
# ============================================================
@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        if not name or not email or not password:
            flash("Please fill all fields")
            return redirect("/register")

        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash("Invalid email format")
            return redirect("/register")

        hashed = generate_password_hash(password)

        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO users (name, username, email, password, role) VALUES (?, ?, ?, ?, ?)",
                (name, name, email, hashed, "student")
            )
            conn.commit()
            conn.close()
            flash("Register success! Please login.")
            return redirect("/login")
        except:
            flash("Email already exists")
            return redirect("/register")

    return render_template("register.html")


@app.route('/admin-login', methods=["GET", "POST"])
def admin_login():
    if is_logged_in():
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="admin")
    return render_template("admin_login.html")


@app.route('/user-login', methods=["GET", "POST"])
def user_login():
    if is_logged_in():
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="student")
    return render_template("user_login.html")


def _handle_login(request, intended_role=None):
    """Shared login logic used by both /login and /admin-login and /user-login."""
    identifier = request.form.get("username") or request.form.get("email")
    password = request.form.get("password")

    if not identifier or not password:
        flash("Please enter your credentials")
        return redirect("/admin-login" if intended_role == "admin" else "/login")

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? OR email=?",
        (identifier, identifier)
    ).fetchone()
    conn.close()

    if user:
        try:
            password_match = check_password_hash(user["password"], password)
        except Exception:
            password_match = (password == user["password"])

        if password_match:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"] or user["username"]
            session["role"] = user["role"]
            flash("Login successful!")
            return redirect("/admin" if user["role"] == "admin" else "/dashboard")
        else:
            flash("Incorrect password")
    else:
        flash("Account not found")

    return redirect("/admin-login" if intended_role == "admin" else "/login")


@app.route('/login', methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="student")
    return render_template("user_login.html")






@app.route('/dashboard')
def dashboard():
    guard = require_student()
    if guard: return guard

    conn = get_db()
    user_id = session.get('user_id')
    notification_count = conn.execute(
        """
        SELECT COUNT(*) as cnt
        FROM booking_notifications
        WHERE user_id=? AND is_sent=0
        """,
        (user_id,)
    ).fetchone()["cnt"]
    facilities = conn.execute(
        "SELECT id, name, location, description, image_filenames, category, capacity, view_360_filename FROM facilities"
    ).fetchall()
    conn.close()

    return render_template("dashboard.html",
        name=session.get('user_name', 'User'),
        notification_count=notification_count,
        facilities=facilities,
    )



@app.route('/help')
def help_page():
    guard = require_student()
    if guard: return guard
    name = session.get('username', 'User')
    conn = get_db()
    notif_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (session.get('user_id'),)
    ).fetchone()["cnt"]
    conn.close()
    return render_template('help.html', name=name, notification_count=notif_count)


@app.route('/profile', methods=["GET", "POST"])
def profile():
    guard = require_student()
    if guard: return guard

    if request.method == "POST":
        session['full_name'] = request.form.get('full_name') or session.get('user_name')
        session['student_id'] = request.form.get('student_id') or ''
        session['email'] = request.form.get('email') or ''
        session['phone'] = request.form.get('phone') or ''
        session['department'] = request.form.get('department') or ''
        session['booking_duration'] = request.form.get('booking_duration') or '1'
        flash("Profile saved")
        return redirect('/profile')

    conn2 = get_db()
    notif_count = conn2.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (session.get('user_id'),)
    ).fetchone()["cnt"]
    conn2.close()
    return render_template(
        'profile.html',
        name=session.get('user_name', 'User'),
        full_name=session.get('full_name', session.get('user_name', '')),
        student_id=session.get('student_id', ''),
        email=session.get('email', ''),
        phone=session.get('phone', ''),
        department=session.get('department', ''),
        booking_duration=int(session.get('booking_duration', 1) or 1),
        photo_url='',
        notification_count=notif_count,
    )


@app.route('/logout')
def logout():
    session.clear()
    session.modified = True
    resp = make_response(redirect("/login"))
    resp.delete_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    flash("You have been logged out.")
    return resp


# ============================================================
# ADMIN ROUTES (smart campus - bavisshaa)
# ============================================================
@app.route("/admin")
def admin_dashboard():
    guard = require_admin()
    if guard: return guard

    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()
    pending_approvals_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE is_approved = 0"
    ).fetchone()["cnt"]

    bookings_dashboard = conn.execute("""
        SELECT b.id, u.username, f.name AS facility_name,
               b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        ORDER BY b.id DESC
    """).fetchall()
    conn.close()

    return render_template(
        "admin_dashboard.html",
        users=users,
        bookings=bookings_dashboard,
        facilities=facilities,
        pending_approvals_count=pending_approvals_count,
    )


@app.route("/admin/users")
def admin_users():
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return render_template("admin_users.html", users=users)


@app.route("/admin/bookings")
def admin_bookings():
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    bookings = conn.execute("""
        SELECT b.id, u.username, f.name as facility_name,
               b.user_id, b.facility_id, b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
    """).fetchall()
    conn.close()
    return render_template("admin_bookings.html", bookings=bookings)


@app.route("/admin/edit-booking/<int:id>", methods=["GET", "POST"])
def edit_booking(id):
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    if request.method == "POST":
        is_approved = 1 if request.form.get('is_approved') == 'on' else 0
        conn.execute("""
            UPDATE bookings SET date=?, time=?, status=?, is_approved=? WHERE id=?
        """, (request.form["date"], request.form["time"], request.form["status"], is_approved, id))
        conn.commit()
        conn.close()
        flash("Booking updated!")
        return redirect("/admin/bookings")

    booking = conn.execute("""
        SELECT b.*, u.username, f.name as facility_name
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        WHERE b.id=?
    """, (id,)).fetchone()
    conn.close()
    return render_template("edit_booking.html", booking=booking)


@app.route("/admin/approve-booking/<int:id>", methods=["POST", "GET"])
def approve_booking(id):
    guard = require_admin()
    if guard: 
        return guard
    
    conn = get_db()
    conn.execute("UPDATE bookings SET is_approved=1 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    
    flash("Booking approved!")
    return redirect("/admin/bookings")

@app.route("/admin/reject-booking/<int:id>", methods=["POST"])
def reject_booking(id):
    guard = require_admin()
    if guard: 
        return guard
    
    conn = get_db()
    conn.execute("UPDATE bookings SET is_approved=0, status='rejected' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    
    flash("Booking rejected!")
    return redirect("/admin/bookings")


@app.route("/admin/facilities")
def admin_facilities():
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()

    # Add badge info for each facility
    facilities_with_badges = []
    for f in facilities:
        booked_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM bookings WHERE facility_id=? AND is_approved=1",
            (f["id"],)
        ).fetchone()["cnt"]
        badge = _badge(f["capacity"], booked_count)
        facilities_with_badges.append({**f, "badge": badge})

    conn.close()
    return render_template("admin_facilities.html", facilities=facilities_with_badges)


@app.route("/admin/add-facility", methods=["GET", "POST"])
def add_facility():
    guard = require_admin()
    if guard: return guard

    if request.method == "POST":
        name = request.form["name"]
        location = request.form["location"]
        description = request.form["description"]
        category = request.form.get("category", "others")
        capacity = request.form.get("capacity", 0)
        image_filenames = []
        upload_dir = os.path.join(app.static_folder, "facility_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        allowed_ext = {"png", "jpg", "jpeg", "webp"}

        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        if files:
            for f in files:
                if not f or not f.filename:
                    continue
                ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
                if ext not in allowed_ext:
                    continue
                filename = secure_filename(f"{name}_{len(image_filenames)+1}.{ext}")
                f.save(os.path.join(upload_dir, filename))
                image_filenames.append(filename)

        # 360°: URL link takes priority over file upload
        view_360_url = request.form.get("view_360_url", "").strip()
        if view_360_url and view_360_url.startswith("http"):
            view_360_filename = view_360_url
        else:
            view_360_filename = ""
            f360 = request.files.get("view_360_file")
            if f360 and f360.filename:
                ext = f360.filename.rsplit(".", 1)[-1].lower() if "." in f360.filename else ""
                if ext in allowed_ext:
                    view_360_filename = secure_filename(f"{name}_360.{ext}")
                    f360.save(os.path.join(upload_dir, view_360_filename))

        conn = get_db()
        conn.execute(
            "INSERT INTO facilities (name, location, description, image_filenames, category, capacity, view_360_filename) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, location, description, ",".join(image_filenames), category, capacity, view_360_filename)
        )
        conn.commit()
        conn.close()
        flash("Facility added!")
        return redirect("/admin/facilities")

    return render_template("add_facility.html")


@app.route("/admin/edit-facility/<int:id>", methods=["GET", "POST"])
def edit_facility(id):
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    if request.method == "POST":
        name = request.form["name"]
        location = request.form["location"]
        description = request.form["description"]
        category = request.form.get("category", "others")
        capacity = request.form.get("capacity", 0)
        upload_dir = os.path.join(app.static_folder, "facility_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        allowed_ext = {"png", "jpg", "jpeg", "webp"}

        # Add new gallery images
        existing = conn.execute("SELECT image_filenames FROM facilities WHERE id=?", (id,)).fetchone()
        image_filenames = [x for x in (existing["image_filenames"] or "").split(",") if x]
        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        if files:
            for f in files:
                if not f or not f.filename:
                    continue
                ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
                if ext not in allowed_ext:
                    continue
                filename = secure_filename(f"{name}_{len(image_filenames)+1}.{ext}")
                f.save(os.path.join(upload_dir, filename))
                image_filenames.append(filename)

        # Handle 360°: URL link takes priority, then file upload, then keep existing
        existing_360 = conn.execute("SELECT view_360_filename FROM facilities WHERE id=?", (id,)).fetchone()
        view_360_filename = (existing_360["view_360_filename"] or "") if existing_360 else ""
        view_360_url = request.form.get("view_360_url", "").strip()
        if view_360_url and view_360_url.startswith("http"):
            view_360_filename = view_360_url
        else:
            f360 = request.files.get("view_360_file")
            if f360 and f360.filename:
                ext = f360.filename.rsplit(".", 1)[-1].lower() if "." in f360.filename else ""
                if ext in allowed_ext:
                    view_360_filename = secure_filename(f"{name}_360.{ext}")
                    f360.save(os.path.join(upload_dir, view_360_filename))

        conn.execute("""
            UPDATE facilities SET name=?, location=?, description=?, image_filenames=?, category=?, capacity=?, view_360_filename=? WHERE id=?
        """, (name, location, description, ",".join(image_filenames), category, capacity, view_360_filename, id))
        conn.commit()
        conn.close()
        flash("Updated successfully!")
        return redirect("/admin/facilities")

    facility = conn.execute("SELECT * FROM facilities WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template("edit_facility.html", facility=facility)


@app.route("/admin/delete-facility/<int:id>")
def delete_facility(id):
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    conn.execute("DELETE FROM facilities WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Deleted successfully!")
    return redirect("/admin/facilities")


@app.route("/admin/schedule")
def admin_schedule_alias():
    guard = require_admin()
    if guard: return guard
    return redirect("/admin/todays-schedule")


@app.route("/admin/todays-schedule")
def todays_schedule():
    guard = require_admin()
    if guard: return guard
    selected_date = request.args.get("date") or malaysia_now().strftime("%Y-%m-%d")
    conn = get_db()
    schedules = conn.execute(
        "SELECT * FROM todays_schedule WHERE date=? ORDER BY time ASC", (selected_date,)
    ).fetchall()
    conn.close()
    return render_template("admin_schedule.html", schedules=schedules, selected_date=selected_date)


@app.route("/admin/todays-schedule/add", methods=["POST"])
def todays_schedule_add():
    guard = require_admin()
    if guard: return guard
    date = request.form["date"]
    time = request.form["time"]
    title = request.form["title"]
    location = request.form.get("location", "")
    notes = request.form.get("notes", "")
    conn = get_db()
    conn.execute(
        "INSERT INTO todays_schedule (date, time, title, location, notes) VALUES (?, ?, ?, ?, ?)",
        (date, time, title, location, notes)
    )
    conn.commit()
    conn.close()
    flash("Schedule added!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/edit/<int:id>")
def todays_schedule_edit(id):
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    item = conn.execute("SELECT * FROM todays_schedule WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template("edit_todays_schedule.html", item=item)


@app.route("/admin/todays-schedule/update/<int:id>", methods=["POST"])
def todays_schedule_update(id):
    guard = require_admin()
    if guard: return guard
    date = request.form["date"]
    conn = get_db()
    conn.execute("""
        UPDATE todays_schedule SET date=?, time=?, title=?, location=?, notes=? WHERE id=?
    """, (date, request.form["time"], request.form["title"],
          request.form.get("location", ""), request.form.get("notes", ""), id))
    conn.commit()
    conn.close()
    flash("Schedule updated!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/delete/<int:id>", methods=["POST"])
def todays_schedule_delete(id):
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    row = conn.execute("SELECT date FROM todays_schedule WHERE id=?", (id,)).fetchone()
    conn.execute("DELETE FROM todays_schedule WHERE id=?", (id,))
    conn.commit()
    conn.close()
    date = row["date"] if row else ""
    flash("Schedule deleted!")
    return redirect(f"/admin/todays-schedule?date={date}" if date else "/admin/todays-schedule")


@app.route("/admin/activity")
def admin_activity():
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    today = malaysia_now().strftime("%Y-%m-%d")
    yesterday = (malaysia_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Recent bookings
    bookings = conn.execute("""
        SELECT b.id, u.name AS user_name, f.name AS facility_name,
               b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        ORDER BY b.id DESC LIMIT 30
    """).fetchall()

    # Recent users
    users = conn.execute("""
        SELECT id, name, email, role FROM users ORDER BY id DESC LIMIT 20
    """).fetchall()

    # Facilities
    facilities = conn.execute("""
        SELECT id, name, location FROM facilities ORDER BY id DESC LIMIT 10
    """).fetchall()

    # Stats
    total_bookings_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE date=?", (today,)
    ).fetchone()["cnt"]
    pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE is_approved=0 AND status != 'canceled'"
    ).fetchone()["cnt"]
    total_users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    total_facilities = conn.execute("SELECT COUNT(*) as cnt FROM facilities").fetchone()["cnt"]

    conn.close()
    return render_template("admin_activity.html",
        bookings=bookings, users=users, facilities=facilities,
        today=today, yesterday=yesterday,
        total_bookings_today=total_bookings_today,
        pending=pending, total_users=total_users,
        total_facilities=total_facilities,
    )


@app.route("/admin/contact")
def admin_contact():
    guard = require_admin()
    if guard: return guard
    conn = get_db()
    messages = conn.execute(
        "SELECT * FROM contact_messages ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_contact.html", messages=messages)


@app.route("/admin/penalties")
def penalties_page():
    guard = require_admin()
    if guard: return guard
    return render_template("add_penalty.html")


# ============================================================
# SEND NOTICES (manual trigger)
# ============================================================
@app.route('/send-notice', methods=['GET', 'POST'])
def send_notice():
    guard = require_student()
    if guard: return guard

    # In this project, reminders are per-user booking_notifications.
    # This endpoint processes all due reminders for all users.
    if request.method == 'POST':
        try:
            now = malaysia_now().strftime("%Y-%m-%d %H:%M")
            conn = get_db()
            cur = conn.cursor()
            rows = cur.execute(
                """
                SELECT id, user_id, booking_id, remind_at, message
                FROM booking_notifications
                WHERE is_sent=0 AND remind_at <= ?
                """,
                (now,)
            ).fetchall()

            for r in rows:
                cur.execute(
                    "UPDATE booking_notifications SET is_sent=1 WHERE id=?",
                    (r["id"],)
                )
            conn.commit()
            processed = len(rows)
            conn.close()

            flash(f"Processed {processed} due reminder(s).")
        except Exception:
            flash("Failed to send notices.")

        # Stay on the page
        return redirect('/send-notice')

    return render_template('sendnotice.html')


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    app.run(debug=True)

