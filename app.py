from flask import Flask, render_template, request, redirect, session, url_for, flash
import sqlite3
import re
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Multi-template/static support:
# This repo contains multiple nested versions of templates/static.
# Configure Flask to search all of them so routes like /login reliably find templates.
from jinja2 import ChoiceLoader, FileSystemLoader

repo_root = os.path.dirname(__file__)

template_paths = [
    # Put smart campus first so its login.html wins over any duplicate at top-level templates/
    os.path.join(repo_root, "smart campus", "templates"),
    os.path.join(repo_root, "templates"),
    os.path.join(repo_root, "smart-hub", "templates"),
    # Some student folders contain templates under an extra nested directory
    os.path.join(repo_root, "smart campus", "smart campus", "templates"),
]


template_paths = [p for p in template_paths if os.path.isdir(p)]

# Flask only accepts one static_folder.
# Use the Smart Campus static folder (this matches the majority of your templates).
main_static_folder = os.path.join(repo_root, "smart campus", "static")

app = Flask(
    __name__,
    template_folder=os.path.join(repo_root, "templates"),
    static_folder=main_static_folder,
)

# Override Jinja loader to include multiple template folders.
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
        image_filenames TEXT DEFAULT ''
    )
    """)

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
            ("Administrator", "admin", "admin@smartcampus.com", "admin123", "admin")
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


# ============================================================
# MAIN DASHBOARD ROUTES (scf hub - Harinitha)
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/facilities')
def facilities():
    return render_template('facilities.html')

@app.route('/bookings_360')
def bookings_360():
    return render_template('bookings.html')

@app.route('/book/<facility>', methods=['GET', 'POST'])
def facility_booking(facility):
    if not facility or facility.strip() == "":
        facility = "Facility"
    return render_template('facility_booking.html', facility=facility)


# ============================================================
# STUDENT ROUTES (smart-hub - Bavisshaa)
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
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, hashed, "student")
            )
            conn.commit()
            conn.close()
            flash("Register success! Please login.")
            return redirect("/login")
        except:
            flash("Email already exists")
            return redirect("/register")

    return render_template("register.html")


@app.route('/admin-login')
def admin_login():
    # Just a landing page for admin users
    return redirect('/login?intended_role=admin')


@app.route('/user-login')
def user_login():
    # Just a landing page for student users
    return redirect('/login?intended_role=student')


@app.route('/login', methods=["GET", "POST"])
def login():
    intended_role = request.args.get('intended_role')

    if is_logged_in():
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")



    if request.method == "POST":
        # Support both username and email login
        identifier = request.form.get("username") or request.form.get("email")
        password = request.form.get("password")

        if not identifier or not password:
            flash("Please enter your credentials")
            return redirect("/login")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? OR email=?",
            (identifier, identifier)
        ).fetchone()
        conn.close()

        if user:
            # Admin uses plain password, students use hashed
            if user["role"] == "admin":
                password_match = (password == user["password"])
            else:
                try:
                    password_match = check_password_hash(user["password"], password)
                except:
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
            flash("User not found")

        return redirect("/login")

    # If caller asked for admin login UI, prefer the smart-campus themed template.
    if intended_role == "admin":
        # Use the Jinja2 ChoiceLoader so templates can come from different folders.
        # The smart-campus template has <title>Smart Campus - Login</title>.
        # If it is not found, it will raise so we can see the real issue.
        return render_template("login.html")
    return render_template("login.html")






@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        flash("Please login first")
        return redirect("/login")
    return render_template("dashboard.html", name=session.get('user_name', 'User'))


@app.route('/profile', methods=["GET", "POST"])
def profile():
    if not is_logged_in():
        flash("Please login first")
        return redirect("/login")

    if request.method == "POST":
        session['full_name'] = request.form.get('full_name') or session.get('user_name')
        session['student_id'] = request.form.get('student_id') or ''
        session['email'] = request.form.get('email') or ''
        session['phone'] = request.form.get('phone') or ''
        session['department'] = request.form.get('department') or ''
        session['booking_duration'] = request.form.get('booking_duration') or '1'
        flash("Profile saved")
        return redirect('/profile')

    return render_template(
        'profile.html',
        name=session.get('user_name', 'User'),
        full_name=session.get('full_name', session.get('user_name', '')),
        student_id=session.get('student_id', ''),
        email=session.get('email', ''),
        phone=session.get('phone', ''),
        department=session.get('department', ''),
        booking_duration=int(session.get('booking_duration', 1) or 1),
    )


@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out")
    return redirect("/login")


# ============================================================
# ADMIN ROUTES (smart campus - Taniiska)
# ============================================================
@app.route("/admin")
def admin_dashboard():
    if not is_admin():
        return redirect("/login")

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
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return render_template("admin_users.html", users=users)


@app.route("/admin/bookings")
def admin_bookings():
    if not is_admin():
        return redirect("/login")
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
    if not is_admin():
        return redirect("/login")
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


@app.route("/admin/approve-booking/<int:id>")
def approve_booking(id):
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    conn.execute("UPDATE bookings SET is_approved=1 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Booking approved!")
    return redirect("/admin/bookings")


@app.route("/admin/facilities")
def admin_facilities():
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()
    conn.close()
    return render_template("admin_facilities.html", facilities=facilities)


@app.route("/admin/add-facility", methods=["GET", "POST"])
def add_facility():
    if not is_admin():
        return redirect("/login")

    if request.method == "POST":
        name = request.form["name"]
        location = request.form["location"]
        description = request.form["description"]
        image_filenames = []

        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        if files:
            upload_dir = os.path.join(app.static_folder, "facility_uploads")
            os.makedirs(upload_dir, exist_ok=True)
            allowed_ext = {"png", "jpg", "jpeg", "webp"}
            for f in files:
                if not f or not f.filename:
                    continue
                ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
                if ext not in allowed_ext:
                    continue
                filename = secure_filename(f"{name}_{len(image_filenames)+1}.{ext}")
                f.save(os.path.join(upload_dir, filename))
                image_filenames.append(filename)

        conn = get_db()
        conn.execute(
            "INSERT INTO facilities (name, location, description, image_filenames) VALUES (?, ?, ?, ?)",
            (name, location, description, ",".join(image_filenames))
        )
        conn.commit()
        conn.close()
        flash("Facility added!")
        return redirect("/admin/facilities")

    return render_template("add_facility.html")


@app.route("/admin/edit-facility/<int:id>", methods=["GET", "POST"])
def edit_facility(id):
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    if request.method == "POST":
        conn.execute("""
            UPDATE facilities SET name=?, location=?, description=? WHERE id=?
        """, (request.form["name"], request.form["location"], request.form["description"], id))
        conn.commit()
        conn.close()
        flash("Updated successfully!")
        return redirect("/admin/facilities")

    facility = conn.execute("SELECT * FROM facilities WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template("edit_facility.html", facility=facility)


@app.route("/admin/delete-facility/<int:id>")
def delete_facility(id):
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    conn.execute("DELETE FROM facilities WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Deleted successfully!")
    return redirect("/admin/facilities")


@app.route("/admin/schedule")
def admin_schedule_alias():
    if not is_admin():
        return redirect("/login")
    return redirect("/admin/todays-schedule")


@app.route("/admin/todays-schedule")
def todays_schedule():
    if not is_admin():
        return redirect("/login")
    from datetime import datetime
    selected_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    schedules = conn.execute(
        "SELECT * FROM todays_schedule WHERE date=? ORDER BY time ASC", (selected_date,)
    ).fetchall()
    conn.close()
    return render_template("admin_schedule.html", schedules=schedules, selected_date=selected_date)


@app.route("/admin/todays-schedule/add", methods=["POST"])
def todays_schedule_add():
    if not is_admin():
        return redirect("/login")
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
    if not is_admin():
        return redirect("/login")
    conn = get_db()
    item = conn.execute("SELECT * FROM todays_schedule WHERE id=?", (id,)).fetchone()
    conn.close()
    return render_template("edit_todays_schedule.html", item=item)


@app.route("/admin/todays-schedule/update/<int:id>", methods=["POST"])
def todays_schedule_update(id):
    if not is_admin():
        return redirect("/login")
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
    if not is_admin():
        return redirect("/login")
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
    if not is_admin():
        return redirect("/login")
    return render_template("admin_activity.html")


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    app.run(debug=True)
