import os
import requests
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template as original_render_template, redirect, session, g
import smtplib
from email.message import EmailMessage
import re
import shutil
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# --- קבצים ---
WEEKLY_SCHEDULE_FILE = "weekly_schedule.json"
OVERRIDES_FILE = "overrides.json"
BOT_KNOWLEDGE_FILE = "bot_knowledge.txt"
APPOINTMENTS_FILE = "appointments.json"
ONE_TIME_FILE = "one_time_changes.json"  

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

def load_appointments():
    return load_json(APPOINTMENTS_FILE)

def save_appointments(data):
    save_json(APPOINTMENTS_FILE, data)

def load_one_time_changes():
    return load_json(ONE_TIME_FILE)

def save_one_time_changes(data):
    save_json(ONE_TIME_FILE, data)

# --- נתיב קבצים של עסקים ---

DATA_ROOT = "data"
BUSINESSES_ROOT = os.path.join(DATA_ROOT, "businesses")
REGISTRY_FILE = os.path.join(BUSINESSES_ROOT, "businesses.json")

def ensure_dirs():
    os.makedirs(BUSINESSES_ROOT, exist_ok=True)
    if not os.path.exists(REGISTRY_FILE):
        save_json(REGISTRY_FILE, {"businesses": []})

def load_businesses():
    ensure_dirs()
    data = load_json(REGISTRY_FILE)
    return data.get("businesses", [])

def save_businesses(businesses_list):
    ensure_dirs()
    save_json(REGISTRY_FILE, {"businesses": businesses_list})

