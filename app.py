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
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, UniqueConstraint, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship
from sqlalchemy.types import JSON as SAJSON
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret")

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////workspace/app.db")
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()

class Business(Base):
    __tablename__ = "businesses"
    id = Column(Integer, primary_key=True)
    business_code = Column(String(64), unique=True, nullable=False)
    business_name = Column(String(255), nullable=False)
    username = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    phone = Column(String(64), nullable=False)
    email = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedules = relationship("WeeklySchedule", cascade="all, delete-orphan", backref="business")
    overrides = relationship("Override", cascade="all, delete-orphan", backref="business")
    appointments = relationship("Appointment", cascade="all, delete-orphan", backref="business")
    knowledge = relationship("BotKnowledge", cascade="all, delete-orphan", backref="business", uselist=False)

class WeeklySchedule(Base):
    __tablename__ = "weekly_schedule"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True, nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0..6 (Mon..Sun per Python weekday)
    time = Column(String(5), nullable=False)       # HH:MM
    __table_args__ = (UniqueConstraint("business_id", "day_of_week", "time", name="uq_weekly_slot"),)

class Override(Base):
    __tablename__ = "overrides"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True, nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    add_times = Column(SAJSON, nullable=False, default=list)
    remove_times = Column(SAJSON, nullable=False, default=list)
    edit_entries = Column(SAJSON, nullable=False, default=list)     # [{from,to}]
    booked_entries = Column(SAJSON, nullable=False, default=list)   # [{time,name,phone,service}]
    one_time_changes = Column(SAJSON, nullable=False, default=list) # [{time,available}]
    __table_args__ = (UniqueConstraint("business_id", "date", name="uq_override_date"),)

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True, nullable=False)
    date = Column(String(10), nullable=False)
    time = Column(String(5), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(64), nullable=False)
    service = Column(String(255), nullable=False)
    price = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("business_id", "date", "time", name="uq_appointment_slot"),)

class BotKnowledge(Base):
    __tablename__ = "bot_knowledge"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), unique=True, nullable=False)
    content = Column(Text, nullable=False, default="")

Base.metadata.create_all(engine)

# --- קבצים ---
# Legacy files no longer used; kept for compatibility constants
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

# DB helpers

def get_db():
    return SessionLocal()


def get_business_by_name(db, business_name):
    return db.query(Business).filter(Business.business_name == business_name).first()


def get_business_by_username(db, username):
    return db.query(Business).filter(Business.username == username).first()


def load_businesses():
    # Return list[dict] for compatibility with templates and existing logic
    with get_db() as db:
        rows = db.query(Business).order_by(Business.created_at.desc()).all()
        return [
            {
                "business_code": r.business_code,
                "business_name": r.business_name,
                "username": r.username,
                "password_hash": r.password_hash,
                "phone": r.phone,
                "email": r.email,
                "created_at": r.created_at.isoformat() + "Z"
            }
            for r in rows
        ]


def save_businesses(data):
    # Not used with DB; kept for backward compatibility no-op
    return

# --- פונקציות עסקיות לכל עסק ---

def get_business_files_path(business_name):
    # Deprecated when using DB
    return os.path.join("businesses", business_name)


def ensure_business_files(business_name):
    # Deprecated when using DB
    base_path = get_business_files_path(business_name)
    os.makedirs(base_path, exist_ok=True)


def load_weekly_schedule(business_name):
    # Return dict[str(day_index)] -> list[str time]
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        result = {str(i): [] for i in range(7)}
        if not b:
            return result
        rows = (
            db.query(WeeklySchedule)
            .filter(WeeklySchedule.business_id == b.id)
            .all()
        )
        for r in rows:
            key = str(r.day_of_week)
            result.setdefault(key, []).append(r.time)
        for k in result:
            result[k] = sorted(set(result[k]))
        return result


