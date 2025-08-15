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

def get_business_files_path(business_name):
    return os.path.join("businesses", business_name)

def ensure_business_files(business_name):
    base_path = get_business_files_path(business_name)
    os.makedirs(base_path, exist_ok=True)

    files_defaults = {
        "weekly_schedule.json": {
            "0": [], "1": [], "2": [], "3": [], "4": [], "5": [], "6": []
        },
        "overrides.json": {},
        "appointments.json": {},
        "one_time_changes.json": {},
        "bot_knowledge.txt": ""
    }

    for fname, default_content in files_defaults.items():
        fpath = os.path.join(base_path, fname)
        if not os.path.exists(fpath):
            if fname.endswith(".txt"):
                save_text(fpath, default_content)
            else:
                save_json(fpath, default_content)

def load_weekly_schedule(business_name):
    ensure_business_files(business_name)
    path = os.path.join(get_business_files_path(business_name), "weekly_schedule.json")
    return load_json(path)

def save_weekly_schedule(business_name, data):
    path = os.path.join(get_business_files_path(business_name), "weekly_schedule.json")
    save_json(path, data)

def load_overrides(business_name):
    ensure_business_files(business_name)
    path = os.path.join(get_business_files_path(business_name), "overrides.json")
    return load_json(path)

def save_overrides(business_name, data):
    path = os.path.join(get_business_files_path(business_name), "overrides.json")
    save_json(path, data)

def load_appointments(business_name):
    ensure_business_files(business_name)
    path = os.path.join(get_business_files_path(business_name), "appointments.json")
    return load_json(path)

def save_appointments(business_name, data):
    path = os.path.join(get_business_files_path(business_name), "appointments.json")
    save_json(path, data)

def load_one_time_changes(business_name):
    ensure_business_files(business_name)
    path = os.path.join(get_business_files_path(business_name), "one_time_changes.json")
    return load_json(path)

def save_one_time_changes(business_name, data):
    path = os.path.join(get_business_files_path(business_name), "one_time_changes.json")
    save_json(path, data)

def load_bot_knowledge(business_name):
    ensure_business_files(business_name)
    path = os.path.join(get_business_files_path(business_name), "bot_knowledge.txt")
    return load_text(path)

def save_bot_knowledge(business_name, content):
    path = os.path.join(get_business_files_path(business_name), "bot_knowledge.txt")
    save_text(path, content)

# --- פונקציות עסקיות בסיסיות ---

def create_business_files(business_name):
    ensure_business_files(business_name)
    print(f"קבצים נוצרו עבור העסק '{business_name}'")

def get_business_details(username, password):
    businesses = load_businesses()
    for b in businesses:
        if b['username'] == username and check_password_hash(b['password_hash'], password):
            return b['business_name'], b['email'], b['phone']
    return None, None, None

# --- ניהול שבועי ושינויים ---

def get_booked_times(appointments):
    booked = {}
    for date, apps_list in appointments.items():
        times = [app['time'] for app in apps_list if 'time' in app]
        booked[date] = times
    return booked

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
        business_name, email, phone = get_business_details(username, password)
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

    business_name = session.get("business_name")
    if not business_name:
        return "Missing business_name", 400
    email = session.get('email')
    phone = session.get('phone')

    return f"שלום {business_name}, המייל שלך: {email}, הטלפון: {phone}"

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
        create_business_files(business_name)
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
        
    business_name = session.get("business_name")
    if not business_name:
        return "Missing business_name", 400

    weekly_schedule = load_weekly_schedule(business_name)

    return render_template("admin_routine.html", weekly_schedule=weekly_schedule)

                          
@app.route("/admin_overrides")
def admin_overrides():
    if not session.get("is_admin"):
        return redirect("/login")

    business_name = session.get("business_name")
    if not business_name:
        return "Missing business_name", 400

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

    week_slots = generate_week_slots(business_name, with_sources=True)  # משתמשים במשתנה מה־query

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

    business_name = session.get("business_name")
    if not business_name:
        return "Missing business_name", 400

    appointments = load_appointments(business_name)
    return render_template("admin_appointments.html", appointments=appointments)

