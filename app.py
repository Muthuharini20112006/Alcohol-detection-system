from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
from dotenv import load_dotenv
import threading
import time
import sqlite3
from contextlib import contextmanager

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration
THRESHOLD = float(os.getenv('THRESHOLD', 0.08))
SENDER_EMAIL = os.getenv('EMAIL_SENDER')
SENDER_PASSWORD = os.getenv('EMAIL_PASSWORD')
RECEIVER_EMAIL = os.getenv('EMAIL_RECEIVER')

# Global variables
monitoring_active = False
monitoring_thread = None
current_level = 0.0
readings_history = []
timestamps_history = []
alert_sent_for_current_exceedance = False

# ============ DATABASE SETUP ============
def init_database():
    """Initialize SQLite database for storing all readings"""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alcohol_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alcohol_level REAL NOT NULL,
                threshold REAL NOT NULL,
                alert_sent INTEGER DEFAULT 0,
                status TEXT DEFAULT 'NORMAL'
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alcohol_level REAL NOT NULL,
                email_sent INTEGER DEFAULT 0,
                recipient TEXT
            )
        ''')
        conn.commit()

@contextmanager
def get_db():
    """Get database connection"""
    conn = sqlite3.connect('alcohol_detection.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def save_reading_to_db(alcohol_level, threshold, alert_sent, status):
    """Save each reading to database"""
    with get_db() as conn:
        conn.execute('''
            INSERT INTO alcohol_readings (timestamp, alcohol_level, threshold, alert_sent, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alcohol_level, threshold, int(alert_sent), status))
        conn.commit()

