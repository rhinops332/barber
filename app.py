import os
import requests
import json
from datetime import datetime, timedelta, time
from flask import Flask, request, jsonify, render_template as original_render_template, redirect, session, g
import smtplib
from email.message import EmailMessage
import re
import shutil
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# --- קבצים ---
BUSINESSES_FILE = "businesses.json"

services_prices = {
    "Men's Haircut": 80,
    "Women's Haircut": 120,
    "Blow Dry": 70,
    "Color": 250
}

# --- פונקציות עזר ---

def load_json(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_text(filename):
    if not os.path.exists(filename):
        return ""
    with open(filename, "r", encoding="utf-8") as f:
        return f.read()

def save_text(filename, content):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content.strip())

def load_businesses():
    return load_json(BUSINESSES_FILE)

def save_businesses(data):
    save_json(BUSINESSES_FILE, data)

# --- פונקציות עסקיות לכל עסק ---

# --- מסד נתונים במקום קבצים ---

def load_weekly_schedule(business_name):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {}
    business_id = row[0]

    cur.execute("SELECT day, start_time FROM weekly_schedule WHERE business_id = %s", (business_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    weekly_schedule = {str(i): [] for i in range(7)}
    for day, start_time in rows:
        if start_time:
            weekly_schedule[str(day)].append(start_time.strftime("%H:%M"))

    return weekly_schedule


def save_weekly_schedule(business_name, schedule_data):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    business_id = row[0]

    cur.execute("DELETE FROM weekly_schedule WHERE business_id = %s", (business_id,))

    for day, times in schedule_data.items():
        for time in times:
            cur.execute(
                "INSERT INTO weekly_schedule (business_id, day, start_time, end_time) VALUES (%s, %s, %s, %s)",
                (business_id, day, time, time)
            )

    conn.commit()
    cur.close()
    conn.close()


def load_overrides(business_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {}
    business_id = row[0]

    cur.execute("SELECT date, start_time, type FROM overrides WHERE business_id = %s", (business_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    overrides = {}
    for date_val, start_time, typ in rows:
        if not start_time or not date_val:
            continue
        date_str = date_val.strftime("%Y-%m-%d")
        time_str = start_time.strftime("%H:%M")
        if date_str not in overrides:
            overrides[date_str] = {"booked": [], "add": [], "remove": []}
        if typ == "booked":
            overrides[date_str]["booked"].append(time_str)
        elif typ == "add":
            overrides[date_str]["add"].append(time_str)
        elif typ == "remove":
            overrides[date_str]["remove"].append(time_str)
    return overrides


def save_overrides(business_name, overrides_data):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    business_id = row[0]

    # מוחקים את כל השורות הקיימות עבור העסק
    cur.execute("DELETE FROM overrides WHERE business_id = %s", (business_id,))

    # עכשיו מכניסים את כל סוגי השעות לפי סוג
    for date_str, info in overrides_data.items():
        for key in ["booked", "add", "remove"]:
            for time_val in info.get(key, []):
                cur.execute(
                    "INSERT INTO overrides (business_id, date, start_time, end_time, type) VALUES (%s, %s, %s, %s, %s)",
                    (business_id, date_str, time_val, time_val, key)
                )

    conn.commit()
    cur.close()
    conn.close()



def load_appointments(business_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {}
    business_id = row[0]

    cur.execute("SELECT name, phone, date, time, service, price FROM appointments WHERE business_id = %s", (business_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    appointments = {}
    for name, phone, date_val, time_val, service, price in rows:
        if not date_val or not time_val:
            continue
        date_str = date_val.strftime("%Y-%m-%d")
        time_str = time_val.strftime("%H:%M")
        appointments.setdefault(date_str, []).append({
            "name": name or "",
            "phone": phone or "",
            "time": time_str,
            "service": service or "",
            "price": price or 0
        })
    return appointments

def save_appointments(business_name, appointments_data):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    business_id = row[0]

    cur.execute("DELETE FROM appointments WHERE business_id = %s", (business_id,))

    for date_str, appts in appointments_data.items():  # <-- חייב לעבור לפי תאריכים
        for appt in appts:
            cur.execute(
                "INSERT INTO appointments (business_id, name, phone, date, time, service, price) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    business_id,
                    appt.get('name'),
                    appt.get('phone'),
                    date_str,  # <-- תאריך הוא המפתח
                    appt.get('time'),
                    appt.get('service'),
                    appt.get('price')
                )
            )

    conn.commit()
    cur.close()
    conn.close()


def load_bot_knowledge(business_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return ""
    business_id = row[0]

    cur.execute("SELECT content FROM bot_knowledge WHERE business_id = %s", (business_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else ""


def save_bot_knowledge(business_name, content):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    business_id = row[0]

    cur.execute("UPDATE bot_knowledge SET content=%s WHERE business_id=%s", (content, business_id))
    conn.commit()
    cur.close()
    conn.close()


# --- חיבור למסד ---

def get_db_connection():
    conn = psycopg2.connect(
        host="dpg-d2gamrndiees73dabd0g-a.frankfurt-postgres.render.com",
        port=5432,
        database="booking_app_tx3i",
        user="booking_app_tx3i_user",
        password="MRWYtWCxlO4azGBf6Iwo6AdP99aSmsxY"
    )
    return conn

# --- פונקציות למסד ---

def create_business_in_db(business_name, username, password_hash, email="", phone=""):
    conn = get_db_connection()
    cur = conn.cursor()
    # יצירת העסק
    cur.execute("""
        INSERT INTO businesses (name, username, password_hash, email, phone)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (business_name, username, password_hash, email, phone))
    business_id = cur.fetchone()[0]

    # יצירת שגרה שבועית כברירת מחדל
    default_schedule = create_default_weekly_schedule()
    for day, slots in default_schedule.items():
        for slot in slots:
            cur.execute("""
                INSERT INTO weekly_schedule (business_id, day, start_time, end_time)
                VALUES (%s, %s, %s, %s)
            """, (business_id, day, slot['start_time'], slot['end_time']))

    # יצירת רשומות ריקות לטבלאות אחרות
    for table in ["appointments", "overrides", "bot_knowledge"]:
        cur.execute(f"INSERT INTO {table} (business_id) VALUES (%s)", (business_id,))

    conn.commit()
    cur.close()
    conn.close()
    print(f"עסק '{business_name}' נוצר במסד עם ID = {business_id}")

def get_business_details(username, password):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, email, phone, password_hash
        FROM businesses
        WHERE username = %s
    """, (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and check_password_hash(row[4], password):
        business_id, business_name, email, phone, _ = row
        return business_name, email, phone, business_id
    return None, None, None, None

# --- יצירת שגרה שבועית ברירת מחדל ---

def create_default_weekly_schedule():
    schedule = {}
    # ימים 0-6
    for day in range(7):
        slots = []
        start_hour = 8
        end_hour = 20
        blocked_start = 14  # שעה שמתחילים חסימה
        blocked_end = 16    # שעה שמסתיים החסימה
        current = datetime.combine(datetime.today(), time(start_hour, 0))
        while current.time() < time(end_hour, 0):
            slot_end = (current + timedelta(minutes=30)).time()
            # בדיקה אם התור בתוך שעות החסומות
            if not (time(blocked_start, 0) <= current.time() < time(blocked_end, 0)):
                slots.append({
                    "start_time": current.time(),
                    "end_time": slot_end
                })
            current += timedelta(minutes=30)
        schedule[day] = slots
    return schedule

# --- ניהול שבועי ושינויים ---

def get_booked_times(appointments):
    bookings = {}
    for date_str, appts in appointments.items():
        for appt in appts:
            bookings.setdefault(date_str, []).append(appt["time"])
    return bookings


def generate_week_slots(business_name, with_sources=False):
    weekly_schedule = load_weekly_schedule(business_name)
    overrides = load_overrides(business_name)
    appointments = load_appointments(business_name)
    bookings = get_booked_times(appointments)
    today = datetime.today()
    week_slots = {}
    heb_days = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

    for i in range(7):
        current_date = today + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        weekday = current_date.weekday()
        day_name = heb_days[weekday]

        day_key = str(weekday)
        scheduled = weekly_schedule.get(day_key, [])
        override = overrides.get(date_str, {"add": [], "remove": [], "edit": []})
        added = override.get("add", [])
        removed = override.get("remove", [])
        edits = override.get("edit", [])
        disabled_day = removed == ["__all__"]

        booked_times = bookings.get(date_str, [])
        edited_to_times = [edit['to'] for edit in edits]
        edited_from_times = [edit['from'] for edit in edits]

        all_times = sorted(set(scheduled + added + edited_to_times))
        final_times = []

        for t in all_times:
            if t in edited_to_times:
                if with_sources:
                    final_times.append({"time": t, "available": True, "source": "edited"})
                else:
                    final_times.append({"time": t, "available": True})
                continue
            if t in edited_from_times:
                continue
            available = not (disabled_day or t in removed or t in booked_times)
            if with_sources:
                source = "base"
                if t in booked_times:
                    source = "booked"
                elif t in added and t not in scheduled:
                    source = "added"
                elif t in scheduled and (t in removed or disabled_day):
                    source = "disabled"
                final_times.append({"time": t, "available": available, "source": source})
            else:
                if available:
                    final_times.append({"time": t, "available": True})
        week_slots[date_str] = {"day_name": day_name, "times": final_times}
    return week_slots

def is_slot_available(business_name, date, time):
    week_slots = generate_week_slots(business_name)
    day_info = week_slots.get(date)
    if not day_info:
        return False
    for t in day_info["times"]:
        if t["time"] == time and t.get("available", True):
            return True
    return False

def get_source(t, scheduled, added, removed, edits, disabled_day, booked_times):
    if t in booked_times:
        return "booked"          
    for edit in edits:
        if t == edit['to']:
            return "edited"      
    if t in added and t not in scheduled:
        return "added"           
    if t in scheduled and (t in removed or disabled_day):
        return "disabled"        
    return "base"                

# --- לפני כל בקשה ---

@app.before_request
def before_request():
    g.username = session.get('username')
    g.is_admin = session.get('is_admin')
    g.is_host = session.get('is_host')

def render_template(template_name_or_list, **context):
    context['session'] = {
        'username': g.get('username'),
        'is_admin': g.get('is_admin'),
        'is_host': g.get('is_host')
    }
    return original_render_template(template_name_or_list, **context)

# --- ניהול התחברות ---

@app.route("/login", methods=['GET', 'POST'])
def login():
    error = None
    host_user = os.environ.get('HOST_USERNAME')
    host_pass = os.environ.get('HOST_PASSWORD')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        # בדיקה של ההוסט
        if username == host_user and password == host_pass:
            session['username'] = username
            session['is_host'] = True
            session['is_admin'] = True
            return redirect('/host_command')

        # בדיקה של עסק רגיל
        business_name, email, phone, business_id = get_business_details(username, password)
        if business_name:
            session['username'] = username
            session['is_host'] = False
            session['is_admin'] = True
            session['business_name'] = business_name
            session['business_email'] = email
            session['business_phone'] = phone
            return redirect('/main_admin')

        error = "שם משתמש או סיסמה שגויים"

    return render_template('login.html', error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route('/dashboard')
def dashboard():
    if not session.get('is_admin'):
        return redirect('/login')

    business_name = session.get('business_name')
    if not business_name:
        return redirect('/login')  # או עמוד שגיאה מתאים

    email = session.get('email')
    phone = session.get('phone')
    name = session.get('name')  # אחרת ה־f-string שלך ישבר

    return f"שלום {name}, המייל שלך: {email}, הטלפון: {phone}"

# --- דף ניהול ראשי ---

@app.route('/host_command', methods=['GET'])
def host_command():
    if not session.get('is_host'):
        return redirect('/login')
    businesses = load_businesses()
    return render_template('host_command.html', businesses=businesses)

@app.route('/add_business', methods=['POST'])
def add_business():
    if not session.get('is_host'):
        return redirect('/login')

    business_name = request.form.get('business_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()

    password_hash = generate_password_hash(password)

    # ולידציות בסיסיות
    if not all([business_name, username, password, phone, email]):
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error="יש למלא את כל השדות")

    businesses = load_businesses()

    # מניעת כפילויות
    if any(b.get("username") == username for b in businesses):
        return render_template('host_command.html',
                               businesses=businesses,
                               error="שם המשתמש כבר בשימוש")

    # יצירת קבצים לתיקיית העסק
    try:
        create_business_in_db(business_name, username, password_hash, email, phone)
    except Exception as e:
        return render_template('host_command.html',
                               businesses=businesses,
                               error=f"שגיאה ביצירת קבצי העסק: {e}")

    # הוספה לרשומת העסקים (סיסמה בהאש)
    businesses.append({
        "business_name": business_name,
        "username": username,
        "password_hash": generate_password_hash(password),
        "phone": phone,
        "email": email,
        "created_at": datetime.utcnow().isoformat() + "Z"
    })
    save_businesses(businesses)

    return render_template('host_command.html',
                           businesses=businesses,
                           msg=f"העסק '{business_name}' נוצר בהצלחה")

@app.route('/delete_business', methods=['POST'])
def delete_business(business_name):
    if not session.get('is_host'):
        return redirect('/login')

    username = request.form.get('username', '').strip()
    businesses = load_businesses()
    entry = next((b for b in businesses if b.get("username") == username), None)

    if not entry:
        return render_template('host_command.html',
                               businesses=businesses,
                               error="העסק לא נמצא")

    # הסרת הרשומה
    businesses = [b for b in businesses if b.get("username") != username]
    save_businesses(businesses)

    # מחיקת תיקיית העסק לפי שם העסק
    try:
        bname = entry.get("business_name")
        bpath = os.path.join(BUSINESSES_ROOT, bname)
        if os.path.isdir(bpath):
            shutil.rmtree(bpath)
    except Exception as e:
        return render_template('host_command.html',
                               businesses=businesses,
                               error=f"העסק הוסר מהרשימה, אך מחיקת התיקייה נכשלה: {e}")

    return render_template('host_command.html',
                           businesses=businesses,
                           msg="העסק נמחק בהצלחה")


@app.route("/main_admin")
def main_admin():
    if not session.get('username') or session.get('is_host'):
        return redirect('/login')
    
    business_name = session.get('business_name', 'עסק לא ידוע')
    return render_template('main_admin.html', business_name=business_name)


@app.route("/admin_routine")
def admin_routine():
    if not session.get("is_admin"):
        return redirect("/login")
        
    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    weekly_schedule = load_weekly_schedule(business_name)

    return render_template("admin_routine.html", weekly_schedule=weekly_schedule)

                          
@app.route("/admin_overrides")
def admin_overrides():
    if not session.get("is_admin"):
        return redirect("/login")

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    weekly_schedule = load_weekly_schedule(business_name)
    overrides = load_overrides(business_name)

    today = datetime.today()
    week_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    hebrew_day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    date_map = {}
    for d_str in week_dates:
        d = datetime.strptime(d_str, "%Y-%m-%d")
        day_name = hebrew_day_names[d.weekday()]
        date_map[d_str] = f"{d.strftime('%-d.%m')} ({day_name})"

    week_slots = generate_week_slots(business_name, with_sources=True)

    return render_template("admin_overrides.html",
                           overrides=overrides,
                           base_schedule=weekly_schedule,
                           week_dates=week_dates,
                           date_map=date_map,
                           week_slots=week_slots)

                           
@app.route("/appointments")
def admin_appointments():
    if not session.get("is_admin"):
        return redirect("/login")

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    appointments = load_appointments(business_name)
    return render_template("admin_appointments.html", appointments=appointments)

@app.route("/orders")
def orders():
    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    week_slots = generate_week_slots(business_name)
    return render_template("orders.html", week_slots=week_slots)

# --- ניהול שגרה שבועית ---

@app.route("/weekly_schedule", methods=["POST"])
def update_weekly_schedule():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    action = data.get("action")
    day_key = data.get("day_key")
    time = data.get("time")
    new_time = data.get("new_time")

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    weekly_schedule = load_weekly_schedule(business_name)

    if day_key not in [str(i) for i in range(7)]:
        return jsonify({"error": "Invalid day key"}), 400

    if action == "enable_day":
        if day_key not in weekly_schedule:
            weekly_schedule[day_key] = []

        save_weekly_schedule(business_name, weekly_schedule)
        return jsonify({"success": True})

    if action == "disable_day":
        weekly_schedule[day_key] = []
        save_weekly_schedule(business_name, weekly_schedule)
        return jsonify({"success": True})

    day_times = weekly_schedule.get(day_key, [])

    if action == "add" and time:
        if time not in day_times:
            day_times.append(time)
            day_times.sort()
            weekly_schedule[day_key] = day_times
    elif action == "remove" and time:
        if time in day_times:
            day_times.remove(time)
            weekly_schedule[day_key] = day_times
    elif action == "edit" and time and new_time:
        if time in day_times:
            day_times.remove(time)
            if new_time not in day_times:
                day_times.append(new_time)
                day_times.sort()
            weekly_schedule[day_key] = day_times
    else:
        return jsonify({"error": "Invalid action or missing time"}), 400

    save_weekly_schedule(business_name, weekly_schedule)
    return jsonify({"message": "Weekly schedule updated", "weekly_schedule": weekly_schedule})

@app.route("/weekly_toggle_day", methods=["POST"])
def toggle_weekly_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    day_key = data.get("day_key")
    enabled = data.get("enabled")

    if day_key not in [str(i) for i in range(7)]:
        return jsonify({"error": "Invalid day key"}), 400

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    weekly_schedule = load_weekly_schedule(business_name)
    weekly_schedule[day_key] = [] if not enabled else weekly_schedule.get(day_key, [])
    save_weekly_schedule(business_name, weekly_schedule)

    return jsonify({"message": "Day updated", "weekly_schedule": weekly_schedule})


# --- ניהול שינויים חד פעמיים (overrides) ---

@app.route("/overrides", methods=["POST"])
def update_overrides():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    action = data.get("action")
    date = data.get("date")
    time = data.get("time")
    new_time = data.get("new_time")

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    overrides = load_overrides(business_name)

    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    if action == "remove_many":
        times = data.get("times", [])
        for t in times:
            if t not in overrides[date]["remove"]:
                overrides[date]["remove"].append(t)
            if t in overrides[date]["add"]:
                overrides[date]["add"].remove(t)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Multiple times removed", "overrides": overrides})

    elif action == "add" and time:
        if time not in overrides[date]["add"]:
            overrides[date]["add"].append(time)
        if time in overrides[date]["remove"]:
            overrides[date]["remove"].remove(time)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Time added", "overrides": overrides})

    elif action == "remove" and time:
        if "remove" not in overrides[date]:
            overrides[date]["remove"] = []
        if "add" not in overrides[date]:
            overrides[date]["add"] = []
        if time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(time)
        if time in overrides[date]["add"]:
            overrides[date]["add"].remove(time)
        if "edit" in overrides[date]:
            overrides[date]["edit"] = [
                e for e in overrides[date]["edit"]
                if e.get("from") != time and e.get("to") != time
            ]
            if not overrides[date]["edit"]:
                overrides[date].pop("edit", None)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Time removed", "overrides": overrides})

    elif action == "edit" and time and new_time:
        if time == new_time:
            return jsonify({"message": "No changes made"})

        # ודא שהיום קיים במבנה
        if date not in overrides:
            overrides[date] = {"add": [], "remove": []}

        # הסרת השעה הישנה מכל הרשימות
        if "add" in overrides[date] and time in overrides[date]["add"]:
            overrides[date]["add"].remove(time)
        if "remove" in overrides[date] and time in overrides[date]["remove"]:
            overrides[date]["remove"].remove(time)
        if "edit" in overrides[date]:
            overrides[date]["edit"] = [
                e for e in overrides[date]["edit"]
                if e.get("from") != time and e.get("to") != time
            ]
            if not overrides[date]["edit"]:
                overrides[date].pop("edit", None)

        # הוספת השעה החדשה ל-remove (כדי שתוצג כאפורה)
        if "remove" not in overrides[date]:
            overrides[date]["remove"] = []
        if new_time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(new_time)

        save_overrides(business_name, overrides)
        return jsonify({"message": "Time edited", "overrides": overrides})

    elif action == "clear" and date:
        if date in overrides:
            overrides.pop(date)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Day overrides cleared", "overrides": overrides})

    elif action == "disable_day" and date:
        overrides[date] = {"add": [], "remove": ["__all__"]}
        save_overrides(business_name, overrides)
        return jsonify({"message": "Day disabled", "overrides": overrides})

    elif action == "revert" and date and time:
        if date in overrides:
            if "add" in overrides[date] and time in overrides[date]["add"]:
                overrides[date]["add"].remove(time)

            if "remove" in overrides[date] and time in overrides[date]["remove"]:
                overrides[date]["remove"].remove(time)

            if "edit" in overrides[date]:
                overrides[date]["edit"] = [
                    e for e in overrides[date]["edit"]
                    if e.get("to") != time and e.get("from") != time
                ]
                if not overrides[date]["edit"]:
                    overrides[date].pop("edit", None)

            if not overrides[date].get("add") and not overrides[date].get("remove") and not overrides[date].get("edit"):
                overrides.pop(date)

        save_overrides(business_name, overrides)
        return jsonify({"message": "Time reverted", "overrides": overrides})

    else:
        return jsonify({"error": "Invalid action or missing parameters"}), 400


@app.route("/overrides_toggle_day", methods=["POST"])
def toggle_override_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    date = data.get("date")
    enabled = data.get("enabled")

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    overrides = load_overrides(business_name)

    if not enabled:
        overrides[date] = {"add": [], "remove": ["__all__"]}
    else:
        if date in overrides and overrides[date].get("remove") == ["__all__"]:
            overrides.pop(date)

    save_overrides(business_name, overrides)
    return jsonify({"message": "Day override toggled", "overrides": overrides})

@app.route('/admin/one-time/toggle_day', methods=['POST'])
def toggle_day():
    data = request.json
    date = data['date']
    one_time = load_one_time_changes()
    if date not in one_time:
        return jsonify({'error': 'Date not found'}), 404

    all_disabled = all(not slot['available'] for slot in one_time[date])
    for slot in one_time[date]:
        slot['available'] = not all_disabled

    save_one_time_changes(one_time)
    return jsonify({'message': 'Day toggled successfully'})

@app.route('/admin/one-time/delete', methods=['POST'])
def delete_slot():
    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    if date in one_time:
        one_time[date] = [slot for slot in one_time[date] if slot['time'] != time]
        save_one_time_changes(one_time)
    return jsonify({'message': 'Slot deleted'})

@app.route('/admin/one-time/edit', methods=['POST'])
def edit_slot():
    data = request.json
    date, old_time, new_time = data['date'], data['old_time'], data['new_time']
    one_time = load_one_time_changes()
    for slot in one_time.get(date, []):
        if slot['time'] == old_time:
            slot['time'] = new_time
            break
    save_one_time_changes(one_time)
    return jsonify({'message': 'Slot edited'})

@app.route('/admin/one-time/toggle_slot', methods=['POST'])
def toggle_slot():
    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    for slot in one_time.get(date, []):
        if slot['time'] == time:
            slot['available'] = not slot['available']
            break
    save_one_time_changes(one_time)
    return jsonify({'message': 'Slot toggled'})

@app.route('/admin/one-time/add', methods=['POST'])
def add_slot():
    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    one_time.setdefault(date, []).append({'time': time, 'available': True})
    save_one_time_changes(one_time)
    return jsonify({'message': 'Slot added'})

@app.route('/appointment_details')
def appointment_details():
    date = request.args.get('date')
    time = request.args.get('time')

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    appointments = load_appointments(business_name)

    if date in appointments:
        for appt in appointments[date]:
            if appt.get('time') == time:
                return render_template('appointment_details.html', appointment=appt)

    return "פרטי ההזמנה לא נמצאו", 404
    
# --- ניהול טקסט ידע של הבוט ---

@app.route("/bot_knowledge", methods=["GET", "POST"])
def bot_knowledge():
    if not session.get("is_admin"):
        return redirect("/login")

    if request.method == "POST":
        content = request.form.get("content", "")
        business_name = session.get('business_name')
        if not business_name: 
            return redirect("/login")
        save_business_json(session.get('business_name'), "bot_knowledge.json", content)
        return redirect("/main_admin")

    content = load_business_json(session.get('business_name'), "bot_knowledge.json")
    return render_template("bot_knowledge.html", content=content)

# --- ניהול הזמנות ---

@app.route("/book", methods=["POST"])
def book_appointment():
    data = request.get_json()
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    date = data.get("date", "").strip()
    time = data.get("time", "").strip()
    service = data.get("service", "").strip()

    if not all([name, phone, date, time, service]):
        return jsonify({"error": "Missing fields"}), 400

    if service not in services_prices:
        return jsonify({"error": "Unknown service"}), 400

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")

    if not is_slot_available(business_name, date, time):
        return jsonify({"error": "This time slot is not available"}), 400

    appointments = load_appointments(business_name)
    # ממירה את הרשימה של תורים למילון לפי תאריך
    booked = get_booked_times(appointments)
    date_appointments = booked.get(date, [])

    if time in date_appointments:
        return jsonify({"error": "This time slot is already booked"}), 400

    # מוסיפים תור חדש
    appointment = {
        "name": name,
        "phone": phone,
        "date": date,
        "time": time,
        "service": service,
        "price": services_prices[service]
    }

    date_appointments = appointments.get(date, [])
    date_appointments.append(appointment)
    appointments[date] = date_appointments
    save_appointments(business_name, appointments)

    # מעדכנים overrides
    overrides = load_overrides(business_name)
    if date not in overrides:
        overrides[date] = {"booked": [], "add": [], "remove": []}
    if "booked" not in overrides[date]:
        overrides[date]["booked"] = []

    overrides[date]["booked"].append({
        "time": time,
        "name": name,
        "phone": phone,
        "service": service
    })
    # הסרת זמן מ-add אם קיים
    if time in overrides[date]["add"]:
        overrides[date]["add"].remove(time)
    # הוספת זמן ל-remove אם לא קיים
    if time not in overrides[date]["remove"]:
        overrides[date]["remove"].append(time)

    save_overrides(business_name, overrides)

    try:
        send_email(name, phone, date, time, service, services_prices[service])
    except Exception as e:
        print("Error sending email:", e)

    return jsonify({
        "message": f"Appointment booked for {date} at {time} for {service}.",
        "date": date,
        "time": time,
        "service": service,
        "can_cancel": True,
        "cancel_endpoint": "/cancel_appointment"
    })

@app.route('/cancel_appointment', methods=['POST'])
def cancel_appointment():
    data = request.get_json()
    date = data.get('date')
    time = data.get('time')
    name = data.get('name')
    phone = data.get('phone')
    
    try:
        appointments = load_business_json(session.get('business_name'), "appointments.json")
    except FileNotFoundError:
        appointments = {}

    day_appointments = appointments.get(date, [])

    new_day_appointments = [
        appt for appt in day_appointments
        if not (appt['time'] == time and appt['name'] == name and appt['phone'] == phone)
    ]

    if len(new_day_appointments) == len(day_appointments):
        return jsonify({'error': 'Appointment not found'}), 404

    appointments[date] = new_day_appointments

    save_business_json(session.get('business_name'), "appointments.json", appointments)


    try:
        with open(OVERRIDES_FILE, 'r', encoding='utf-8') as f:
            overrides = json.load(f)
    except FileNotFoundError:
        overrides = {}

    if date not in overrides:
        overrides[date] = {"add": [], "remove": [], "edit": []}

    if time in overrides[date].get("remove", []):
        overrides[date]["remove"].remove(time)

    if time not in overrides[date].get("add", []):
        overrides[date]["add"].append(time)

    with open(OVERRIDES_FILE, 'w', encoding='utf-8') as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)

    return jsonify({'message': f'Appointment on {date} at {time} canceled successfully.'})

# --- שליחת אימייל ---

def send_email(name, phone, date, time, service, price):
    EMAIL_USER = os.environ.get("EMAIL_USER")
    EMAIL_PASS = os.environ.get("EMAIL_PASS")
    if not EMAIL_USER or not EMAIL_PASS:
        print("Missing EMAIL_USER or EMAIL_PASS environment variables")
        return

    msg = EmailMessage()
    msg.set_content(f"""
New appointment booked:

Name: {name}
Phone: {phone}
Date: {date}
Time: {time}
Service: {service}
Price: {price}₪
""")
    msg['Subject'] = f'New Appointment - {name}'
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_USER

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully")
    except Exception as e:
        print("Failed to send email:", e)

# --- דף הצגת תורים (מנהל בלבד) ---

@app.route("/availability")
def availability():
    business_name = session.get('business_name')
    if not business_name:
        return jsonify({"error": "Business name not set"}), 400

    week_slots = generate_week_slots(business_name)
    return jsonify(week_slots)
# --- דף הבית ---

@app.route("/")
def index():
    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")  # או עמוד ברירת מחדל

    week_slots = generate_week_slots(business_name)
    return render_template("index.html", week_slots=week_slots, services=services_prices)
# --- API - שאלות לבוט ---

@app.route("/ask", methods=["POST"])
def ask_bot():
    data = request.get_json()
    question = data.get("message", "").strip()

    if not question:
        return jsonify({"answer": "אנא כתוב שאלה."})

    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    knowledge_text = load_bot_knowledge(business_name)

    messages = [
        {"role": "system", "content": "You are a helpful assistant for a hair salon booking system."},
        {"role": "system", "content": f"Additional info: {knowledge_text}"},
        {"role": "user", "content": question}
    ]

    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        return jsonify({"error": "Missing GitHub API token"}), 500

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "openai/gpt-4.1",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 200
    }

    try:
        response = requests.post(
            "https://models.github.ai/inference/v1/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        output = response.json()
        answer = output["choices"][0]["message"]["content"].strip()
        return jsonify({"answer": answer})
    except Exception as e:
        print("Error calling GitHub AI API:", e)
        fallback_answer = "מצטער, לא הצלחתי לעבד את השאלה כרגע."
        return jsonify({"answer": fallback_answer})

# --- הפעלת השרת ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
