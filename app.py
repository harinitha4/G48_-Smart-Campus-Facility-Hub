from flask import Flask, render_template, request, redirect, session, url_for, flash, make_response
import sqlite3
import re
import os
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from jinja2 import ChoiceLoader, FileSystemLoader

repo_root = os.path.dirname(__file__)

# Template search order: repo contains multiple nested versions of templates.
# This lets existing templates resolve correctly.
template_paths = [
    os.path.join(repo_root, "smart campus", "templates"),
    os.path.join(repo_root, "templates"),
    os.path.join(repo_root, "smart-hub", "templates"),
    os.path.join(repo_root, "smart campus", "smart campus", "templates"),
]

template_paths = [p for p in template_paths if os.path.isdir(p)]


def merge_static_fallbacks():
    """Merge nested static folders into the top-level /static if missing."""

    top_static = os.path.join(repo_root, "static")
    candidate_statics = [
        os.path.join(repo_root, "smart campus", "static"),
        os.path.join(repo_root, "smart campus", "smart campus", "static"),
        os.path.join(repo_root, "smart-hub", "static"),
    ]

    if not os.path.isdir(top_static):
        os.makedirs(top_static, exist_ok=True)

    for cand in candidate_statics:
        if not os.path.isdir(cand):
            continue
        for root, _, files in os.walk(cand):
            for fn in files:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, cand)
                dst = os.path.join(top_static, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst):
                    try:
                        import shutil

                        shutil.copy2(src, dst)
                    except Exception:
                        pass


merge_static_fallbacks()

main_static_folder = os.path.join(repo_root, "static")

app = Flask(
    __name__,
    template_folder=os.path.join(repo_root, "templates"),
    static_folder=main_static_folder,
)

app.jinja_loader = ChoiceLoader([FileSystemLoader(p) for p in template_paths])

app.secret_key = "smartcampus_secret_key"


# ============================================================
# DATABASE
# ============================================================

