import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template as original_render_template, redirect, session, g
from flask_wtf import CSRFProtect
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# הגנת CSRF בסיסית
csrf = CSRFProtect(app)

# קבצים
WEEKLY_SCHEDULE_FILE = "weekly_schedule.json"
OVERRIDES_FILE = "overrides.json"
BOT_KNOWLEDGE_FILE = "bot_knowledge.txt"
APPOINTMENTS_FILE = "appointments.json"
ONE_TIME_FILE = "one_time_changes.json"

# שירותים ומחירים
services_prices = {
    "Men's Haircut": 80,
    "Women's Haircut": 120,
    "Blow Dry": 70,
    "Color": 250
}

# לוגינג בסיסי
logging.basicConfig(level=logging.INFO)

# Cache בזיכרון ל־weekly_schedule ול־overrides
_cached_weekly_schedule = None
_cached_overrides = None
_cache_last_loaded = None
CACHE_EXPIRY_SECONDS = 15  # לחדש כל 15 שניות


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


def load_appointments():
    return load_json(APPOINTMENTS_FILE)


def save_appointments(data):
    save_json(APPOINTMENTS_FILE, data)


def load_one_time_changes():
    return load_json(ONE_TIME_FILE)


def save_one_time_changes(data):
    save_json(ONE_TIME_FILE, data)


def get_weekly_schedule_cached():
    """Cache קריאה ל- weekly_schedule."""
    global _cached_weekly_schedule, _cache_last_loaded
    now = datetime.now()
    if not _cached_weekly_schedule or not _cache_last_loaded or (now - _cache_last_loaded).seconds > CACHE_EXPIRY_SECONDS:
        _cached_weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
        _cache_last_loaded = now
    return _cached_weekly_schedule


def get_overrides_cached():
    global _cached_overrides, _cache_last_loaded
    now = datetime.now()
    if not _cached_overrides or not _cache_last_loaded or (now - _cache_last_loaded).seconds > CACHE_EXPIRY_SECONDS:
        _cached_overrides = load_json(OVERRIDES_FILE)
        _cache_last_loaded = now
    return _cached_overrides


def clear_cache():
    global _cached_weekly_schedule, _cached_overrides, _cache_last_loaded
    _cached_weekly_schedule = None
    _cached_overrides = None
    _cache_last_loaded = None


# מיפוי אחיד: 0=ראשון ... 6=שבת (הכי טבעי בישראל)
HEB_DAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]

# --- יצירת רשימת שעות שבועית עם שינויים ---

def generate_week_slots():
    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    overrides = load_json(OVERRIDES_FILE)
    today = datetime.today()
    week_slots = {}

    for i in range(7):
        current_date = today + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")

        # weekday() בפייתון מחזיר שני=0...ראשון=6, נעביר ל-0=ראשון
        py_weekday = current_date.weekday()  # שני=0 ... ראשון=6
        day_index = (py_weekday + 1) % 7
        day_name = HEB_DAYS[day_index]

        # נשתמש ב-day_index כמפתח לשגרה
        day_key = str(day_index)
        scheduled_times = weekly_schedule.get(day_key, [])

        # שינויים חד-פעמיים (overrides)
        override = overrides.get(date_str, {"add": [], "remove": []})
        add_times = override.get("add", [])
        remove_times = override.get("remove", [])

        # אם כבו את כל היום בשינויים (remove == ["__all__"]) - כל השעות לא זמינות
        if remove_times == ["__all__"]:
            final_times_list = []
        else:
            # שעות לפי שגרה + תוספות פחות מחיקות
            final_times_set = set(scheduled_times) | set(add_times)
            final_times_set -= set(remove_times)
            final_times_list = sorted(final_times_set)

        # בונים רשימת שעות עם מצב זמין/כבוי
        times = []
        for t in final_times_list:
            # שעה נחשבת כבויה אם מופיעה ב-remove_times או אם כל היום כבוי
            available = not (remove_times == ["__all__"] or t in remove_times)
            times.append({"time": t, "available": available})

        week_slots[date_str] = {
            "day_name": day_name,
            "times": times,
        }

    return week_slots


def is_slot_available(date, time):
    week_slots = generate_week_slots()
    day_info = week_slots.get(date)
    if not day_info:
        return False
    for t in day_info["times"]:
        if t["time"] == time and t.get("available", True):
            return True
    return False


# --- לפני כל בקשה - העברת session ל-g ---