# --- ניהול שגרה שבועית ---

@app.route("/weekly_schedule", methods=["POST"])
def update_weekly_schedule():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    business_name = data.get("business")  # <- קבלת העסק מה־POST
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    action = data.get("action")
    day_key = data.get("day_key")
    time = data.get("time")
    new_time = data.get("new_time")

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
    business_name = data.get("business")  # <- קבלת העסק מה־POST
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    day_key = data.get("day_key")
    enabled = data.get("enabled")

    if day_key not in [str(i) for i in range(7)]:
        return jsonify({"error": "Invalid day key"}), 400

    weekly_schedule = load_weekly_schedule(business_name)
    weekly_schedule[day_key] = [] if not enabled else weekly_schedule.get(day_key, [])
    save_weekly_schedule(business_name, weekly_schedule)

    return jsonify({"message": "Day updated", "weekly_schedule": weekly_schedule})


# --- ניהול שינויים חד פעמיים (overrides) ---

# ---------------- Overrides ----------------

@app.route("/overrides", methods=["POST"])
def update_overrides():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    business_name = data.get("business")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    action = data.get("action")
    date = data.get("date")
    time = data.get("time")
    new_time = data.get("new_time")

    overrides = load_overrides(business_name)
    if date not in overrides:
        overrides[date] = {"add": [], "remove": []}

    if action == "add" and time:
        if time not in overrides[date]["add"]:
            overrides[date]["add"].append(time)
        if time in overrides[date]["remove"]:
            overrides[date]["remove"].remove(time)

    elif action == "remove" and time:
        overrides[date]["remove"].append(time)
        if time in overrides[date]["add"]:
            overrides[date]["add"].remove(time)
        if "edit" in overrides[date]:
            overrides[date]["edit"] = [e for e in overrides[date]["edit"]
                                       if e.get("from") != time and e.get("to") != time]
            if not overrides[date]["edit"]:
                overrides[date].pop("edit")

    elif action == "edit" and time and new_time:
        if "edit" not in overrides[date]:
            overrides[date]["edit"] = []
        overrides[date]["edit"] = [e for e in overrides[date]["edit"] if e.get("from") != time]
        overrides[date]["edit"].append({"from": time, "to": new_time})

        if "remove" not in overrides[date]:
            overrides[date]["remove"] = []
        if time not in overrides[date]["remove"]:
            overrides[date]["remove"].append(time)

        if "add" not in overrides[date]:
            overrides[date]["add"] = []
        if new_time not in overrides[date]["add"]:
            overrides[date]["add"].append(new_time)

    elif action == "disable_day" and date:
        overrides[date] = {"add": [], "remove": ["__all__"]}

    elif action == "clear" and date:
        overrides.pop(date, None)

    save_overrides(business_name, overrides)
    return jsonify({"message": "Overrides updated", "overrides": overrides})

@app.route("/overrides_toggle_day", methods=["POST"])
def toggle_override_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    business_name = data.get("business")
    date = data.get("date")
    enabled = data.get("enabled")

    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    overrides = load_overrides(business_name)

    if not enabled:
        overrides[date] = {"add": [], "remove": ["__all__"]}
    else:
        if date in overrides and overrides[date].get("remove") == ["__all__"]:
            overrides.pop(date)

    save_overrides(business_name, overrides)
    return jsonify({"message": "Day override toggled", "overrides": overrides})

# ---------------- One-time changes ----------------

@app.route('/admin/one-time/add', methods=['POST'])
def one_time_add_slot():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    business_name = data.get("business")
    date, time = data.get('date'), data.get('time')

    if not business_name:
        return jsonify({'error': 'Missing business_name'}), 400

    one_time = load_one_time_changes(business_name)
    one_time.setdefault(date, []).append({'time': time, 'available': True})
    save_one_time_changes(business_name, one_time)
    return jsonify({'message': 'Slot added'})

