from flask import Flask, render_template, request, redirect, flash, session

import sqlite3
import re
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "12345"

# connect database
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# create table
def init_db():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT
    )
    """)

    # Bookings: store per-user facility reservations (demo-friendly)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        facility_title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    conn.commit()
    conn.close()

init_db()

# home
@app.route('/')
def home():
    return "Smart Campus Facility Hub running"

# register
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
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, hashed)
            )
            conn.commit()
            conn.close()
            flash("Register success!")
        except:
            flash("Email already exists")

        return redirect("/register")

    return render_template("register.html")

# login
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            flash("Please enter both email and password")
            return redirect("/login")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()
        conn.close()

        if user:
            if check_password_hash(user["password"], password):
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                flash("Login successful!")
                return redirect("/dashboard")
            else:
                flash("Incorrect password")
        else:
            flash("Email not found")

        return redirect("/login")

    return render_template("login.html")

# dashboard (protected page)
@app.route('/dashboard')
def dashboard():
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    return render_template("dashboard.html", name=session.get('user_name', 'User'))



@app.route('/bookings')
def bookings():
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, facility_title, status, created_at
        FROM bookings
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (session['user_id'],),
    ).fetchall()
    conn.close()

    # Normalize status labels expected by templates
    normalized = []
    for r in rows:
        status = r["status"]
        # UI expects: approved / rejected / canceled
        if status == 'active' or status == 'pending':
            status = 'approved'
        elif status == 'rejected':
            status = 'rejected'
        elif status == 'canceled':
            status = 'canceled'

        normalized.append({
            'id': r['id'],
            'facility_title': r['facility_title'],
            'status': status if status else 'approved',
            'created_at': r['created_at'],
        })

    return render_template('bookings.html', name=session.get('user_name', 'User'), bookings=normalized)


@app.route('/booking/create', methods=["POST"])
def booking_create():
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    facility_title = request.form.get('facility_title', '').strip()
    if not facility_title:
        flash('Facility title missing')
        return redirect('/dashboard')

    conn = get_db()
    conn.execute(
        "INSERT INTO bookings (user_id, facility_title, status, created_at) VALUES (?, ?, 'pending', datetime('now'))",
        (session['user_id'], facility_title)
    )
    conn.commit()
    conn.close()

    flash(f'Booked: {facility_title}')
    return redirect('/bookings')


@app.route('/booking/cancel/<int:booking_id>', methods=["POST"])
def booking_cancel(booking_id):
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    conn = get_db()
    booking = conn.execute(
        "SELECT id FROM bookings WHERE id=? AND user_id=?",
        (booking_id, session['user_id'])
    ).fetchone()

    if not booking:
        conn.close()
        flash('Booking not found')
        return redirect('/bookings')

    conn.execute(
        "UPDATE bookings SET status='canceled' WHERE id=? AND user_id=?",
        (booking_id, session['user_id'])
    )
    conn.commit()
    conn.close()

    flash('Booking canceled')
    return redirect('/bookings')


# profile (protected page)
@app.route('/profile', methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    if request.method == "POST":
        # Demo persistence: store profile fields in session.
        session['full_name'] = request.form.get('full_name') or session.get('full_name') or session.get('user_name')
        session['student_id'] = request.form.get('student_id') or ''
        session['email'] = request.form.get('email') or ''
        session['phone'] = request.form.get('phone') or ''
        session['department'] = request.form.get('department') or ''
        session['booking_duration'] = request.form.get('booking_duration') or '1'

        session['preferred_categories'] = request.form.getlist('pref_categories') if hasattr(request.form, 'getlist') else request.form.get('pref_categories', '')
        session['preferred_categories'] = session['preferred_categories'] if isinstance(session.get('preferred_categories'), list) else [session.get('preferred_categories')]
        session['notification_prefs'] = request.form.getlist('pref_notifications') if hasattr(request.form, 'getlist') else request.form.get('pref_notifications', '')
        session['notification_prefs'] = session['notification_prefs'] if isinstance(session.get('notification_prefs'), list) else [session.get('notification_prefs')]

        flash("Profile saved")
        return redirect('/profile')

    # Defaults
    full_name = session.get('full_name', session.get('user_name', ''))
    student_id = session.get('student_id', '')
    # email from DB if available
    email = session.get('email', '')
    phone = session.get('phone', '')
    department = session.get('department', '')
    booking_duration = int(session.get('booking_duration', '1') or 1)
    preferred_categories = session.get('preferred_categories', ['academic'])
    notification_prefs = session.get('notification_prefs', ['booking_updates'])

    # Demo booking stats
    total_bookings = int(session.get('total_bookings', 0))
    favorite_count = int(session.get('favorite_count', 0))
    favorites = session.get('favorites', ['Library Discussion Rooms', 'Study Lounges'])

    photo_url = session.get('photo_url', '') or ''

    return render_template(
        'profile.html',
        name=session.get('user_name', 'User'),
        photo_url=photo_url,
        full_name=full_name,
        student_id=student_id,
        email=email,
        phone=phone,
        department=department,
        booking_duration=booking_duration,
        preferred_categories=set(preferred_categories if isinstance(preferred_categories, list) else [preferred_categories]),
        notification_prefs=set(notification_prefs if isinstance(notification_prefs, list) else [notification_prefs]),
        total_bookings=total_bookings,
        favorite_count=favorite_count,
        favorites=favorites,
    )


@app.route('/profile/password', methods=["POST"])
def profile_password():
    if "user_id" not in session:
        flash("Please login first")
        return redirect("/login")

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    # Demo-only behavior (not persisted in DB)
    if not new_password or new_password != confirm_password:
        flash("New password and confirm password must match")
        return redirect('/profile')

    flash("Password change saved (demo)")
    return redirect('/profile')


# logout
@app.route('/logout')
def logout():

    session.clear()
    flash("You have been logged out")
    return redirect("/login")

# run app
if __name__ == "__main__":
    app.run(debug=True)