def valid_code(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{3,32}", code or ""))

def create_business_files(business_code: str):
    """יוצר תיקיית עסק עם 4 קבצי ברירת מחדל"""
    path = os.path.join(BUSINESSES_ROOT, business_code)
    os.makedirs(path, exist_ok=True)

    defaults = {
        "appointments.json": {},               
        "overrides.json": {},                   
        "weekly_schedule.json": {               
            "0": [], "1": [], "2": [], "3": [], "4": [], "5": [], "6": []
        },
        "bot_knowledge.json": {"knowledge": ""}  # תוכן ידע של הבוט
    }

    for filename, content in defaults.items():
        with open(os.path.join(path, filename), "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

# --- יצירת קבצים לכל עסק ---

def create_business_files(business_name):
    base_path = "businesses"  # התיקייה הראשית של כל העסקים
    business_path = os.path.join(base_path, business_name)
    os.makedirs(business_path, exist_ok=True)

    # רשימת הקבצים שצריך להעתיק
    files = [
        "appointments.json",
        "overrides.json",
        "weekly_schedule.json",
        "bot_knowledge.json"
    ]

    for file_name in files:
        source_path = file_name  # קובץ קיים בשורש
        dest_path = os.path.join(business_path, file_name)

        if os.path.exists(source_path):
            shutil.copy2(source_path, dest_path)
        else:
            # אם הקובץ לא קיים בשורש, ניצור קובץ ריק
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=4)

    print(f"נוצרו קבצים עבור העסק '{business_name}' בתוך '{business_path}' עם תוכן התחלתי זהה לקיימים")


# --- שעות תפוסות ושבועי ---

def get_booked_times(appointments):
    booked = {}
    for date, apps_list in appointments.items():
        times = []
        for app in apps_list:
            time = app.get('time')
            if time:
                times.append(time)
        booked[date] = times
    return booked

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

def generate_week_slots(with_sources=False):
    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    overrides = load_json(OVERRIDES_FILE)
    appointments = load_appointments()
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
                source = get_source(t, scheduled, added, removed, edits, disabled_day, booked_times)
                final_times.append({"time": t, "available": available, "source": source})
            else:
                if available:
                    final_times.append({"time": t, "available": True})

        week_slots[date_str] = {"day_name": day_name, "times": final_times}

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
        businesses = load_businesses()
        for b in businesses:
            if b['username'] == username and check_password_hash(b['password_hash'], password):
                session['username'] = username
                session['is_host'] = False
                session['is_admin'] = True
                session['business_name'] = b['business_name']
                return redirect('/main_admin')

        error = "שם משתמש או סיסמה שגויים"

    return render_template('login.html', error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

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

    ensure_dirs()

    business_code = request.form.get('business_code', '').strip()
    business_name = request.form.get('business_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()

    # ולידציות בסיסיות
    if not all([business_code, business_name, username, password, phone, email]):
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error="יש למלא את כל השדות")

    if not valid_code(business_code):
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error="קוד עסק חייב להיות 3–32 תווים: A-Z,a-z,0-9,_,-")

    businesses = load_businesses()

    # מניעת כפילויות
    if any(b.get("business_code") == business_code for b in businesses):
        return render_template('host_command.html',
                               businesses=businesses,
                               error="קוד העסק כבר קיים")
    if any(b.get("username") == username for b in businesses):
        return render_template('host_command.html',
                               businesses=businesses,
                               error="שם המשתמש כבר בשימוש")

    # יצירת קבצים לתיקיית העסק
    try:
        create_business_files(business_code)
    except Exception as e:
        return render_template('host_command.html',
                               businesses=businesses,
                               error=f"שגיאה ביצירת קבצי העסק: {e}")

    # הוספה לרשומת העסקים (סיסמה בהאש)
    businesses.append({
        "business_code": business_code,
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
def delete_business():
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

    # מחיקת תיקיית העסק (לפי business_code)
    try:
        bcode = entry.get("business_code")
        bpath = os.path.join(BUSINESSES_ROOT, bcode)
        if os.path.isdir(bpath):
            shutil.rmtree(bpath)
    except Exception as e:
        # אם המחיקה נכשלה, נציג אזהרה אבל נשאיר את המחיקה מהרישום
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

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)

    return render_template("admin_routine.html", weekly_schedule=weekly_schedule)

                          
@app.route("/admin_overrides")
def admin_overrides():
    if not session.get("is_admin"):
        return redirect("/login")

    weekly_schedule = load_json(WEEKLY_SCHEDULE_FILE)
    overrides = load_json(OVERRIDES_FILE)

    today = datetime.today()
    week_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    hebrew_day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    date_map = {}
    for d_str in week_dates:
        d = datetime.strptime(d_str, "%Y-%m-%d")
        day_name = hebrew_day_names[d.weekday()]
        date_map[d_str] = f"{d.strftime('%-d.%m')} ({day_name})"

    week_slots = generate_week_slots(with_sources=True)

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
    appointments = load_appointments()
    return render_template("admin_appointments.html", appointments=appointments)

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

    if action == "enable_day":
        if day_key not in weekly_schedule:
            weekly_schedule[day_key] = []
        save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
        return jsonify({"success": True})

    if action == "disable_day":
        weekly_schedule[day_key] = []
        save_json(WEEKLY_SCHEDULE_FILE, weekly_schedule)
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
    new_time = data.get("new_time")

    overrides = load_json(OVERRIDES_FILE)

    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    if action == "remove_many":
        times = data.get("times", [])
        for t in times:
            if t not in overrides[date]["remove"]:
                overrides[date]["remove"].append(t)
            if t in overrides[date]["add"]:
                overrides[date]["add"].remove(t)
        save_json(OVERRIDES_FILE, overrides)
        return jsonify({"message": "Multiple times removed", "overrides": overrides})

    elif action == "add" and time:
        if time not in overrides[date]["add"]:
            overrides[date]["add"].append(time)
        if time in overrides[date]["remove"]:
            overrides[date]["remove"].remove(time)
        save_json(OVERRIDES_FILE, overrides)
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
        save_json(OVERRIDES_FILE, overrides)
        return jsonify({"message": "Time removed", "overrides": overrides})

    elif action == "edit" and time and new_time:
        if time == new_time:
            return jsonify({"message": "No changes made"})

        if "edit" not in overrides[date]:
            overrides[date]["edit"] = []

        overrides[date]["edit"] = [
            item for item in overrides[date]["edit"] if item.get("from") != time
        ]

        overrides[date]["edit"].append({
            "from": time,
            "to": new_time
        })

        if "remove" not in overrides[date]:
            overrides[date]["remove"] = []
        if time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(time)

        if "add" not in overrides[date]:
            overrides[date]["add"] = []
        if new_time not in overrides[date]["add"]:
            overrides[date]["add"].append(new_time)

        save_json(OVERRIDES_FILE, overrides)
        return jsonify({"message": "Time edited", "overrides": overrides})

    elif action == "clear" and date:
        if date in overrides:
            overrides.pop(date)
        save_json(OVERRIDES_FILE, overrides)
        return jsonify({"message": "Day overrides cleared", "overrides": overrides})

    elif action == "disable_day" and date:
        overrides[date] = {"add": [], "remove": ["__all__"]}
        save_json(OVERRIDES_FILE, overrides)
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

        save_json(OVERRIDES_FILE, overrides)
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

    overrides = load_json(OVERRIDES_FILE)

    if not enabled:
        overrides[date] = {"add": [], "remove": ["__all__"]}
    else:
        if date in overrides and overrides[date].get("remove") == ["__all__"]:
            overrides.pop(date)

    save_json(OVERRIDES_FILE, overrides)
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

    appointments = load_appointments()

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

    overrides = load_json(OVERRIDES_FILE)
    if date not in overrides:
        overrides[date] = {"add": [], "remove": [], "edit": [], "booked": []}
    elif "booked" not in overrides[date]:
        overrides[date]["booked"] = []

    overrides[date]["booked"].append({
        "time": time,
        "name": name,
        "phone": phone,
        "service": service
    })
    if time not in overrides[date]["remove"]:
        overrides[date]["remove"].append(time)
    if time in overrides[date]["add"]:
        overrides[date]["add"].remove(time)

    save_json(OVERRIDES_FILE, overrides)

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
        with open(APPOINTMENTS_FILE, 'r', encoding='utf-8') as f:
            appointments = json.load(f)
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

    with open(APPOINTMENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(appointments, f, ensure_ascii=False, indent=2)


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

    if not question:
        return jsonify({"answer": "אנא כתוב שאלה."})

    knowledge_text = load_text(BOT_KNOWLEDGE_FILE)

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