@app.route('/admin/one-time/delete', methods=['POST'])
def one_time_delete_slot():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    business_name = data.get("business")
    date, time = data.get('date'), data.get('time')

    if not business_name:
        return jsonify({'error': 'Missing business_name'}), 400

    one_time = load_one_time_changes(business_name)
    if date in one_time:
        one_time[date] = [slot for slot in one_time[date] if slot['time'] != time]
        save_one_time_changes(business_name, one_time)
    return jsonify({'message': 'Slot deleted'})

@app.route('/admin/one-time/edit', methods=['POST'])
def one_time_edit_slot():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    business_name = data.get("business")
    date, old_time, new_time = data.get('date'), data.get('old_time'), data.get('new_time')

    if not business_name:
        return jsonify({'error': 'Missing business_name'}), 400

    one_time = load_one_time_changes(business_name)
    for slot in one_time.get(date, []):
        if slot['time'] == old_time:
            slot['time'] = new_time
            break
    save_one_time_changes(business_name, one_time)
    return jsonify({'message': 'Slot edited'})

@app.route('/admin/one-time/toggle_slot', methods=['POST'])
def one_time_toggle_slot():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    business_name = data.get("business")
    date, time = data.get('date'), data.get('time')

    if not business_name:
        return jsonify({'error': 'Missing business_name'}), 400

    one_time = load_one_time_changes(business_name)
    for slot in one_time.get(date, []):
        if slot['time'] == time:
            slot['available'] = not slot['available']
            break
    save_one_time_changes(business_name, one_time)
    return jsonify({'message': 'Slot toggled'})

@app.route('/admin/one-time/toggle_day', methods=['POST'])
def one_time_toggle_day():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    business_name = data.get("business")
    date = data.get('date')

    if not business_name:
        return jsonify({'error': 'Missing business_name'}), 400

    one_time = load_one_time_changes(business_name)
    if date not in one_time:
        return jsonify({'error': 'Date not found'}), 404

    all_disabled = all(not slot['available'] for slot in one_time[date])
    for slot in one_time[date]:
        slot['available'] = not all_disabled

    save_one_time_changes(business_name, one_time)
    return jsonify({'message': 'Day toggled successfully'})

# ---------------- Appointment details ----------------

@app.route('/appointment_details')
def appointment_details():
    date = request.args.get('date')
    time = request.args.get('time')
    business_name = request.args.get('business')

    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    appointments = load_appointments(business_name)
    if date in appointments:
        for appt in appointments[date]:
            if appt.get('time') == time:
                return render_template('appointment_details.html', appointment=appt)

    return "פרטי ההזמנה לא נמצאו", 404

# --- ניהול טקסט ידע של הבוט ---

# --- Bot Knowledge ---

@app.route("/bot_knowledge", methods=["GET", "POST"])
def bot_knowledge():
    if not session.get("is_admin"):
        return redirect("/login")

    business_name = session.get("business_name")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    if request.method == "POST":
        content = request.form.get("content", "")
        save_business_json(business_name, "bot_knowledge.json", content)
        return redirect("/main_admin")

    content = load_business_json(business_name, "bot_knowledge.json")
    return render_template("bot_knowledge.html", content=content)

# --- Book Appointment ---