def get_db():
    # Use the repo root database.db, matching existing code expectations.
    conn = sqlite3.connect(os.path.join(repo_root, "database.db"))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Users table (admin + student roles)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'student'
        )
        """
    )

    # Facilities table
    cur.execute(
        """
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
        """
    )

    # Bookings table (used by both student and admin pages)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            facility_id INTEGER,
            date TEXT,
            time TEXT,
            status TEXT,
            is_approved INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (facility_id) REFERENCES facilities(id)
        )
        """
    )

    # Booking reminders
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS booking_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            booking_id INTEGER,
            remind_at TEXT,
            message TEXT,
            is_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (booking_id) REFERENCES bookings(id)
        )
        """
    )

    # Today's Schedule table (admin)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS todays_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            title TEXT,
            location TEXT,
            notes TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def seed_data():
    conn = get_db()
    cur = conn.cursor()

    # Seed admin user (if missing)
    cur.execute("SELECT id FROM users WHERE username=?", ("admin",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (name, username, email, password, role) VALUES (?, ?, ?, ?, ?)",
            ("Administrator", "admin", "admin@smartcampus.com", generate_password_hash("admin123"), "admin"),
        )

    # Seed sample facilities
    cur.execute("SELECT id FROM facilities")
    if not cur.fetchall():
        facilities = [
            ("Library Study Room", "Main Campus", "Quiet space", ""),
            ("Computer Lab", "Block A", "PCs available", ""),
            ("Sports Hall", "Sports Complex", "Indoor court", ""),
        ]
        cur.executemany(
            "INSERT INTO facilities (name, location, description, image_filenames) VALUES (?, ?, ?, ?)",
            facilities,
        )

    conn.commit()
    conn.close()


init_db()
seed_data()


# ============================================================
# HELPERS
# ============================================================

def is_logged_in():
    return "user_id" in session


def is_admin():
    return session.get("role") == "admin"


def require_admin():
    if "user_id" not in session:
        flash("Please log in as admin.")
        return redirect("/admin-login")
    if session.get("role") != "admin":
        session.clear()
        session.modified = True
        flash("Access denied. Please log in as admin.")
        return redirect("/admin-login")
    return None


def require_student():
    if "user_id" not in session:
        flash("Please log in to continue.")
        return redirect("/login")
    if session.get("role") == "admin":
        session.clear()
        session.modified = True
        flash("Access denied. Please log in as a student.")
        return redirect("/login")
    return None


# ============================================================
# ROUTES: Public
# ============================================================


@app.route("/")
def index():
    today = datetime.now().date().isoformat()
    conn = get_db()

    total_bookings = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    total_capacity = conn.execute(
        "SELECT COALESCE(SUM(capacity), 0) FROM facilities WHERE capacity > 0"
    ).fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]

    facilities_raw = conn.execute(
        """
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
        """,
        (today,),
    ).fetchall()

    conn.close()

    icons = {
        "sport": "🏀",
        "court": "🏀",
        "badminton": "🏸",
        "tennis": "🎾",
        "meeting": "🏢",
        "conference": "🏢",
        "study": "📚",
        "library": "📖",
        "lab": "🔬",
        "computer": "💻",
        "hall": "🎟️",
        "event": "🎟️",
        "seminar": "🎙️",
        "auditorium": "🎭",
        "gym": "🏋️",
        "pool": "🏊",
    }

    def _icon(cat: str):
        c = (cat or "").lower()
        for k, v in icons.items():
            if k in c:
                return v
        return "🏛️"

    def _badge(capacity, booked):
        if not capacity:
            return "Open"
        avail = max(0, capacity - booked)
        if avail == 0:
            return "Full"
        return f"{avail} Left" if avail <= 3 else f"{avail} Free"

    facility_cards = [
        {
            "id": f["id"],
            "name": f["name"],
            "description": (f["description"] or "")[:60],
            "icon": _icon(f["category"]),
            "badge": _badge(f["capacity"], f["booked_today"]),
            "full": f["booked_today"] >= f["capacity"] if f["capacity"] else False,
        }
        for f in facilities_raw
    ]

    return render_template(
        "index.html",
        total_bookings=total_bookings,
        spaces_available=total_capacity or len(facility_cards),
        total_users=total_users,
        facility_cards=facility_cards,
    )


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/facilities")
def facilities():
    conn = get_db()
    facilities_list = conn.execute("SELECT id, name, description FROM facilities ORDER BY id").fetchall()
    conn.close()
    return render_template("facilities.html", facilities=facilities_list)


@app.route("/bookings_360")
def bookings_360():
    conn = get_db()
    facilities = conn.execute("SELECT id, name FROM facilities ORDER BY id").fetchall()
    conn.close()
    return render_template("bookings.html", facilities=facilities)


# ============================================================
# ROUTES: Auth (Student + Admin)
# ============================================================


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

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
                (name, name, email, hashed, "student"),
            )
            conn.commit()
            conn.close()
            flash("Register success! Please login.")
            return redirect("/login")
        except Exception:
            flash("Email or username already exists")
            return redirect("/register")

    return render_template("register.html")


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if is_logged_in():
        return redirect("/admin" if is_admin() else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="admin")
    return render_template("admin_login.html")


@app.route("/user-login", methods=["GET", "POST"])
def user_login():
    if is_logged_in():
        return redirect("/admin" if is_admin() else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="student")
    return render_template("user_login.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect("/admin" if is_admin() else "/dashboard")
    if request.method == "POST":
        return _handle_login(request, intended_role="student")
    return render_template("user_login.html")


def _handle_login(request, intended_role=None):
    identifier = request.form.get("username") or request.form.get("email")
    password = request.form.get("password")

    if not identifier or not password:
        flash("Please enter your credentials")
        return redirect("/admin-login" if intended_role == "admin" else "/login")

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? OR email=?",
        (identifier, identifier),
    ).fetchone()
    conn.close()

    if not user:
        flash("Account not found")
        return redirect("/admin-login" if intended_role == "admin" else "/login")

    # Role check (optional, but helps avoid confusion)
    if intended_role and user["role"] != intended_role:
        flash("Incorrect account type")
        return redirect("/admin-login" if intended_role == "admin" else "/login")

    try:
        # Werkzeug expects the stored value to be a hash string.
        password_match = check_password_hash(user["password"], password)
    except Exception:
        password_match = False

    # Debug fallback: if for some reason the hash check fails, log the mismatch reason.
    # (No password is printed.)
    if not password_match:
        # If the app is using an old/foreign database file, admin users might not match.
        # This keeps behavior secure (no secrets) while helping diagnose wrong DB issues.
        app.logger.warning(
            "Login failed: identifier=%s intended_role=%s db_password_type=%s",
            identifier,
            intended_role,
            type(user['password']).__name__ if user and 'password' in user.keys() else 'unknown',
        )

    if not password_match:
        flash("Incorrect password")
        return redirect("/admin-login" if intended_role == "admin" else "/login")

    session["user_id"] = user["id"]
    session["user_name"] = user["name"] or user["username"]
    session["role"] = user["role"]

    flash("Login successful!")
    return redirect("/admin" if user["role"] == "admin" else "/dashboard")


# ============================================================
# ROUTES: Student
# ============================================================


@app.route("/dashboard")
def dashboard():
    guard = require_student()
    if guard:
        return guard

    conn = get_db()
    user_id = session.get("user_id")
    notification_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (user_id,),
    ).fetchone()["cnt"]

    facilities = conn.execute(
        "SELECT id, name, location, description, image_filenames, category, capacity, view_360_filename FROM facilities"
    ).fetchall()

    # Convert stored filenames (local DB values) -> public URLs that the browser can request.
    # Stored in DB as comma-separated filenames relative to /static/facility_uploads/.
    facilities_for_client = []
    for f in facilities:
        image_filenames = (f["image_filenames"] or "").split(",") if f["image_filenames"] else []
        image_filenames = [x.strip() for x in image_filenames if x.strip()]
        first_image = image_filenames[0] if image_filenames else ""

        facilities_for_client.append(
            {
                "id": f["id"],
                "title": f["name"],
                "category": f["category"],
                "capacity": f["capacity"],
                "description": f["description"],
                "image": f"/static/facility_uploads/{first_image}" if first_image else "",
                # status fields used by the dashboard UI; keep it simple for now.
                "status": "Available",
                "availability_status": "Available",
                "location": f["location"],
                "view_360_filename": f["view_360_filename"],
                "view_360_url": f["view_360_filename"],
            }
        )

    conn.close()

    return render_template(
        "dashboard.html",
        name=session.get("user_name", "User"),
        notification_count=notification_count,
        facilities=facilities_for_client,
    )


@app.route("/help")
def help_page():
    # Keep /help as a backwards-compatible shortcut.
    # Requirement: Help button should jump to the Main Dashboard Contact section/page.
    guard = require_student()
    if guard:
        return guard
    return redirect(url_for("contact"))



@app.route("/profile", methods=["GET", "POST"])
def profile():
    guard = require_student()
    if guard:
        return guard

    if request.method == "POST":
        session["full_name"] = request.form.get("full_name") or session.get("user_name")
        session["student_id"] = request.form.get("student_id") or ""
        session["email"] = request.form.get("email") or ""
        session["phone"] = request.form.get("phone") or ""
        session["department"] = request.form.get("department") or ""
        session["booking_duration"] = request.form.get("booking_duration") or "1"

        # optional lists from some templates
        session["preferred_categories"] = request.form.getlist("pref_categories")
        if not isinstance(session["preferred_categories"], list):
            session["preferred_categories"] = [session["preferred_categories"]]

        session["notification_prefs"] = request.form.getlist("pref_notifications")
        if not isinstance(session["notification_prefs"], list):
            session["notification_prefs"] = [session["notification_prefs"]]

        flash("Profile saved")
        return redirect("/profile")

    # Defaults
    full_name = session.get("full_name", session.get("user_name", ""))
    student_id = session.get("student_id", "")
    email = session.get("email", "")
    phone = session.get("phone", "")
    department = session.get("department", "")
    booking_duration = int(session.get("booking_duration", "1") or 1)

    preferred_categories = session.get("preferred_categories", ["academic"])
    notification_prefs = session.get("notification_prefs", ["booking_updates"])

    conn2 = get_db()
    notif_count = conn2.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (session.get("user_id"),),
    ).fetchone()["cnt"]
    conn2.close()

    photo_url = session.get("photo_url", "") or ""

    return render_template(
        "profile.html",
        name=session.get("user_name", "User"),
        photo_url=photo_url,
        full_name=full_name,
        student_id=student_id,
        email=email,
        phone=phone,
        department=department,
        booking_duration=booking_duration,
        preferred_categories=set(
            preferred_categories if isinstance(preferred_categories, list) else [preferred_categories]
        ),
        notification_prefs=set(
            notification_prefs if isinstance(notification_prefs, list) else [notification_prefs]
        ),
        notification_count=notif_count,
    )


@app.route("/profile/password", methods=["POST"])
def profile_password():
    guard = require_student()
    if guard:
        return guard

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not new_password or new_password != confirm_password:
        flash("New password and confirm password must match")
        return redirect("/profile")

    # demo-only
    flash("Password change saved (demo)")
    return redirect("/profile")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out")
    resp = make_response(redirect("/login"))
    resp.delete_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


# Student bookings list + cancel
@app.route("/bookings")
def my_bookings():
    guard = require_student()
    if guard:
        return guard

    conn = get_db()
    user_id = session.get("user_id")
    rows = conn.execute(
        """
        SELECT b.id, f.name AS facility_title, b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN facilities f ON b.facility_id = f.id
        WHERE b.user_id = ?
        ORDER BY b.id DESC
        """,
        (user_id,),
    ).fetchall()

    notif_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM booking_notifications WHERE user_id=? AND is_sent=0",
        (user_id,),
    ).fetchone()["cnt"]
    conn.close()

    bookings = []
    for r in rows:
        created_at = f"{r['date']} {r['time']}" if r["date"] and r["time"] else ""
        bookings.append({
            **dict(r),
            "created_at": created_at,
            # normalize for templates
            "status": r["status"] or ("approved" if r["is_approved"] else "pending"),
        })

    # templates appear in smart-hub; ChoiceLoader should find them
    return render_template(
        "smart_hub_bookings.html",
        name=session.get("user_name", "User"),
        bookings=bookings,
        notification_count=notif_count,
    )


@app.route("/booking/cancel/<int:id>", methods=["POST"])
def cancel_booking(id):
    guard = require_student()
    if guard:
        return guard

    conn = get_db()
    user_id = session.get("user_id")
    conn.execute(
        "UPDATE bookings SET status='cancelled' WHERE id=? AND user_id=?",
        (id, user_id),
    )
    conn.commit()
    conn.close()

    flash("Booking cancelled.")
    return redirect("/bookings")


# Facility booking
@app.route("/book/<int:facility_id>", methods=["GET", "POST"])
def facility_booking(facility_id):
    if not session.get("user_id"):
        flash("Please log in to book a facility.")
        return redirect(url_for("login"))

    conn = get_db()
    facility_row = conn.execute("SELECT id, name FROM facilities WHERE id=?", (facility_id,)).fetchone()
    if facility_row is None:
        conn.close()
        flash("Facility not found.")
        return redirect("/dashboard")

    facility = facility_row["name"]

    if request.method == "POST":
        booking_date = request.form.get("date")
        duration_from = request.form.get("duration_from")

        # optional fields
        duration_to = request.form.get("duration_to")
        student_name = request.form.get("student_name")
        student_id = request.form.get("student_id")
        _purpose = request.form.get("purpose")

        if not booking_date or not duration_from:
            flash("Please provide booking date and start time")
            conn.close()
            return redirect(url_for("facility_booking", facility_id=facility_id))

        user_id = session.get("user_id")

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bookings (user_id, facility_id, date, time, status, is_approved)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, facility_id, booking_date, duration_from, "pending", 0),
        )
        booking_id = cur.lastrowid
        conn.commit()

        dt = datetime.strptime(f"{booking_date} {duration_from}", "%Y-%m-%d %H:%M")
        remind_at = (dt - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
        message = f"Reminder: your booking at {duration_from} for {facility} starts in 15 minutes."

        conn.execute(
            """
            INSERT INTO booking_notifications (user_id, booking_id, remind_at, message, is_sent)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, booking_id, remind_at, message),
        )
        conn.commit()
        conn.close()

        flash("Booking submitted! You will receive a reminder 15 minutes before start.")
        return redirect("/bookings")

    conn.close()

    return render_template(
        "facility_booking.html",
        facility=facility,
        facility_id=facility_id,
        user_name=session.get("user_name", ""),
        notification_count=session.get("notification_count", 0),
    )


