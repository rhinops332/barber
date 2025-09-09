import os
import requests
import json
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, render_template as original_render_template, redirect, session, g
import smtplib
from email.message import EmailMessage
import re
import shutil
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2.extras import RealDictCursor


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# --- קבצים ---
BUSINESSES_FILE = "businesses.json"
# נתיב תיקיית כל העסקים
BUSINESSES_ROOT = os.path.join(os.getcwd(), "businesses")


services_prices = {
    "Men's Haircut": 80,
    "Women's Haircut": 120,
    "Blow Dry": 70,
    "Color": 250
}


# --- פונקציות עזר ---

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
            overrides[date_str] = {"booked": [], "add": [], "remove": [], "edit_from": [], "edit_to": []}

        if typ == "booked":
            overrides[date_str]["booked"].append(time_str)
        elif typ == "add":
            overrides[date_str]["add"].append(time_str)
        elif typ == "remove":
            overrides[date_str]["remove"].append(time_str)
        elif typ == "edit_from":
            overrides[date_str]["edit_from"].append(time_str)
        elif typ == "edit_to":
            overrides[date_str]["edit_to"].append(time_str)

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

    # מכניסים את כל סוגי השעות כולל עריכות
    for date_str, info in overrides_data.items():
        for key in ["booked", "add", "remove", "edit_from", "edit_to"]:
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

def load_businesses():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, username, password_hash, email, phone FROM businesses")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    businesses = []
    for bid, name, username, password_hash, email, phone in rows:
        businesses.append({
            "id": bid,
            "name": name,
            "username": username,
            "password_hash": password_hash,
            "email": email,
            "phone": phone
        })
    return businesses


def save_businesses(businesses_data):
    conn = get_db_connection()
    cur = conn.cursor()

    # מוחקים הכל
    cur.execute("DELETE FROM businesses")

    # מכניסים מחדש (בלי id כי הוא אוטומטי)
    for biz in businesses_data:
        cur.execute(
            "INSERT INTO businesses (name, username, password_hash, email, phone) VALUES (%s, %s, %s, %s, %s)",
            (biz.get("name"), biz.get("username"),
             biz.get("password_hash"), biz.get("email"), biz.get("phone"))
        )

    conn.commit()
    cur.close()
    conn.close()