def save_weekly_schedule(business_name, data):
    # Replace weekly schedule for business with provided data
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return
        existing = db.query(WeeklySchedule).filter(WeeklySchedule.business_id == b.id).all()
        existing_map = {(e.day_of_week, e.time): e for e in existing}
        desired = set()
        for day_key, times in (data or {}).items():
            try:
                day_idx = int(day_key)
            except Exception:
                continue
            for t in times:
                desired.add((day_idx, t))
        # Delete removed
        for (d, t), row in list(existing_map.items()):
            if (d, t) not in desired:
                db.delete(row)
        # Insert new
        for (d, t) in desired:
            if (d, t) not in existing_map:
                db.add(WeeklySchedule(business_id=b.id, day_of_week=d, time=t))
        db.commit()


def _override_row_for(db, b, date):
    row = (
        db.query(Override)
        .filter(Override.business_id == b.id, Override.date == date)
        .one_or_none()
    )
    if not row:
        row = Override(business_id=b.id, date=date, add_times=[], remove_times=[], edit_entries=[], booked_entries=[], one_time_changes=[])
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def load_overrides(business_name):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return {}
        rows = db.query(Override).filter(Override.business_id == b.id).all()
        result = {}
        for r in rows:
            result[r.date] = {
                "add": list(r.add_times or []),
                "remove": list(r.remove_times or []),
                "edit": list(r.edit_entries or []),
                "booked": list(r.booked_entries or []),
            }
        return result


def save_overrides(business_name, data):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return
        # Existing dates
        existing_rows = db.query(Override).filter(Override.business_id == b.id).all()
        existing_dates = {r.date for r in existing_rows}
        incoming_dates = set((data or {}).keys())
        # Delete removed dates
        for r in existing_rows:
            if r.date not in incoming_dates:
                db.delete(r)
        # Upsert others
        for date, payload in (data or {}).items():
            row = (
                db.query(Override)
                .filter(Override.business_id == b.id, Override.date == date)
                .one_or_none()
            )
            if not row:
                row = Override(business_id=b.id, date=date)
                db.add(row)
            row.add_times = payload.get("add", [])
            row.remove_times = payload.get("remove", [])
            row.edit_entries = payload.get("edit", [])
            # Keep booked entries if provided; else preserve
            if "booked" in payload:
                row.booked_entries = payload.get("booked", [])
        db.commit()


def load_appointments(business_name):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return {}
        rows = (
            db.query(Appointment)
            .filter(Appointment.business_id == b.id)
            .all()
        )
        result = {}
        for r in rows:
            result.setdefault(r.date, []).append({
                "name": r.name,
                "phone": r.phone,
                "time": r.time,
                "service": r.service,
                "price": r.price,
            })
        # keep times sorted within date
        for d in result:
            result[d] = sorted(result[d], key=lambda x: x.get("time", ""))
        return result


def save_appointments(business_name, data):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return
        # Replace all appointments for this business with provided data
        db.query(Appointment).filter(Appointment.business_id == b.id).delete()
        for date, appts in (data or {}).items():
            for a in appts:
                db.add(Appointment(
                    business_id=b.id,
                    date=date,
                    time=a.get("time"),
                    name=a.get("name", ""),
                    phone=a.get("phone", ""),
                    service=a.get("service", ""),
                    price=int(a.get("price", 0))
                ))
        db.commit()


def load_one_time_changes(business_name=None):
    # store under overrides.one_time_changes per date
    with get_db() as db:
        if business_name is None:
            business_name = session.get('business_name')
        b = get_business_by_name(db, business_name)
        if not b:
            return {}
        rows = db.query(Override).filter(Override.business_id == b.id).all()
        result = {}
        for r in rows:
            if r.one_time_changes:
                result[r.date] = list(r.one_time_changes)
        return result


def save_one_time_changes(business_name, data):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return
        # Upsert dates only for provided payload
        for date, changes in (data or {}).items():
            row = _override_row_for(db, b, date)
            row.one_time_changes = changes
        db.commit()


def load_bot_knowledge(business_name):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return ""
        row = db.query(BotKnowledge).filter(BotKnowledge.business_id == b.id).one_or_none()
        return row.content if row else ""