@app.route("/book", methods=["POST"])
def book_appointment():
    data = request.get_json()
    business_name = data.get("business")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    date = data.get("date", "").strip()
    time = data.get("time", "").strip()
    service = data.get("service", "").strip()

    if not all([name, phone, date, time, service]):
        return jsonify({"error": "Missing fields"}), 400
    if service not in services_prices:
        return jsonify({"error": "Unknown service"}), 400
    if not is_slot_available(business_name, date, time):
        return jsonify({"error": "This time slot is not available"}), 400

    appointments = load_appointments(business_name)
    date_appointments = appointments.get(date, [])
    if any(appt["time"] == time for appt in date_appointments):
        return jsonify({"error": "This time slot is already booked"}), 400

    appointment = {"name": name, "phone": phone, "time": time, "service": service,
                   "price": services_prices[service]}
    date_appointments.append(appointment)
    appointments[date] = date_appointments
    save_appointments(business_name, appointments)

    overrides = load_overrides(business_name)
    overrides.setdefault(date, {"add": [], "remove": [], "edit": [], "booked": []})
    overrides[date]["booked"].append({"time": time, "name": name, "phone": phone, "service": service})
    if time not in overrides[date]["remove"]:
        overrides[date]["remove"].append(time)
    if time in overrides[date]["add"]:
        overrides[date]["add"].remove(time)
    save_overrides(business_name, overrides)

    try:
        send_email(name, phone, date, time, service, services_prices[service])
    except Exception as e:
        print("Error sending email:", e)

    return jsonify({"message": f"Appointment booked for {date} at {time} for {service}."})

# --- Cancel Appointment ---

@app.route('/cancel_appointment', methods=['POST'])
def cancel_appointment():
    data = request.get_json()
    business_name = data.get("business") or session.get("business_name")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400

    date, time, name, phone = data.get('date'), data.get('time'), data.get('name'), data.get('phone')
    appointments = load_appointments(business_name)
    day_appointments = appointments.get(date, [])
    new_day_appointments = [appt for appt in day_appointments
                            if not (appt['time'] == time and appt['name'] == name and appt['phone'] == phone)]
    if len(new_day_appointments) == len(day_appointments):
        return jsonify({'error': 'Appointment not found'}), 404

    appointments[date] = new_day_appointments
    save_appointments(business_name, appointments)

    overrides = load_overrides(business_name)
    if date not in overrides:
        overrides[date] = {"add": [], "remove": [], "edit": []}

    if time in overrides[date].get("remove", []):
        overrides[date]["remove"].remove(time)
    if time not in overrides[date].get("add", []):
        overrides[date]["add"].append(time)

    save_overrides(business_name, overrides)
    return jsonify({'message': f'Appointment on {date} at {time} canceled successfully.'})

# --- Homepage & Availability ---

@app.route("/")
def index():
    business_name = session.get("business_name")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400
    week_slots = generate_week_slots(business_name)
    return render_template("index.html", week_slots=week_slots, services=services_prices)

@app.route("/availability")
def availability():
    business_name = session.get("business_name")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400
    week_slots = generate_week_slots(business_name)
    return jsonify(week_slots)

# --- Bot API ---

@app.route("/ask", methods=["POST"])
def ask_bot():
    data = request.get_json()
    question = data.get("message", "").strip()
    business_name = data.get("business") or session.get("business_name")
    if not business_name:
        return jsonify({"error": "Missing business_name"}), 400
    if not question:
        return jsonify({"answer": "אנא כתוב שאלה."})

    knowledge_text = load_bot_knowledge(business_name)
    messages = [
        {"role": "system", "content": "You are a helpful assistant for a hair salon booking system."},
        {"role": "system", "content": f"Additional info: {knowledge_text}"},
        {"role": "user", "content": question}
    ]

    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        return jsonify({"error": "Missing GitHub API token"}), 500

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"}
    payload = {"model": "openai/gpt-4.1", "messages": messages, "temperature": 0.7, "max_tokens": 200}

    try:
        response = requests.post("https://models.github.ai/inference/v1/chat/completions",
                                 headers=headers, json=payload)
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"answer": answer})
    except Exception as e:
        print("Error calling GitHub AI API:", e)
        return jsonify({"answer": "מצטער, לא הצלחתי לעבד את השאלה כרגע."})

# --- הפעלת השרת ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