def save_alert_to_db(alcohol_level, email_sent):
    """Save alert to database"""
    with get_db() as conn:
        conn.execute('''
            INSERT INTO alert_history (timestamp, alcohol_level, email_sent, recipient)
            VALUES (?, ?, ?, ?)
        ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alcohol_level, int(email_sent), RECEIVER_EMAIL))
        conn.commit()

# ============ EMAIL ALERT FUNCTION ============
def send_email_alert(alcohol_level):
    """Send email alert when alcohol is detected"""
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = "🚨 ALCOHOL DETECTION ALERT - Immediate Action Required"
        
        body = f"""
        ⚠️ ALCOHOL DETECTION SYSTEM ALERT ⚠️
        ====================================
        
        Status: ALCOHOL DETECTED - THRESHOLD EXCEEDED!
        
        Details:
        ---------
        Alcohol Level: {alcohol_level}% BAC
        Threshold Limit: {THRESHOLD}% BAC
        Time of Detection: {current_time}
        
        🚫 WARNING: Alcohol level exceeds safe limit!
        
        Required Actions:
        -----------------
        1. Check the person immediately
        2. Do NOT allow driving or operating machinery
        3. Ensure safety measures are taken
        4. Provide assistance if needed
        
        This is an automated alert from your Alcohol Detection System.
        Please take appropriate action immediately.
        
        ====================================
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email alert sent at {current_time}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send email: {str(e)}")
        return False

# ============ SENSOR SIMULATION ============
def simulate_sensor():
    """Simulate alcohol sensor reading with realistic variations"""
    global current_level
    
    # Random walk simulation for realistic fluctuations
    change = random.uniform(-0.015, 0.015)
    current_level += change
    
    # Keep within realistic bounds (0.00 to 0.15)
    current_level = max(0.00, min(0.15, current_level))
    
    # Occasional spikes for testing (10% chance)
    if random.random() < 0.1:
        current_level = random.uniform(0.07, 0.14)
    
    return round(current_level, 3)

# ============ MONITORING LOOP ============
def monitoring_loop():
    """Continuous monitoring loop running in background thread"""
    global monitoring_active, alert_sent_for_current_exceedance, readings_history, timestamps_history
    
    while monitoring_active:
        # Read alcohol level
        alcohol_level = simulate_sensor()
        current_time = datetime.now()
        time_str = current_time.strftime("%H:%M:%S")
        
        # Check threshold
        exceeded = alcohol_level >= THRESHOLD
        status = 'ALERT' if exceeded else 'NORMAL'
        
        # Store history (keep last 50 readings for live chart)
        readings_history.append(alcohol_level)
        timestamps_history.append(time_str)
        
        if len(readings_history) > 50:
            readings_history.pop(0)
            timestamps_history.pop(0)
        
        # Handle alert
        alert_sent = False
        if exceeded:
            if not alert_sent_for_current_exceedance:
                print(f"🚨 ALERT: {alcohol_level}% BAC at {time_str}")
                email_sent = send_email_alert(alcohol_level)
                alert_sent_for_current_exceedance = True
                alert_sent = email_sent
                
                # Save alert to database
                if email_sent:
                    save_alert_to_db(alcohol_level, True)
        else:
            alert_sent_for_current_exceedance = False
        
        # Save every reading to database
        save_reading_to_db(alcohol_level, THRESHOLD, alert_sent, status)
        
        # Send real-time data to frontend via WebSocket
        socketio.emit('sensor_update', {
            'alcohol_level': alcohol_level,
            'threshold': THRESHOLD,
            'exceeded': exceeded,
            'timestamp': time_str,
            'alert_sent': alert_sent,
            'readings_history': readings_history,
            'timestamps_history': timestamps_history
        })
        
        # Wait 2 seconds before next reading
        time.sleep(2)

# ============ ROUTES ============
@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html', threshold=THRESHOLD)

@app.route('/start_monitoring')
def start_monitoring():
    """Start the monitoring system"""
    global monitoring_active, monitoring_thread, readings_history, timestamps_history, alert_sent_for_current_exceedance
    
    if not monitoring_active:
        monitoring_active = True
        alert_sent_for_current_exceedance = False
        readings_history = []
        timestamps_history = []
        
        # Start monitoring thread
        monitoring_thread = threading.Thread(target=monitoring_loop)
        monitoring_thread.daemon = True
        monitoring_thread.start()
        
        return {'status': 'started', 'message': 'Monitoring started successfully'}
    return {'status': 'already_running', 'message': 'Monitoring is already running'}

@app.route('/stop_monitoring')
def stop_monitoring():
    """Stop the monitoring system"""
    global monitoring_active
    monitoring_active = False
    return {'status': 'stopped', 'message': 'Monitoring stopped successfully'}

@app.route('/update_threshold', methods=['POST'])
def update_threshold():
    """Update the alcohol threshold value"""
    global THRESHOLD
    data = request.get_json()
    new_threshold = float(data.get('threshold', 0.08))
    THRESHOLD = new_threshold
    
    # Update .env file
    with open('.env', 'r') as f:
        lines = f.readlines()
    with open('.env', 'w') as f:
        for line in lines:
            if line.startswith('THRESHOLD='):
                f.write(f'THRESHOLD={new_threshold}\n')
            else:
                f.write(line)
    
    return {'status': 'success', 'new_threshold': THRESHOLD}

@app.route('/test_email')
def test_email():
    """Send a test email"""
    success = send_email_alert(0.08)
    return {'status': 'success' if success else 'failed', 'message': 'Test email sent' if success else 'Failed to send email'}

# ============ HISTORY API ENDPOINTS ============
@app.route('/api/history/all')
def get_all_history():
    """Get all readings from database"""
    limit = request.args.get('limit', 1000, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    with get_db() as conn:
        total = conn.execute('SELECT COUNT(*) as count FROM alcohol_readings').fetchone()['count']
        
        readings = conn.execute('''
            SELECT * FROM alcohol_readings 
            ORDER BY timestamp DESC 
            LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
        
        return jsonify({
            'total': total,
            'limit': limit,
            'offset': offset,
            'readings': [dict(row) for row in readings]
        })

@app.route('/api/history/alerts')
def get_alert_history():
    """Get all alert history"""
    with get_db() as conn:
        alerts = conn.execute('''
            SELECT * FROM alert_history 
            ORDER BY timestamp DESC 
            LIMIT 500
        ''').fetchall()
        
        return jsonify({
            'alerts': [dict(row) for row in alerts]
        })

@app.route('/api/history/date-range')
def get_history_by_date():
    """Get readings between dates"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    with get_db() as conn:
        query = '''
            SELECT * FROM alcohol_readings 
            WHERE date(timestamp) BETWEEN ? AND ?
            ORDER BY timestamp DESC
        '''
        readings = conn.execute(query, (start_date, end_date)).fetchall()
        
        return jsonify({
            'readings': [dict(row) for row in readings],
            'count': len(readings)
        })

@app.route('/api/history/export')
def export_history():
    """Export all data as CSV"""
    import csv
    from io import StringIO
    
    with get_db() as conn:
        readings = conn.execute('''
            SELECT id, timestamp, alcohol_level, threshold, alert_sent, status 
            FROM alcohol_readings 
            ORDER BY timestamp DESC
        ''').fetchall()
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Timestamp', 'Alcohol Level (%)', 'Threshold (%)', 'Alert Sent', 'Status'])
        
        for row in readings:
            writer.writerow([row['id'], row['timestamp'], row['alcohol_level'], row['threshold'], 'Yes' if row['alert_sent'] else 'No', row['status']])
        
        return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=alcohol_readings.csv'}

@app.route('/api/history/delete', methods=['POST'])
def delete_history():
    """Delete multiple records by IDs"""
    data = request.get_json()
    ids_to_delete = data.get('ids', [])
    
    if not ids_to_delete:
        return {'status': 'error', 'message': 'No IDs provided'}
    
    try:
        with get_db() as conn:
            placeholders = ','.join('?' * len(ids_to_delete))
            conn.execute(f'DELETE FROM alcohol_readings WHERE id IN ({placeholders})', ids_to_delete)
            conn.commit()
        
        return {'status': 'success', 'deleted_count': len(ids_to_delete)}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@app.route('/api/stats/summary')
def get_summary_stats():
    """Get summary statistics from all data"""
    with get_db() as conn:
        total_readings = conn.execute('SELECT COUNT(*) as count FROM alcohol_readings').fetchone()['count']
        total_alerts = conn.execute('SELECT COUNT(*) as count FROM alcohol_readings WHERE alert_sent = 1').fetchone()['count']
        avg_level = conn.execute('SELECT AVG(alcohol_level) as avg FROM alcohol_readings').fetchone()['avg']
        max_level = conn.execute('SELECT MAX(alcohol_level) as max FROM alcohol_readings').fetchone()['max']
        
        return jsonify({
            'total_readings': total_readings,
            'total_alerts': total_alerts,
            'average_level': round(avg_level, 3) if avg_level else 0,
            'max_level': round(max_level, 3) if max_level else 0
        })

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clear all history (admin function)"""
    with get_db() as conn:
        conn.execute('DELETE FROM alcohol_readings')
        conn.execute('DELETE FROM alert_history')
        conn.commit()
    return {'status': 'success', 'message': 'All history cleared'}

# ============ MAIN EXECUTION ============
if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    
    print("=" * 60)
    print("🚗 ALCOHOL DETECTION SYSTEM - WEBSITE")
    print("=" * 60)
    print(f"📊 Current Threshold: {THRESHOLD}% BAC")
    print(f"📧 Sender Email: {SENDER_EMAIL}")
    print(f"📬 Receiver Email: {RECEIVER_EMAIL}")
    print(f"🌐 Website URL: http://127.0.0.1:5000")
    print(f"⚠️  Press Ctrl+C to stop the server")
    print("=" * 60)
    print("🚀 Starting server...")
    
    socketio.run(app, debug=True, host='127.0.0.1', port=5000)