def load_business_settings(business_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM design_settings WHERE business_id = %s", (business_id,))
    row = cur.fetchone()
    colnames = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()
    if row:
        return dict(zip(colnames, row))
    return None


def save_business_settings(business_id, settings):
    conn = get_db_connection()
    cur = conn.cursor()

    # נבנה רשימת עדכונים דינמית מכל המפתחות שקיימים ב־settings
    columns = [f"{key} = %s" for key in settings.keys()]
    values = list(settings.values())

    query = f"UPDATE design_settings SET {', '.join(columns)} WHERE business_id = %s"
    cur.execute(query, values + [business_id])

    conn.commit()
    cur.close()
    conn.close()


# --- ניקוי המסד ומחיקת מידע מיותר ---

def disable_past_hours():
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M")

    conn = get_db_connection()
    cur = conn.cursor()

    # לוקח את כל העסקים
    cur.execute("SELECT id FROM businesses")
    businesses = [row[0] for row in cur.fetchall()]

    for business_id in businesses:
        # טוען את השגרה השבועית
        cur.execute("SELECT day, start_time FROM weekly_schedule WHERE business_id=%s", (business_id,))
        weekly_rows = cur.fetchall()
        weekly_schedule = {str(i): [] for i in range(7)}
        for day, start_time in weekly_rows:
            if start_time:
                weekly_schedule[str(day)].append(start_time.strftime("%H:%M"))

        # טוען את השינויים הקיימים
        cur.execute("SELECT date, start_time, type FROM overrides WHERE business_id=%s", (business_id,))
        override_rows = cur.fetchall()
        overrides = {}
        for date_val, start_time_val, typ in override_rows:
            if not date_val or not start_time_val:
                continue
            date_s = date_val.strftime("%Y-%m-%d")
            time_s = start_time_val.strftime("%H:%M")
            if date_s not in overrides:
                overrides[date_s] = {"booked": [], "add": [], "remove": [], "edit_from": [], "edit_to": []}
            overrides[date_s].setdefault(typ, []).append(time_s)

        # קביעת היום הנוכחי
        weekday = str(datetime.now(tz).weekday())
        today_schedule = weekly_schedule.get(weekday, [])

        # מיזוג כל השעות להיום
        today_override = overrides.get(today_str, {"booked": [], "add": [], "remove": [], "edit_from": [], "edit_to": []})
        all_today_times = set(today_schedule + today_override.get("add", []) + today_override.get("edit_to", []))

        # מוסיף ל-remove כל שעה שעברה
        for t in all_today_times:
            if t < current_time_str and t not in today_override["remove"]:
                today_override["remove"].append(t)

        overrides[today_str] = today_override

        # מוחק את כל השורות הקיימות להיום
        cur.execute("DELETE FROM overrides WHERE business_id=%s AND date=%s", (business_id, today_str))

        # מכניס את כל השינויים המעודכנים להיום
        for key in ["booked", "add", "remove", "edit_from", "edit_to"]:
            for time_val in today_override.get(key, []):
                cur.execute(
                    "INSERT INTO overrides (business_id, date, start_time, end_time, type) VALUES (%s, %s, %s, %s, %s)",
                    (business_id, today_str, time_val, time_val, key)
                )

    conn.commit()
    cur.close()
    conn.close()
    print(f"[{now.strftime('%H:%M:%S')}] Past hours disabled for all businesses.")
    
def clear_old_info():
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)
    cutoff = now - timedelta(hours=24)

    conn = get_db_connection()
    cur = conn.cursor()

    # מחיקת appointments ישנים
    cur.execute("""
        DELETE FROM appointments
        WHERE (date::text || ' ' || time::text)::timestamp < %s
    """, (cutoff,))

    # מחיקת overrides ישנים
    cur.execute("""
        DELETE FROM overrides
        WHERE (date::text || ' ' || start_time::text)::timestamp < %s
    """, (cutoff,))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[{now.strftime('%H:%M:%S')}] Old appointments and overrides cleared.")

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

def create_default_business_settings(business_id, business_name, conn):
    cur = conn.cursor()

    # בדיקה אם כבר קיימת שורה עבור העסק
    cur.execute("SELECT id FROM design_settings WHERE business_id=%s", (business_id,))
    if cur.fetchone():
        cur.close()
        return  # כבר קיימת, לא עושים כלום

    # ערכי ברירת מחדל תואמים ל-JS / CSS
    cur.execute("""
        INSERT INTO design_settings (
            business_id,
            -- כפתורי יום
            day_button_shape, day_button_color, day_button_size,
            day_button_text_size, day_button_text_color, day_button_font_family,

            -- כפתורי שעה
            slot_button_shape, slot_button_color, slot_button_size,
            slot_button_text_size, slot_button_text_color, slot_button_font_family,

            -- כותרות
            heading_font_family, subheading_font_family,
            heading_font_size, subheading_font_size,
            heading_color, subheading_color,
            heading_text, subheading_text,

            -- רקע כללי
            body_background_color
        )
        VALUES (
            %s,
            -- יום
            '12px', '#3498db', '12px',
            '15px', '#ffffff', 'Tahoma, sans-serif',

            -- שעה
            '8px', '#2ecc71', '8px',
            '14px', '#ffffff', 'Verdana, sans-serif',

            -- כותרות
            'Georgia, serif', 'Verdana, sans-serif',
            '32px', '18px',
            '#2980b9', '#555555',
            %s, %s,

            -- רקע כללי
            '#f3f4f6'
        )
    """, (
        business_id,
        business_name,           # heading_text = שם העסק
        "בחרו תור לקביעת פגישה"     # subheading_text
    ))

    conn.commit()
    cur.close()




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
        override = overrides.get(date_str, {"add": [], "remove": [], "edit_from": [], "edit_to": []})
        added = override.get("add", [])
        removed = override.get("remove", [])
        edited_from_times = override.get("edit_from", [])
        edited_to_times = override.get("edit_to", [])
        disabled_day = removed == ["__all__"]

        booked_times = bookings.get(date_str, [])
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
                source = get_source(t, scheduled, added, removed, list(zip(edited_from_times, edited_to_times)), disabled_day, booked_times)
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
    for from_time, to_time in edits:
        if t == to_time:
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