@app.before_request
def before_request():
    g.username = session.get('username')
    g.is_admin = session.get('is_admin')

    # Timeout ל-session אחרי 30 דקות ללא פעילות
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=30)


# --- החלפת render_template להוספת session ל-context ---

def render_template(template_name_or_list, **context):
    context['session'] = {
        'username': g.get('username'),
        'is_admin': g.get('is_admin')
    }
    return original_render_template(template_name_or_list, **context)


# --- ניהול התחברות ---

@app.route("/login", methods=['GET', 'POST'])
def login():
    error = None
    admin_user = os.environ.get('ADMIN_USERNAME')
    admin_password = os.environ.get('ADMIN_PASSWORD') or "1234"

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form.get('password', '')

        if not username:
            error = "יש להזין שם משתמש"
            return render_template('login.html', error=error, admin_user=admin_user)

        if username == admin_user:
            if password == admin_password:
                session['username'] = username
                session['is_admin'] = True
                logging.info(f"Admin {username} logged in.")
                return redirect('/main_admin')
            else:
                error = "סיסמה שגויה"
                logging.warning(f"Failed login attempt for admin user {username}.")
                return render_template('login.html', error=error, admin_user=admin_user)

        # משתמש רגיל - אין צורך בסיסמה
        session['username'] = username
        session['is_admin'] = False
        logging.info(f"User {username} logged in.")
        return redirect('/')

    return render_template('login.html', error=error, admin_user=admin_user)


@app.route("/logout")
def logout():
    logging.info(f"User {session.get('username')} logged out.")
    session.clear()
    return redirect("/")


# --- דף ניהול ראשי ---

@app.route("/main_admin")
def main_admin():
    if not session.get("is_admin"):
        return redirect("/login")
    return render_template("main_admin.html")


# --- דפי ניהול שגרה שבועית ---

@app.route("/admin_routine")
def admin_routine():
    if not session.get("is_admin"):
        return redirect("/login")

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    return render_template("admin_routine.html", weekly_schedule=weekly_schedule)


@app.route("/weekly_schedule", methods=["POST"])
def update_weekly_schedule():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    action = data.get("action")
    day_key = data.get("day_key")
    time = data.get("time")
    new_time = data.get("new_time")

    if day_key not in [str(i) for i in range(7)]:
        return jsonify({"error": "Invalid day key"}), 400

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    day_times = weekly_schedule.get(day_key, [])

    # ניהול יום כבוי
    if action == "enable_day":
        # השבתת יום ריק = הפעלת יום, נשאיר את המצב הקיים או נוסיף רשימת שעות ריקה
        if day_key not in weekly_schedule:
            weekly_schedule[day_key] = []
        save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
        clear_cache()
        return jsonify({"success": True})

    if action == "disable_day":
        weekly_schedule[day_key] = []
        save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
        clear_cache()
        return jsonify({"success": True})

    # פעולות על שעות
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

    save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
    clear_cache()
    return jsonify({"message": "Weekly schedule updated", "weekly_schedule": weekly_schedule})


# --- ניהול שינויים חד-פעמיים (overrides) ---

@app.route("/admin_overrides")
def admin_overrides():
    if not session.get("is_admin"):
        return redirect("/login")

    overrides = load_json(OVERRIDES_FILE)
    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)

    today = datetime.today()
    week_dates = []
    date_map = {}

    for i in range(7):
        date = today + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        week_dates.append(date_str)
        py_weekday = date.weekday()
        day_index = (py_weekday + 1) % 7
        date_map[date_str] = day_index

    return render_template("admin_overrides.html",
                           overrides=overrides,
                           base_schedule=weekly_schedule,
                           week_dates=week_dates,
                           date_map=date_map)


@app.route("/overrides", methods=["POST"])
def update_overrides():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    action = data.get("action")
    date = data.get("date")
    time = data.get("time")

    overrides = load_json(OVERRIDES_FILE)
    day_override = overrides.get(date, {"add": [], "remove": []})

    if action == "add":
        if time and time not in day_override["add"]:
            day_override["add"].append(time)
            if time in day_override["remove"]:
                day_override["remove"].remove(time)
    elif action == "remove":
        if time and time not in day_override["remove"]:
            day_override["remove"].append(time)
            if time in day_override["add"]:
                day_override["add"].remove(time)
    elif action == "clear":
        overrides.pop(date, None)
        save_json(OVERRIDES_FILE, overrides)
        clear_cache()
        return jsonify({"message": f"Overrides cleared for {date}"})
    else:
        return jsonify({"error": "Invalid action"}), 400

    overrides[date] = day_override
    save_json(OVERRIDES_FILE, overrides)
    clear_cache()
    return jsonify({"message": "Overrides updated", "overrides": overrides})


