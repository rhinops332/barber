import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template as original_render_template, redirect, session, g
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# קבצים
WEEKLY_SCHEDULE_FILE = "weekly_schedule.json"
OVERRIDES_FILE = "overrides.json"
BOT_KNOWLEDGE_FILE = "bot_knowledge.txt"
APPOINTMENTS_FILE = "appointments.json"

# שירותים ומחירים
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

def load_one_time_changes():
    return load_json(OVERRIDES_FILE)

def save_one_time_changes(data):
    save_json(OVERRIDES_FILE, data)

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

# --- יצירת רשימת שעות שבועית עם שינויים ---

def generate_week_slots():
    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    overrides = load_one_time_changes()
    today = datetime.today()
    week_slots = {}
    heb_days = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

    for i in range(7):
        current_date = today + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        weekday = current_date.weekday()
        day_name = heb_days[weekday]
        day_key = str(weekday)
        scheduled_times = weekly_schedule.get(day_key, [])

        override = overrides.get(date_str, {"add": [], "remove": []})
        add_times = override.get("add", [])
        remove_times = override.get("remove", [])

        if remove_times == ["__all__"]:
            final_times = []
        else:
            all_final = sorted(set(scheduled_times + add_times))
            final_times = []
            for t in all_final:
                if t in remove_times:
                    final_times.append({"time": t, "available": False})
                else:
                    final_times.append({"time": t, "available": True})

        week_slots[date_str] = {
            "day_name": day_name,
            "times": final_times
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

@app.before_request
def before_request():
    g.username = session.get('username')
    g.is_admin = session.get('is_admin')

def render_template(template_name_or_list, **context):
    context['session'] = {
        'username': g.get('username'),
        'is_admin': g.get('is_admin')
    }
    return original_render_template(template_name_or_list, **context)

# --- התחברות ---

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
        elif username == admin_user:
            if password == admin_password:
                session['username'] = username
                session['is_admin'] = True
                return redirect('/admin_command')
            else:
                error = "סיסמה שגויה"
        else:
            session['username'] = username
            session['is_admin'] = False
            return redirect('/')

    return render_template('login.html', error=error, admin_user=admin_user)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- דפי HTML נפרדים ---

@app.route("/admin/weekly")
def weekly_schedule_page():
    if not session.get("is_admin"):
        return redirect("/login")

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    return render_template("schedule.html", weekly_schedule=weekly_schedule)

@app.route("/admin/one-time")
def one_time_changes_page():
    if not session.get("is_admin"):
        return redirect("/login")

    overrides = load_one_time_changes()
    return render_template("one_time_changes.html", overrides=overrides)

# --- דף ראשי של ניהול ---

@app.route("/admin_command")
def admin_command():
    if not session.get("is_admin"):
        return redirect("/login")

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    overrides = load_json(OVERRIDES_FILE)
    week_slots = generate_week_slots()
    bot_knowledge = load_text(BOT_KNOWLEDGE_FILE)
    appointments = load_appointments()

    default_times = []
    current_time = datetime.strptime("08:00", "%H:%M")
    end_time = datetime.strptime("20:00", "%H:%M")
    while current_time <= end_time:
        default_times.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=30)

    return render_template("admin_command.html",
        weekly_schedule=weekly_schedule,
        overrides=overrides,
        week_slots=week_slots,
        bot_knowledge=bot_knowledge,
        appointments=appointments,
        default_times=default_times)
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

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)

    if day_key not in [str(i) for i in range(7)]:
        return jsonify({"error": "Invalid day key"}), 400

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

    save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
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

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    weekly_schedule[day_key] = [] if not enabled else weekly_schedule.get(day_key, [])
    save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)

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

    overrides = load_one_time_changes()
    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    day_override = overrides[date]

    if action == "add":
        if time and time not in day_override["add"]:
            day_override["add"].append(time)
        if time and time in day_override["remove"]:
            day_override["remove"].remove(time)
    elif action == "remove":
        if time and time not in day_override["remove"]:
            day_override["remove"].append(time)
        if time and time in day_override["add"]:
            day_override["add"].remove(time)
    elif action == "clear":
        overrides.pop(date, None)
        save_one_time_changes(overrides)
        return jsonify({"message": f"Overrides cleared for {date}"})
    else:
        return jsonify({"error": "Invalid action"}), 400

    overrides[date] = day_override
    save_one_time_changes(overrides)
    return jsonify({"message": "Overrides updated", "overrides": overrides})

@app.route('/admin/one-time/toggle_day', methods=['POST'])
def toggle_day():
    data = request.json
    date = data.get('date')
    if not date:
        return jsonify({'error': 'Missing date'}), 400

    overrides = load_one_time_changes()
    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    remove_list = overrides[date].get("remove", [])
    # אם כל היום כבוי (__all__ בתוך remove) אז נדליק, אחרת נכבה
    if "__all__" in remove_list:
        # הדלק יום - הסר __all__ מהרשימה
        remove_list = [t for t in remove_list if t != "__all__"]
    else:
        # כבה יום - הוסף __all__ לרשימה והסר את כל ה-add
        remove_list = ["__all__"]
        overrides[date]["add"] = []

    overrides[date]["remove"] = remove_list

    save_one_time_changes(overrides)
    return jsonify({"message": "Day toggled successfully", "overrides": overrides})