# ---------------------- הוספת עסק ----------------------
@app.route('/add_business', methods=['POST'])
def add_business():
    if not session.get('is_host'):
        return redirect('/login')

    business_name = request.form.get('business_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()

    if not all([business_name, username, password, phone, email]):
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error="יש למלא את כל השדות")

    businesses = load_businesses()
    if any(b["username"] == username for b in businesses):
        return render_template('host_command.html',
                               businesses=businesses,
                               error="שם המשתמש כבר בשימוש")

    password_hash = generate_password_hash(password)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1️⃣ יצירת העסק בטבלת businesses
        cur.execute("""
            INSERT INTO businesses (name, username, password_hash, email, phone)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (business_name, username, password_hash, email, phone))
        business_id = cur.fetchone()[0]

        # 2️⃣ יצירת שגרה שבועית ברירת מחדל
        default_schedule = create_default_weekly_schedule()
        for day, slots in default_schedule.items():
            for slot in slots:
                cur.execute("""
                    INSERT INTO weekly_schedule (business_id, day, start_time, end_time)
                    VALUES (%s, %s, %s, %s)
                """, (business_id, day, slot['start_time'], slot['end_time']))

        # 2.1️⃣ יצירת שורה ב-business_settings עם ערכי ברירת מחדל
        create_default_business_settings(business_id,business_name, conn)

        # 3️⃣ יצירת רשומות ריקות לשאר הטבלאות
        for table in ["appointments", "overrides", "bot_knowledge"]:
            cur.execute(f"INSERT INTO {table} (business_id) VALUES (%s)", (business_id,))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error=f"שגיאה ביצירת העסק: {e}")

    # 4️⃣ יצירת תיקיית העסק
    try:
        bpath = os.path.join(BUSINESSES_ROOT, business_name)
        os.makedirs(bpath, exist_ok=True)
    except Exception as e:
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error=f"העסק נוצר במסד אך התיקייה לא נוצרה: {e}")

    return render_template('host_command.html',
                           businesses=load_businesses(),
                           msg=f"העסק '{business_name}' נוצר בהצלחה")

# ---------------------- מחיקת עסק ----------------------


@app.route('/delete_business', methods=['POST'])
def delete_business():
    if not session.get('is_host'):
        return redirect('/login')

    username = request.form.get('username', '').strip()

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # נביא את ה-id של העסק (בלי בדיקות, כי אתה בטוח שהוא קיים)
        cur.execute("SELECT id, name FROM businesses WHERE username = %s", (username,))
        business_id, business_name = cur.fetchone()

        # מחיקה מכל הטבלאות שתלויות ב-business_id
        cur.execute("DELETE FROM appointments WHERE business_id = %s", (business_id,))
        cur.execute("DELETE FROM weekly_schedule WHERE business_id = %s", (business_id,))
        cur.execute("DELETE FROM overrides WHERE business_id = %s", (business_id,))
        cur.execute("DELETE FROM bot_knowledge WHERE business_id = %s", (business_id,))
        cur.execute("DELETE FROM design_settings WHERE business_id = %s", (business_id,))

        # מחיקה מהטבלה הראשית
        cur.execute("DELETE FROM businesses WHERE username = %s", (username,))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return render_template(
            'host_command.html',
            businesses=load_businesses(),
            error=f"שגיאה במחיקת העסק מהמסד: {e}"
        )

    # מחיקת תיקיית העסק
    try:
        bpath = os.path.join(BUSINESSES_ROOT, business_name)
        if os.path.isdir(bpath):
            shutil.rmtree(bpath)
    except Exception as e:
        return render_template(
            'host_command.html',
            businesses=load_businesses(),
            error=f"העסק הוסר מהמסד, אך מחיקת התיקייה נכשלה: {e}"
        )

    return render_template(
        'host_command.html',
        businesses=load_businesses(),
        msg="העסק נמחק בהצלחה"
    )




    
@app.route("/main_admin")
def main_admin():
    if not session.get('username') or session.get('is_host'):
        return redirect('/login')

    disable_past_hours()
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

    clear_old_info()

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

    disable_past_hours()
    clear_old_info()
    week_slots = generate_week_slots(business_name)
    return render_template("orders.html", week_slots=week_slots, business_name=business_name)

@app.route("/admin_design")
def admin_design():
    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")

    conn = get_db_connection()
    cur = conn.cursor()

    # שליפת business_id לפי שם העסק
    cur.execute("SELECT id FROM businesses WHERE name=%s", (business_name,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return "Business not found", 404
    business_id = row[0]

    # שליפת הגדרות עיצוב
    cur.execute("SELECT * FROM design_settings WHERE business_id=%s", (business_id,))
    settings_row = cur.fetchone()
    conn.close()

    if not settings_row:
        return "No design settings found for this business", 404

    # רשימת עמודות כפי שהגדרת בטבלה
    columns = [
        "day_button_shape","day_button_color","day_button_size",
        "day_button_text_size","day_button_text_color","day_button_font_family",
        "slot_button_shape","slot_button_color","slot_button_size","slot_button_text_size",
        "slot_button_text_color","slot_button_font_family",
        "heading_font_family","subheading_font_family","heading_font_size","subheading_font_size",
        "heading_color","subheading_color",
        "heading_text","subheading_text",
        "body_background_color"
    ]

    # המרת השורה למילון
    design_settings = {col: settings_row[idx] for idx, col in enumerate(columns)}
    design_settings['business_name'] = business_name

    return render_template("admin_design.html", design_settings=design_settings)



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
        overrides[date] = {"booked": [], "add": [], "remove": [], "edit_from": [], "edit_to": []}

    if action == "remove_many":
        times = data.get("times", [])
        for t in times:
            if t not in overrides[date]["remove"]:
                overrides[date]["remove"].append(t)
            if t in overrides[date]["add"]:
                overrides[date]["add"].remove(t)
            if t in overrides[date]["edit_from"]:
                idx = overrides[date]["edit_from"].index(t)
                overrides[date]["edit_from"].pop(idx)
                overrides[date]["edit_to"].pop(idx)
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
        if time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(time)
        if time in overrides[date]["add"]:
            overrides[date]["add"].remove(time)
        if time in overrides[date]["edit_from"]:
            idx = overrides[date]["edit_from"].index(time)
            overrides[date]["edit_from"].pop(idx)
            overrides[date]["edit_to"].pop(idx)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Time removed", "overrides": overrides})

    elif action == "edit" and time and new_time:
        if time == new_time:
            return jsonify({"message": "No changes made"})

        # הסר אם השעה המקורית כבר קיימת ב-edit_from
        if time in overrides[date]["edit_from"]:
            idx = overrides[date]["edit_from"].index(time)
            overrides[date]["edit_from"].pop(idx)
            overrides[date]["edit_to"].pop(idx)

        # הוסף את השעה הישנה ל-edit_from ואת החדשה ל-edit_to
        overrides[date]["edit_from"].append(time)
        overrides[date]["edit_to"].append(new_time)

        # ודא שהשעה הישנה מופיעה ב-remove
        if time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(time)

        # ודא שהשעה החדשה מופיעה ב-add
        if new_time not in overrides[date]["add"]:
            overrides[date]["add"].append(new_time)

        save_overrides(business_name, overrides)
        return jsonify({"message": "Time edited", "overrides": overrides})

    elif action == "clear" and date:
        if date in overrides:
            overrides.pop(date)
        save_overrides(business_name, overrides)
        return jsonify({"message": "Day overrides cleared", "overrides": overrides})

    elif action == "disable_day" and date:
        overrides[date] = {"add": [], "remove": ["__all__"], "edit_from": [], "edit_to": []}
        save_overrides(business_name, overrides)
        return jsonify({"message": "Day disabled", "overrides": overrides})

    elif action == "revert" and date and time:
        if date in overrides:
            if time in overrides[date]["add"]:
                overrides[date]["add"].remove(time)
            if time in overrides[date]["remove"]:
                overrides[date]["remove"].remove(time)
            if time in overrides[date]["edit_from"]:
                idx = overrides[date]["edit_from"].index(time)
                overrides[date]["edit_from"].pop(idx)
                overrides[date]["edit_to"].pop(idx)
            if not overrides[date]["add"] and not overrides[date]["remove"] and not overrides[date]["edit_from"]:
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

    # טען תורים קיימים
    appointments = load_appointments(business_name)
    date_appointments = appointments.get(date, [])

    # בדיקה אם השעה כבר קיימת
    if any(a["time"] == time for a in date_appointments):
        return jsonify({"error": "This time slot is already booked"}), 400

    # הוספת תור חדש
    appointment = {
        "name": name,
        "phone": phone,
        "date": date,
        "time": time,
        "service": service,
        "price": services_prices[service]
    }
    date_appointments.append(appointment)
    appointments[date] = date_appointments
    save_appointments(business_name, appointments)

    # טען overrides
    overrides = load_overrides(business_name)
    if date not in overrides:
        overrides[date] = {"booked": [], "add": [], "remove": [], "edit_from": [], "edit_to": []}

    # ודא שהשעה מופיעה ב-booked כ-string
    if time not in overrides[date]["booked"]:
        overrides[date]["booked"].append(time)

    # הסרת זמן מ-add אם קיים
    if time in overrides[date]["add"]:
        overrides[date]["add"].remove(time)
    # הוספת זמן ל-remove אם לא קיים
    if time not in overrides[date]["remove"]:
        overrides[date]["remove"].append(time)

    save_overrides(business_name, overrides)

    # שליחת מייל (אם מוגדר)
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


# --- עיצוב דף ההזמנות ---

@app.route("/business_settings", methods=["GET", "POST"])
def business_settings_route():
    business_name = session.get('business_name')
    if not business_name:
        return jsonify({"error": "Business not logged in"}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM businesses WHERE name = %s", (business_name,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return jsonify({"error": "Business not found"}), 404
    business_id = row[0]

    if request.method == "GET":
        cur.execute("SELECT * FROM design_settings WHERE business_id = %s", (business_id,))
        settings = cur.fetchone()
        if not settings:
            cur.close()
            conn.close()
            return jsonify({"error": "Business settings not found"}), 404
        colnames = [desc[0] for desc in cur.description]
        settings_dict = dict(zip(colnames, settings))
        # פירוק JSON לשדות קריאים
        if settings_dict.get("day_names"):
            settings_dict["day_names"] = json.loads(settings_dict["day_names"])
        if settings_dict.get("layout_json"):
            settings_dict["layout_json"] = json.loads(settings_dict["layout_json"])
        cur.close()
        conn.close()
        return jsonify(settings_dict)

    if request.method == "POST":
        data = request.json
        if not data:
            cur.close()
            conn.close()
            return jsonify({"error": "No data provided"}), 400

        columns = [
           "day_button_shape","day_button_color","day_button_size",
           "day_button_text_size","day_button_text_color","day_button_font_family",
           "slot_button_shape","slot_button_color","slot_button_size","slot_button_text_size",
           "slot_button_text_color","slot_button_font_family",
           "heading_font_family","subheading_font_family","heading_font_size","subheading_font_size",
           "heading_color","subheading_color",
           "heading_text","subheading_text",
           "body_background_color"
       ]


        # בדיקה אם כבר קיימת שורה
        cur.execute("SELECT 1 FROM design_settings WHERE business_id = %s", (business_id,))
        exists = cur.fetchone()

        values = []
        for col in columns:
            val = data.get(col)
            if col in ["day_names", "layout_json"]:
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except:
                        val = [d.strip() for d in val.split(",")] if col=="day_names" else {}
                val = json.dumps(val)
            values.append(val)

        if exists:
            placeholders = ", ".join([f"{col} = %s" for col in columns])
            cur.execute(f"UPDATE design_settings SET {placeholders} WHERE business_id = %s", values + [business_id])
        else:
            cols_str = ", ".join(["business_id"] + columns)
            vals_placeholders = ", ".join(["%s"] * (len(columns) + 1))
            cur.execute(f"INSERT INTO design_settings ({cols_str}) VALUES ({vals_placeholders})", [business_id] + values)

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Business settings saved successfully"})


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