@app.route("/notifications")
def notifications():
    guard = require_student()
    if guard:
        return guard

    conn = get_db()
    user_id = session.get("user_id")
    notes = conn.execute(
        """
        SELECT id, booking_id, remind_at, message, is_sent, created_at
        FROM booking_notifications
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT 30
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    name = session.get("user_name", "User")
    notif_count = len([n for n in notes if not n["is_sent"]])
    return render_template("notifications.html", notes=notes, name=name, notification_count=notif_count)


# ============================================================
# ROUTES: Admin
# ============================================================


@app.route("/admin")
def admin_dashboard():
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()
    pending_approvals_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE is_approved = 0"
    ).fetchone()["cnt"]

    bookings_dashboard = conn.execute(
        """
        SELECT b.id, u.username, f.name AS facility_name,
               b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        ORDER BY b.id DESC
        """
    ).fetchall()
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
    if guard:
        return guard

    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()

    return render_template("admin_users.html", users=users)


@app.route("/admin/bookings")
def admin_bookings():
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    bookings = conn.execute(
        """
        SELECT b.id, u.username, f.name as facility_name,
               b.user_id, b.facility_id, b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        """
    ).fetchall()
    conn.close()

    return render_template("admin_bookings.html", bookings=bookings)


@app.route("/admin/edit-booking/<int:id>", methods=["GET", "POST"])
def edit_booking(id):
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    if request.method == "POST":
        is_approved = 1 if request.form.get("is_approved") == "on" else 0
        conn.execute(
            """
            UPDATE bookings SET date=?, time=?, status=?, is_approved=? WHERE id=?
            """,
            (
                request.form.get("date"),
                request.form.get("time"),
                request.form.get("status"),
                is_approved,
                id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Booking updated!")
        return redirect("/admin/bookings")

    booking = conn.execute(
        """
        SELECT b.*, u.username, f.name as facility_name
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        WHERE b.id=?
        """,
        (id,),
    ).fetchone()
    conn.close()
    return render_template("edit_booking.html", booking=booking)


@app.route("/admin/approve-booking/<int:id>")
def approve_booking(id):
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    conn.execute(
        "UPDATE bookings SET is_approved=1, status='approved' WHERE id=?",
        (id,),
    )
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
    conn.execute(
        "UPDATE bookings SET is_approved=0, status='rejected' WHERE id=?",
        (id,),
    )
    conn.commit()
    conn.close()
    flash("Booking rejected!")
    return redirect("/admin/bookings")


@app.route("/admin/delete-booking/<int:id>", methods=["POST"])
def delete_booking(id):
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Booking deleted!")
    return redirect("/admin/bookings")


@app.route("/admin/facilities")
def admin_facilities():
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()
    conn.close()
    return render_template("admin_facilities.html", facilities=facilities)


@app.route("/admin/add-facility", methods=["GET", "POST"])
def add_facility():
    guard = require_admin()
    if guard:
        return guard

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
            """
            INSERT INTO facilities (name, location, description, image_filenames, category, capacity, view_360_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, location, description, ",".join(image_filenames), category, capacity, view_360_filename),
        )
        conn.commit()
        conn.close()

        flash("Facility added!")
        return redirect("/admin/facilities")

    return render_template("add_facility.html")


