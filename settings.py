
# ---------------------- Imports ----------------------
from flask import Flask, render_template, request, session, redirect, jsonify
from psycopg2 import connect
import os

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"

BUSINESSES_ROOT = "./businesses"

# ---------------------- Database connection ----------------------
def get_db_connection():
    return connect(
        dbname="your_db_name",
        user="your_user",
        password="your_password",
        host="localhost",
        port=5432
    )

# ---------------------- Load & Save business settings ----------------------
def load_business_settings(business_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM business_settings WHERE business_id=%s", (business_id,))
    row = cur.fetchone()
    colnames = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()
    if row:
        return dict(zip(colnames, row))
    else:
        return None

def save_business_settings(business_id, settings_data):
    conn = get_db_connection()
    cur = conn.cursor()

    # Build dynamic SQL SET part
    set_parts = []
    values = []
    for key, val in settings_data.items():
        set_parts.append(f"{key}=%s")
        values.append(val)

    values.append(business_id)
    sql = f"UPDATE business_settings SET {', '.join(set_parts)} WHERE business_id=%s"
    cur.execute(sql, values)
    conn.commit()
    cur.close()
    conn.close()

# ---------------------- Flask Routes ----------------------
@app.route('/settings/<int:business_id>', methods=['GET'])
def settings_page(business_id):
    if not session.get('is_host'):
        return redirect('/login')

    settings = load_business_settings(business_id)
    if not settings:
        return "לא נמצאו הגדרות לעסק הזה", 404

    return render_template('business_settings.html', settings=settings)

@app.route('/settings/<int:business_id>', methods=['POST'])
def update_settings(business_id):
    if not session.get('is_host'):
        return redirect('/login')

    settings_data = request.form.to_dict()
    save_business_settings(business_id, settings_data)
    return jsonify({"success": True, "message": "ההגדרות נשמרו בהצלחה"})

# ---------------------- HTML Template (business_settings.html) ----------------------
"""
<!DOCTYPE html>
<html lang="he">
<head>
<meta charset="UTF-8">
<title>הגדרות העסק</title>
</head>
<body>
<h1>הגדרות העסק</h1>
<form id="settingsForm">
  {% for key, val in settings.items() %}
    {% if key != 'id' and key != 'business_id' %}
      <label>{{ key }}:</label>
      <input type="text" name="{{ key }}" value="{{ val }}"><br>
    {% endif %}
  {% endfor %}
  <button type="submit">שמור</button>
</form>

<script>
document.getElementById("settingsForm").addEventListener("submit", async function(e){
    e.preventDefault();
    const formData = new FormData(this);
    const data = {};
    formData.forEach((v,k) => data[k] = v);
    const response = await fetch("", {
        method: "POST",
        headers: {"Content-Type":"application/x-www-form-urlencoded"},
        body: new URLSearchParams(data)
    });
    const result = await response.json();
    alert(result.message);
});
</script>
</body>
</html>
"""
