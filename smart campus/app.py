from flask import Flask, render_template, request, redirect, session, url_for, flash
import sqlite3

app = Flask(__name__)
app.secret_key = "secret123"

# ---------------- DB CONNECTION ----------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# ---------------- INIT DB ----------------
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT,
        role TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS facilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        location TEXT,
        description TEXT
    )
    """)

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

    # Todays Schedule (NEW)
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
    
    # Add is_approved column if not exists
    cursor.execute("PRAGMA table_info(bookings)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'is_approved' not in columns:
        cursor.execute("ALTER TABLE bookings ADD COLUMN is_approved INTEGER DEFAULT 0")

    # Facility images (comma-separated filenames)
    cursor.execute("PRAGMA table_info(facilities)")
    facility_columns = [col[1] for col in cursor.fetchall()]
    if 'image_filenames' not in facility_columns:
        cursor.execute("ALTER TABLE facilities ADD COLUMN image_filenames TEXT DEFAULT ''")

    conn.commit()
    conn.close()

init_db()

# ---------------- SEED DATA ----------------
def seed_data():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE username = ?", ("admin",))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users VALUES (NULL, ?, ?, ?)",
                       ("admin", "admin123", "admin"))

    cursor.execute("SELECT * FROM users WHERE username = ?", ("user",))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users VALUES (NULL, ?, ?, ?)",
                       ("user", "user123", "user"))

    cursor.execute("SELECT * FROM facilities")
    if not cursor.fetchall():
        facilities = [
            ("Library Study Room", "Main Campus", "Quiet space"),
            ("Computer Lab", "Block A", "PCs available"),
            ("Sports Hall", "Sports Complex", "Indoor court")
        ]
        cursor.executemany("INSERT INTO facilities (name, location, description) VALUES (?, ?, ?)", facilities)

    conn.commit()
    conn.close()

seed_data()

# ---------------- AUTH ----------------
def admin_only():
    return "role" in session and session["role"] == "admin"

# ---------------- LOGIN ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect("/admin" if session["role"] == "admin" else "/home")

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect("/admin" if user["role"] == "admin" else "/home")
        else:
            flash("Invalid login")

    return render_template("login.html")

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- ADMIN DASHBOARD ----------------
@app.route("/admin")
def admin_dashboard():
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    bookings = conn.execute("SELECT * FROM bookings").fetchall()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()

    pending_approvals_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE is_approved = 0"
    ).fetchone()["cnt"]

    # Build a small dashboard-friendly bookings list
    # (so the dashboard table can use username/facility_name/is_approved)
    bookings_dashboard = conn.execute("""
        SELECT 
            b.id,
            u.username,
            f.name AS facility_name,
            b.date,
            b.time,
            b.status,
            b.is_approved
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

# ---------------- USERS ----------------
@app.route("/admin/users")
def admin_users():
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()

    return render_template("admin_users.html", users=users)

# ---------------- BOOKINGS ----------------
@app.route("/admin/bookings")
def admin_bookings():
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    bookings = conn.execute("""
        SELECT 
            b.id, 
            u.username, 
            f.name as facility_name, 
            b.user_id, 
            b.facility_id, 
            b.date, 
            b.time, 
            b.status,
            b.is_approved
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN facilities f ON b.facility_id = f.id
    """).fetchall()
    conn.close()

    return render_template("admin_bookings.html", bookings=bookings)