@app.route("/admin/edit-facility/<int:id>", methods=["GET", "POST"])
def edit_facility(id):
    guard = require_admin()
    if guard:
        return guard

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

        existing = conn.execute("SELECT image_filenames FROM facilities WHERE id=?", (id,)).fetchone()
        image_filenames = [x for x in (existing["image_filenames"] or "").split(",") if x] if existing else []

        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        for f in files:
            if not f or not f.filename:
                continue
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in allowed_ext:
                continue
            filename = secure_filename(f"{name}_{len(image_filenames)+1}.{ext}")
            f.save(os.path.join(upload_dir, filename))
            image_filenames.append(filename)

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

        conn.execute(
            """
            UPDATE facilities SET
                name=?, location=?, description=?, image_filenames=?, category=?, capacity=?, view_360_filename=?
            WHERE id=?
            """,
            (name, location, description, ",".join(image_filenames), category, capacity, view_360_filename, id),
        )
        conn.commit()
        conn.close()

        flash("Updated successfully!")
        return redirect("/admin/facilities")

    facility = conn.execute("SELECT * FROM facilities WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template("edit_facility.html", facility=facility)


@app.route("/admin/delete-facility/<int:id>", methods=["POST", "GET"])
def delete_facility(id):
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    conn.execute("DELETE FROM facilities WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Deleted successfully!")
    return redirect("/admin/facilities")


# Schedule
@app.route("/admin/schedule")
def admin_schedule_alias():
    guard = require_admin()
    if guard:
        return guard
    return redirect("/admin/todays-schedule")


@app.route("/admin/todays-schedule")
def todays_schedule():
    guard = require_admin()
    if guard:
        return guard

    selected_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    schedules = conn.execute(
        "SELECT * FROM todays_schedule WHERE date=? ORDER BY time ASC",
        (selected_date,),
    ).fetchall()
    conn.close()

    return render_template(
        "admin_schedule.html",
        schedules=schedules,
        selected_date=selected_date,
    )


@app.route("/admin/todays-schedule/add", methods=["POST"])
def todays_schedule_add():
    guard = require_admin()
    if guard:
        return guard

    date = request.form["date"]
    time = request.form["time"]
    title = request.form["title"]
    location = request.form.get("location", "")
    notes = request.form.get("notes", "")

    conn = get_db()
    conn.execute(
        "INSERT INTO todays_schedule (date, time, title, location, notes) VALUES (?, ?, ?, ?, ?)",
        (date, time, title, location, notes),
    )
    conn.commit()
    conn.close()

    flash("Schedule added!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/edit/<int:id>")
def todays_schedule_edit(id):
    guard = require_admin()
    if guard:
        return guard

    conn = get_db()
    item = conn.execute("SELECT * FROM todays_schedule WHERE id=?", (id,)).fetchone()
    conn.close()

    return render_template("edit_todays_schedule.html", item=item)


@app.route("/admin/todays-schedule/update/<int:id>", methods=["POST"])
def todays_schedule_update(id):
    guard = require_admin()
    if guard:
        return guard

    date = request.form["date"]

    conn = get_db()
    conn.execute(
        """
        UPDATE todays_schedule SET date=?, time=?, title=?, location=?, notes=? WHERE id=?
        """,
        (
            date,
            request.form["time"],
            request.form["title"],
            request.form.get("location", ""),
            request.form.get("notes", ""),
            id,
        ),
    )
    conn.commit()
    conn.close()

    flash("Schedule updated!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/delete/<int:id>", methods=["POST"])
def todays_schedule_delete(id):
    guard = require_admin()
    if guard:
        return guard

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
    if guard:
        return guard

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    conn = get_db()

    bookings = conn.execute(
        """
        SELECT b.id, u.name AS user_name, f.name AS facility_name,
               b.date, b.time, b.status, b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
        ORDER BY b.id DESC LIMIT 30
        """
    ).fetchall()

    users = conn.execute("SELECT id, name, email, role FROM users ORDER BY id DESC LIMIT 20").fetchall()
    facilities = conn.execute("SELECT id, name, location FROM facilities ORDER BY id DESC LIMIT 10").fetchall()

    total_bookings_today = conn.execute("SELECT COUNT(*) as cnt FROM bookings WHERE date=?", (today,)).fetchone()["cnt"]
    pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE is_approved=0 AND status != 'cancelled'"
    ).fetchone()["cnt"]
    total_users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    total_facilities = conn.execute("SELECT COUNT(*) as cnt FROM facilities").fetchone()["cnt"]

    conn.close()

    return render_template(
        "admin_activity.html",
        bookings=bookings,
        users=users,
        facilities=facilities,
        today=today,
        yesterday=yesterday,
        total_bookings_today=total_bookings_today,
        pending=pending,
        total_users=total_users,
        total_facilities=total_facilities,
    )


@app.route("/admin/penalties")
def penalties_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("add_penalty.html")


# ============================================================
# SEND NOTICES (manual trigger)
# ============================================================


@app.route("/send-notice", methods=["GET", "POST"])
def send_notice():
    guard = require_student()
    if guard:
        return guard

    if request.method == "POST":
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = get_db()
        cur = conn.cursor()

        rows = cur.execute(
            """
            SELECT id
            FROM booking_notifications
            WHERE is_sent=0 AND remind_at <= ?
            """,
            (now,),
        ).fetchall()

        for r in rows:
            cur.execute("UPDATE booking_notifications SET is_sent=1 WHERE id=?", (r["id"],))

        conn.commit()
        processed = len(rows)
        conn.close()

        flash(f"Processed {processed} due reminder(s).")
        return redirect("/send-notice")

    return render_template("sendnotice.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)