def save_bot_knowledge(business_name, content):
    with get_db() as db:
        b = get_business_by_name(db, business_name)
        if not b:
            return
        row = db.query(BotKnowledge).filter(BotKnowledge.business_id == b.id).one_or_none()
        if not row:
            row = BotKnowledge(business_id=b.id, content=content or "")
            db.add(row)
        else:
            row.content = content or ""
        db.commit()

# --- פונקציות עסקיות בסיסיות ---

def create_business_files(business_name):
    # Deprecated under DB. Kept as no-op.
    return

def get_business_details(username, password):
    with get_db() as db:
        b = get_business_by_username(db, username)
        if b and check_password_hash(b.password_hash, password):
            return b.business_name, b.email, b.phone
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

    business_code = request.form.get('business_code', '').strip()
    business_name = request.form.get('business_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()

    if not all([business_code, business_name, username, password, phone, email]):
        return render_template('host_command.html',
                               businesses=load_businesses(),
                               error="יש למלא את כל השדות")

    with get_db() as db:
        if db.query(Business).filter(Business.username == username).first():
            return render_template('host_command.html',
                                   businesses=load_businesses(),
                                   error="שם המשתמש כבר בשימוש")
        if db.query(Business).filter(Business.business_code == business_code).first():
            return render_template('host_command.html',
                                   businesses=load_businesses(),
                                   error="קוד העסק כבר בשימוש")

        db.add(Business(
            business_code=business_code,
            business_name=business_name,
            username=username,
            password_hash=generate_password_hash(password),
            phone=phone,
            email=email
        ))
        db.commit()

    businesses = load_businesses()
    return render_template('host_command.html',
                           businesses=businesses,
                           msg=f"העסק '{business_name}' נוצר בהצלחה")

@app.route('/delete_business', methods=['POST'])
def delete_business():
    if not session.get('is_host'):
        return redirect('/login')

    username = request.form.get('username', '').strip()

    with get_db() as db:
        b = db.query(Business).filter(Business.username == username).one_or_none()
        if not b:
            return render_template('host_command.html',
                                   businesses=load_businesses(),
                                   error="העסק לא נמצא")
        db.delete(b)
        db.commit()

    return render_template('host_command.html',
                           businesses=load_businesses(),
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
    business_name = session.get('business_name')
    if not business_name:
        return redirect("/login")
    appointments = load_appointments(business_name)

    # If date param is requested (used by orders page), return JSON
    if request.args.get('date'):
        return jsonify(appointments)

    if not session.get("is_admin"):
        return redirect("/login")

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

    save_one_time_changes(session.get('business_name'), one_time)
    return jsonify({'message': 'Day toggled successfully'})

@app.route('/admin/one-time/delete', methods=['POST'])
def delete_slot():
    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    if date in one_time:
        one_time[date] = [slot for slot in one_time[date] if slot['time'] != time]
        save_one_time_changes(session.get('business_name'), one_time)
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
    save_one_time_changes(session.get('business_name'), one_time)
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
    save_one_time_changes(session.get('business_name'), one_time)
    return jsonify({'message': 'Slot toggled'})

@app.route('/admin/one-time/add', methods=['POST'])
def add_slot():
    data = request.json
    date, time = data['date'], data['time']
    one_time = load_one_time_changes()
    one_time.setdefault(date, []).append({'time': time, 'available': True})
    save_one_time_changes(session.get('business_name'), one_time)
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
        save_bot_knowledge(business_name, content)
        return redirect("/main_admin")

    content = load_bot_knowledge(session.get('business_name'))
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
    save_appointments(business_name, appointments)

    overrides = load_overrides(business_name)
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
        appointments = load_appointments(session.get('business_name'))
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

    save_appointments(session.get('business_name'), appointments)


    overrides = load_overrides(session.get('business_name'))

    if date not in overrides:
        overrides[date] = {"add": [], "remove": [], "edit": []}

    if time in overrides[date].get("remove", []):
        overrides[date]["remove"].remove(time)

    if time not in overrides[date].get("add", []):
        overrides[date]["add"].append(time)

    save_overrides(session.get('business_name'), overrides)

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