# EDIT BOOKING
@app.route("/admin/edit-booking/<int:id>", methods=["GET", "POST"])
def edit_booking(id):
    if not admin_only():
        return "Access Denied"

    conn = get_db()

    if request.method == "POST":
        is_approved = 1 if 'is_approved' in request.form and request.form['is_approved'] == 'on' else 0
        conn.execute("""
            UPDATE bookings
            SET date=?, time=?, status=?, is_approved=?
            WHERE id=?
        """, (
            request.form["date"],
            request.form["time"],
            request.form["status"],
            is_approved,
            id
        ))
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
    
    # Ensure is_approved is set for legacy bookings
    if booking and booking['is_approved'] is None:
        conn.execute("UPDATE bookings SET is_approved=0 WHERE id=?", (id,))
        conn.commit()
        booking = conn.execute("""
            SELECT b.*, u.username, f.name as facility_name
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            JOIN facilities f ON b.facility_id = f.id
            WHERE b.id=?
        """, (id,)).fetchone()
    conn.close()

    if not booking:
        flash("Booking not found!")
        return redirect("/admin/bookings")

    return render_template("edit_booking.html", booking=booking)

# APPROVE BOOKING
@app.route("/admin/approve-booking/<int:id>", methods=["POST"])
def approve_booking(id):
    if not admin_only():
        return "Access Denied"
    conn = get_db()
    conn.execute("UPDATE bookings SET is_approved=1 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/admin/bookings")

# REJECT BOOKING
@app.route("/admin/reject-booking/<int:id>", methods=["POST"])
def reject_booking(id):
    if not admin_only():
        return "Access Denied"
    conn = get_db()
    conn.execute("UPDATE bookings SET is_approved=0 WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/admin/bookings")

# DELETE BOOKING
@app.route("/admin/delete-booking/<int:id>")
def delete_booking(id):
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash("Booking deleted!")
    return redirect("/admin/bookings")

# =========================================================
# 🆕 FACILITY MANAGEMENT (NEW FEATURE)
# =========================================================

# VIEW FACILITIES
@app.route("/admin/facilities")
def admin_facilities():
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    facilities = conn.execute("SELECT * FROM facilities").fetchall()
    conn.close()

    return render_template("admin_facilities.html", facilities=facilities)

# ADD FACILITY
@app.route("/admin/add-facility", methods=["GET", "POST"])
def add_facility():
    if not admin_only():
        return "Access Denied"

    if request.method == "POST":
        name = request.form["name"]
        location = request.form["location"]
        description = request.form["description"]

        if not name or not location:
            flash("Name and Location required!")
            return redirect("/admin/add-facility")

        # Handle uploads (optional)
        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        image_filenames = []

        if files:
            import os
            from werkzeug.utils import secure_filename

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

                # avoid overwriting
                counter = 1
                base, ext2 = os.path.splitext(filename)
                while os.path.exists(os.path.join(upload_dir, filename)):
                    filename = f"{base}_{counter}{ext2}"
                    counter += 1

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


# EDIT FACILITY
@app.route("/admin/edit-facility/<int:id>", methods=["GET", "POST"])
def edit_facility(id):
    if not admin_only():
        return "Access Denied"

    conn = get_db()

    if request.method == "POST":
        # Update text fields
        conn.execute("""
            UPDATE facilities
            SET name=?, location=?, description=?
            WHERE id=?
        """, (
            request.form["name"],
            request.form["location"],
            request.form["description"],
            id
        ))

        # Handle optional new images (append)
        files = request.files.getlist("facility_images") if "facility_images" in request.files else []
        new_filenames = []

        if files:
            import os
            from werkzeug.utils import secure_filename

            upload_dir = os.path.join(app.static_folder, "facility_uploads")
            os.makedirs(upload_dir, exist_ok=True)

            allowed_ext = {"png", "jpg", "jpeg", "webp"}

            # existing filenames
            row = conn.execute("SELECT image_filenames FROM facilities WHERE id=?", (id,)).fetchone()
            existing = []
            if row and row["image_filenames"]:
                existing = [x for x in row["image_filenames"].split(",") if x]

            for f in files:
                if not f or not f.filename:
                    continue

                ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
                if ext not in allowed_ext:
                    continue

                filename = secure_filename(f"{request.form['name']}_{len(existing)+len(new_filenames)+1}.{ext}")

                counter = 1
                base, ext2 = os.path.splitext(filename)
                while os.path.exists(os.path.join(upload_dir, filename)):
                    filename = f"{base}_{counter}{ext2}"
                    counter += 1

                f.save(os.path.join(upload_dir, filename))
                new_filenames.append(filename)

            if new_filenames:
                all_filenames = existing + new_filenames
                conn.execute(
                    "UPDATE facilities SET image_filenames=? WHERE id=?",
                    (",".join(all_filenames), id)
                )

        conn.commit()
        conn.close()


        flash("Updated successfully!")
        return redirect("/admin/facilities")

    facility = conn.execute("SELECT * FROM facilities WHERE id=?", (id,)).fetchone()
    conn.close()

    return render_template("edit_facility.html", facility=facility)