@app.route('/admin/one-time/delete', methods=['POST'])
def delete_slot():
    data = request.json
    date = data.get('date')
    time = data.get('time')
    if not date or not time:
        return jsonify({'error': 'Missing date or time'}), 400

    overrides = load_one_time_changes()
    if date in overrides:
        if time in overrides[date].get("add", []):
            overrides[date]["add"].remove(time)
        if time in overrides[date].get("remove", []):
            overrides[date]["remove"].remove(time)
        # אם אחרי המחיקה הרשימות ריקות אפשר לשמור את זה כך
        save_one_time_changes(overrides)

    return jsonify({'message': 'Slot deleted'})

@app.route('/admin/one-time/edit', methods=['POST'])
def edit_slot():
    data = request.json
    date = data.get('date')
    old_time = data.get('old_time')
    new_time = data.get('new_time')
    if not date or not old_time or not new_time:
        return jsonify({'error': 'Missing parameters'}), 400

    overrides = load_one_time_changes()
    if date not in overrides:
        return jsonify({'error': 'Date not found'}), 404

    # הסר את old_time מכל מקום
    if old_time in overrides[date].get("add", []):
        overrides[date]["add"].remove(old_time)
    if old_time in overrides[date].get("remove", []):
        overrides[date]["remove"].remove(old_time)

    # הוסף את new_time לפי הגדרה: אם old_time היה ב-add או remove - לשמור על סטטוס?  
    # (בפשטות נניח שזמין כברירת מחדל)
    overrides[date]["add"].append(new_time)
    overrides[date]["add"] = sorted(set(overrides[date]["add"]))

    save_one_time_changes(overrides)
    return jsonify({'message': 'Slot edited'})

@app.route('/admin/one-time/toggle_slot', methods=['POST'])
def toggle_slot():
    data = request.json
    date = data.get('date')
    time = data.get('time')
    if not date or not time:
        return jsonify({'error': 'Missing date or time'}), 400

    overrides = load_one_time_changes()
    if date not in overrides:
        return jsonify({'error': 'Date not found'}), 404

    if time in overrides[date].get("add", []):
        overrides[date]["add"].remove(time)
        overrides[date]["remove"].append(time)
    elif time in overrides[date].get("remove", []):
        overrides[date]["remove"].remove(time)
        overrides[date]["add"].append(time)
    else:
        # אם לא קיים בשום רשימה, נניח שמוסיפים ל-add
        overrides[date]["add"].append(time)

    overrides[date]["add"] = sorted(set(overrides[date]["add"]))
    overrides[date]["remove"] = sorted(set(overrides[date]["remove"]))

    save_one_time_changes(overrides)
    return jsonify({'message': 'Slot toggled'})

@app.route('/admin/one-time/add', methods=['POST'])
def add_slot():
    data = request.json
    date = data.get('date')
    time = data.get('time')
    if not date or not time:
        return jsonify({'error': 'Missing date or time'}), 400

    overrides = load_one_time_changes()
    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    if time not in overrides[date]["add"]:
        overrides[date]["add"].append(time)
        # אם הזמן קיים ב-remove - להסיר אותו משם
        if time in overrides[date]["remove"]:
            overrides[date]["remove"].remove(time)

    overrides[date]["add"] = sorted(set(overrides[date]["add"]))
    save_one_time_changes(overrides)
    return jsonify({'message': 'Slot added'})

# --- ניהול טקסט ידע של הבוט ---

@app.route("/bot_knowledge", methods=["GET", "POST"])
def bot_knowledge():
    if not session.get("is_admin"):
        return redirect("/login")

    if request.method == "POST":
        content = request.form.get("content", "")
        save_text(BOT_KNOWLEDGE_FILE, content)
        return redirect("/admin_command")

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

    # בדיקה אם השעה תפוסה
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
        print("Error sending email:", e)

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
    msg['From'] = 'nextwaveaiandweb@gmail.com'  # שנה למייל שלך
    msg['To'] = 'nextwaveaiandweb@gmail.com'    # שנה למייל שלך

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        EMAIL_USER = os.environ.get("EMAIL_USER")
        EMAIL_PASS = os.environ.get("EMAIL_PASS")

        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully")
    except Exception as e:
        print("Failed to send email:", e)

# --- דף הצגת תורים (מנהל בלבד) ---

@app.route("/availability")
def availability():
    week_slots = generate_week_slots()
    return jsonify(week_slots)  # מחזיר מפתחות כמו "2025-08-01"

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
    app.run(host="0.0.0.0", port=port)