@app.route("/overrides_toggle_day", methods=["POST"])
def toggle_override_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    date = data.get("date")
    enabled = data.get("enabled")

    overrides = load_json(OVERRIDES_FILE)

    if not enabled:
        overrides[date] = {"add": [], "remove": ["__all__"]}
    else:
        if date in overrides and overrides[date].get("remove") == ["__all__"]:
            overrides.pop(date)

    save_json(OVERRIDES_FILE, overrides)
    clear_cache()
    return jsonify({"message": "Day override toggled", "overrides": overrides})


# --- ניהול שינויים חד-פעמיים ב-one_time_changes (אם רוצים לנהל בנפרד מה-overrides) ---

@app.route('/admin/one-time/toggle_day', methods=['POST'])
def toggle_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

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
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    if date in one_time:
        one_time[date] = [slot for slot in one_time[date] if slot['time'] != time]
        save_one_time_changes(one_time)
    return jsonify({'message': 'Slot deleted'})


@app.route('/admin/one-time/edit', methods=['POST'])
def edit_slot():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

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
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

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
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    one_time.setdefault(date, []).append({'time': time, 'available': True})
    save_one_time_changes(one_time)
    return jsonify({'message': 'Slot added'})


# --- ניהול טקסט ידע של הבוט ---

@app.route("/bot_knowledge", methods=["GET", "POST"])
def bot_knowledge():
    if not session.get("is_admin"):
        return redirect("/login")

    if request.method == "POST":
        content = request.form.get("content", "")
        save_text(BOT_KNOWLEDGE_FILE, content)
        return redirect("/main_admin")

    content = load_text(BOT_KNOWLEDGE_FILE)
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

    if not is_slot_available(date, time):
        return jsonify({"error": "This time slot is not available"}), 400

    appointments = load_appointments()
    date_appointments = appointments.get(date, [])

    for appt in date_appointments:
        if appt["time"] == time:
            return jsonify({"error": "This time slot is already booked"}), 400

    appointment = {
        "name": name,
        "phone": phone,
        "time": time,
        "service": service,
        "price": services_prices[service]
    }
    date_appointments.append(appointment)
    appointments[date] = date_appointments
    save_appointments(appointments)

    try:
        send_email(name, phone, date, time, service, services_prices[service])
    except Exception as e:
        logging.error(f"Error sending email: {e}")

    return jsonify({"message": f"Appointment booked for {date} at {time} for {service}."})


# --- שליחת אימייל ---

def send_email(name, phone, date, time, service, price):
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
    msg['From'] = os.environ.get("EMAIL_USER", "nextwaveaiandweb@gmail.com")  # מומלץ להשתמש במשתני סביבה
    msg['To'] = os.environ.get("EMAIL_USER", "nextwaveaiandweb@gmail.com")

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        EMAIL_USER = os.environ.get("EMAIL_USER")
        EMAIL_PASS = os.environ.get("EMAIL_PASS")
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        logging.info("Email sent successfully")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


# --- דף הצגת תורים (מנהל בלבד) ---

@app.route("/availability")
def availability():
    week_slots = generate_week_slots()
    return jsonify(week_slots)


# --- דף הבית ---

@app.route("/")
def index():
    week_slots = generate_week_slots()
    return render_template("index.html", week_slots=week_slots, services=services_prices)


# --- API - שאלות לבוט ---

@app.route("/ask", methods=["POST"])
def ask_bot():
    data = request.get_json()
    question = data.get("message", "").strip()

    knowledge_text = load_text(BOT_KNOWLEDGE_FILE)

    if not question:
        answer = "אנא כתוב שאלה."
    elif "שעות" in question or "תורים" in question:
        answer = "השעות הזמינות הן לפי השגרה השבועית, אפשר לראות בדף ההזמנות."
    elif "מחיר" in question:
        answer = "המחירים שונים לפי השירות, למשל תספורת גברים 80 ש\"ח."
    else:
        answer = "מצטער, לא הבנתי את השאלה. נסה לשאול משהו אחר."

    return jsonify({"answer": answer})


# --- הפעלת השרת ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)