# DELETE FACILITY
@app.route("/admin/delete-facility/<int:id>")
def delete_facility(id):
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    conn.execute("DELETE FROM facilities WHERE id=?", (id,))
    conn.commit()
    conn.close()

    flash("Deleted successfully!")
    return redirect("/admin/facilities")

# ---------------- SCHEDULE (Today's Schedule CRUD) ----------------

# Backwards-compatible alias (old link)
@app.route("/admin/schedule")
def admin_schedule_alias():
    if not admin_only():
        return "Access Denied"
    return redirect("/admin/todays-schedule")


@app.route("/admin/todays-schedule")
def todays_schedule():

    if not admin_only():
        return "Access Denied"

    # Filter by selected date (default = today)
    selected_date = request.args.get("date")
    if not selected_date:
        from datetime import datetime
        selected_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    schedules = conn.execute(
        """
        SELECT *
        FROM todays_schedule
        WHERE date = ?
        ORDER BY time ASC, id DESC
        """,
        (selected_date,)
    ).fetchall()
    conn.close()

    return render_template("admin_schedule.html", schedules=schedules, selected_date=selected_date)


@app.route("/admin/todays-schedule/add", methods=["POST"])
def todays_schedule_add():
    if not admin_only():
        return "Access Denied"

    date = request.form["date"]
    time = request.form["time"]
    title = request.form["title"]
    location = request.form.get("location", "")
    notes = request.form.get("notes", "")

    if not date or not time or not title:
        flash("Date, Time and Title are required")
        return redirect(f"/admin/todays-schedule?date={date}")

    conn = get_db()
    conn.execute(
        "INSERT INTO todays_schedule (date, time, title, location, notes) VALUES (?, ?, ?, ?, ?)",
        (date, time, title, location, notes),
    )
    conn.commit()
    conn.close()

    flash("Schedule added!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/edit/<int:id>", methods=["GET"])
def todays_schedule_edit(id):
    if not admin_only():
        return "Access Denied"

    conn = get_db()
    item = conn.execute("SELECT * FROM todays_schedule WHERE id=?", (id,)).fetchone()
    conn.close()

    if not item:
        flash("Schedule item not found")
        return redirect("/admin/todays-schedule")

    return render_template("edit_todays_schedule.html", item=item)


@app.route("/admin/todays-schedule/update/<int:id>", methods=["POST"])
def todays_schedule_update(id):
    if not admin_only():
        return "Access Denied"

    date = request.form["date"]
    time = request.form["time"]
    title = request.form["title"]
    location = request.form.get("location", "")
    notes = request.form.get("notes", "")

    conn = get_db()
    conn.execute(
        """
        UPDATE todays_schedule
        SET date=?, time=?, title=?, location=?, notes=?
        WHERE id=?
        """,
        (date, time, title, location, notes, id),
    )
    conn.commit()
    conn.close()

    flash("Schedule updated!")
    return redirect(f"/admin/todays-schedule?date={date}")


@app.route("/admin/todays-schedule/delete/<int:id>", methods=["POST"])
def todays_schedule_delete(id):
    if not admin_only():
        return "Access Denied"

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
    if not admin_only():
        return "Access Denied"
    return render_template("admin_activity.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
