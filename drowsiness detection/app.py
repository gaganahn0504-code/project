import os
import platform
import threading
import time
import requests
import cv2
import dlib
import numpy as np
import pyttsx3
from flask import Flask, Response, render_template, jsonify, request, redirect, session, url_for
from scipy.spatial import distance as dist
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from flask_mysqldb import MySQL
from datetime import datetime
from functools import wraps
import serial
import serial.tools.list_ports

app = Flask(__name__)
app.secret_key = "super_secret_key"  # Change this in production!
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = '1234'  # Consider using environment variables for credentials
app.config['MYSQL_DB'] = 'drowsiness_detection'
mysql = MySQL(app)

# Token generator
app.config['SECURITY_PASSWORD_SALT'] = 'your_salt_here'  # Change this in production!
serializer = URLSafeTimedSerializer(app.secret_key)

# Email Configuration (Sender)
SENDER_EMAIL = 'gaganagaganahn@gmail.com'
SENDER_PASSWORD = 'cwlj obhl czbf ggsw'  # Use App Password for Gmail if 2FA is enabled

# Load face detector & landmark predictor
detector = dlib.get_frontal_face_detector()
predictor_path = os.path.join(os.getcwd(), "shape_predictor_68_face_landmarks.dat")
if not os.path.exists(predictor_path):
    raise FileNotFoundError(f"Error: The shape predictor file '{predictor_path}' was not found. Please download it.")

predictor = dlib.shape_predictor(predictor_path)

# Parameters
params = {
    "EAR_THRESHOLD": 0.25,
    "CLOSED_EYE_TIME_THRESHOLD": 2,  # seconds
    "CONSECUTIVE_FRAMES": 10,
    "EMAIL_COOLDOWN": 300,  # 5 minutes cooldown between emails for the same user
}

# Global variables
camera_lock = threading.Lock()
voice_lock = threading.Lock()
cap = None
is_running = False
start_time = None
last_alert_time_per_user = {}  # Store last alert time per user
engine = None
alert_active = False
drowsy_frames = 0
current_user = None
current_car = None
current_car_number = None
current_user_email = None
dashboard_alert_status = None


# Helper Functions
def eye_aspect_ratio(eye):
    if len(eye) != 6:
        return 0.0
    A = dist.euclidean(eye[1], eye[5])
    B = dist.euclidean(eye[2], eye[4])
    C = dist.euclidean(eye[0], eye[3])
    if C == 0:
        return 0.0
    return (A + B) / (2.0 * C)

def draw_eye_landmarks(frame, landmarks, color=(0, 255, 0)):
    if landmarks is None or landmarks.num_parts < 48:
        return

    for n in range(36, 48):
        try:
            part = landmarks.part(n)
            x = part.x
            y = part.y
            cv2.circle(frame, (x, y), 2, color, -1)
        except Exception as e:
            print(f"Error drawing landmark {n}: {e}")
            continue

def init_engine():
    global engine
    if engine is None:
        print("Attempting to initialize TTS engine...")
        try:
            engine = pyttsx3.init()
            if engine:
                engine.setProperty('rate', 150)
                engine.setProperty('volume', 0.9)
                print("TTS engine initialized successfully.")
            else:
                print("TTS engine initialization returned None.")
                engine = None
        except Exception as e:
            print(f"Error initializing TTS engine: {e}")
            engine = None


def start_motor():
    """Send HTTP request to turn motor ON via serial_relay_control.py"""
    try:
        import requests
        requests.post("http://localhost:5001/trigger", json={"command": "ON"})
        print(" Motor ON signal sent to Arduino via HTTP.")
    except Exception as e:
        print(f"Failed to send motor ON signal: {e}")


def stop_motor():
    """Send HTTP request to turn motor OFF via serial_relay_control.py"""
    try:
        import requests
        requests.post("http://localhost:5001/trigger", json={"command": "OFF"})
        print("Motor OFF signal sent to Arduino via HTTP.")
    except Exception as e:
        print(f"Failed to send motor OFF signal: {e}")



def get_motor_status():
    """Check the current motor status from the relay control service."""
    try:
        response = requests.get("http://localhost:5001/status")
        if response.status_code == 200:
            return response.json().get("status", "UNKNOWN")
        else:
            return "UNKNOWN"
    except Exception as e:
        print(f"Failed to get motor status: {e}")
        return "ERROR"
def sound_alarm():
    global alert_active
    print("Starting sound alarm thread...")
    while alert_active:
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.Beep(1000, 700)
            elif platform.system() == "Darwin":
                os.system("afplay /System/Library/Sounds/Glass.aiff")
            else:
                os.system("beep -f 1000 -l 700")
            time.sleep(1)
        except ImportError:
            print("Skipping sound alert: 'winsound' module not available on this OS.")
            break
        except FileNotFoundError:
            print("Skipping sound alert: Sound file not found (macOS).")
            break
        except OSError:
            print("Skipping sound alert: 'beep' command not found or failed (Linux). Install 'beep' package.")
            break
        except Exception as e:
            print(f"Error in playing beep alert: {e}")
            time.sleep(1)
    print("Sound alarm thread finished.")

def voice_alert():
    global alert_active, engine
    print("Starting voice alert thread...")
    init_engine()

    if engine is None:
        print("❌ Skipping voice alert: TTS engine not available or failed to initialize.")
        return

    while alert_active:
        print("Voice alert loop: Active. Trying to speak...")
        try:
            with voice_lock:
                if not engine.isBusy():
                    print("Engine not busy, attempting to say warning...")
                    engine.say("Warning! Drowsiness detected! Wake up!")
                    engine.runAndWait()
                    print("Voice warning delivered.")
                else:
                    print("Engine is busy, skipping this cycle.")
            time.sleep(1)
        except Exception as e:
            print(f"❌ Error during engine.say/runAndWait: {e}")
            time.sleep(2)
    print("Voice alert thread finished.")

def stop_alerts():
    global alert_active, engine, dashboard_alert_status
    if alert_active:
        print("Stopping alerts...")
        alert_active = False
        dashboard_alert_status = None
        stop_motor()  # Stop motor when alerts are stopped
        if engine:
            try:
                with voice_lock:
                    print("Attempting to stop TTS engine...")
                    engine.stop()
                    print("TTS engine stopped.")
            except Exception as e:
                print(f"Error stopping TTS engine: {e}")
        print("Alerts stopped.")

def send_email(recipient_email, subject, body):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
            print(f"✅ Email successfully sent to {recipient_email}")
            return True
    except smtplib.SMTPAuthenticationError:
        print(f"Error: Email authentication failed for {SENDER_EMAIL}. Check credentials or 'App Password' for Gmail.")
    except smtplib.SMTPException as e:
        print(f"SMTP error occurred sending to {recipient_email}: {str(e)}")
    except Exception as e:
        print(f"Unexpected error sending email to {recipient_email}: {str(e)}")
    return False

def send_alert_email(recipient_email, driver_id, car, car_number):
    global last_alert_time_per_user, dashboard_alert_status

    if not recipient_email:
        print(" Cannot send alert email: Recipient email is missing.")
        return False

    current_time = time.time()
    user_last_alert_time = last_alert_time_per_user.get(driver_id, 0)

    if (current_time - user_last_alert_time) < params["EMAIL_COOLDOWN"]:
        print(f"Email cooldown active for driver {driver_id}. Last alert sent {current_time - user_last_alert_time:.0f}s ago.")
        dashboard_alert_status = "Drowsiness detected! Please check your email (Alert recently sent)."
        return False

    subject = f"🚨 DROWSINESS ALERT: Driver {driver_id} needs immediate attention!"
    body = f"""
    DROWSINESS DETECTED!

    Driver ID: {driver_id}
    Email: {recipient_email}
    Vehicle: {car} ({car_number})
    Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    Immediate attention is required to prevent potential accidents.

    System: Drowsiness Detection System
    """

    if send_email(recipient_email, subject, body):
        last_alert_time_per_user[driver_id] = current_time
        dashboard_alert_status = "Drowsiness detected! Please check your email."
        return True
    else:
        return False

# Login Required Decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# --- Flask Routes ---

@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        driver_id = request.form.get("driver_id")
        password = request.form.get("password")

        if not driver_id or not password:
            error = "Driver ID and Password are required."
            return render_template("login.html", error=error)

        cur = mysql.connection.cursor()
        cur.execute("SELECT driver_id, password, car_brand, car_number, driving_date, email FROM users WHERE driver_id = %s", (driver_id,))
        user = cur.fetchone()
        cur.close()

        if user and user[1] == password:
            session["user"] = user[0]
            session["car"] = user[2]
            session["car_number"] = user[3]
            session["date"] = str(user[4])
            session["email"] = user[5]

            global current_user, current_car, current_car_number, current_user_email
            current_user = user[0]
            current_car = user[2]
            current_car_number = user[3]
            current_user_email = user[5]

            print(f"User {current_user} ({current_user_email}) logged in.")

            next_url = request.args.get('next')
            return redirect(next_url or url_for("index"))
        else:
            error = "Invalid driver ID or password"

    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    global current_user, current_car, current_car_number, current_user_email, is_running, cap, dashboard_alert_status
    print(f"User {session.get('user')} logging out.")
    session.clear()
    current_user = None
    current_car = None
    current_car_number = None
    current_user_email = None
    dashboard_alert_status = None

    with camera_lock:
        if is_running:
            is_running = False
            stop_alerts()
            if cap and cap.isOpened():
                cap.release()
                cap = None
            print("Video stopped on logout.")

    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        driver_id = request.form.get("driver_id")
        password = request.form.get("password")
        email = request.form.get("email")
        car_brand = request.form.get("car_brand")
        car_number = request.form.get("car_number")
        driving_date = request.form.get("driving_date")

        if not all([driver_id, password, email, car_brand, car_number, driving_date]):
            error = "All fields are required."
            return render_template("register.html", error=error)

        if "@" not in email or "." not in email:
            error = "Invalid email format."
            return render_template("register.html", error=error, form_data=request.form)

        try:
            datetime.strptime(driving_date, '%Y-%m-%d')
        except ValueError:
            error = "Invalid date format. Please use YYYY-MM-DD."
            return render_template("register.html", error=error, form_data=request.form)

        cur = mysql.connection.cursor()
        cur.execute("SELECT driver_id FROM users WHERE driver_id = %s OR email = %s", (driver_id, email))
        existing_user = cur.fetchone()

        if existing_user:
            cur.close()
            if existing_user[0] == driver_id:
                error = "Driver ID already exists!"
            else:
                error = "Email address already registered!"
            return render_template("register.html", error=error, form_data=request.form)

        try:
            cur.execute("INSERT INTO users (driver_id, password, email, car_brand, car_number, driving_date) VALUES (%s, %s, %s, %s, %s, %s)",
                        (driver_id, password, email, car_brand, car_number, driving_date))
            mysql.connection.commit()
            cur.close()
            print(f"User {driver_id} registered successfully.")
            return redirect(url_for("login"))
        except Exception as e:
            mysql.connection.rollback()
            cur.close()
            print(f"Error during registration: {e}")
            error = "Registration failed. Please try again."
            return render_template("register.html", error=error, form_data=request.form)

    return render_template("register.html", error=error)

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    message = None
    error = None
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            error = "Please enter your email address."
            return render_template("forgot.html", error=error)

        cur = mysql.connection.cursor()
        cur.execute("SELECT driver_id FROM users WHERE email = %s", (email,))
        user_exists = cur.fetchone()
        cur.close()

        if user_exists:
            try:
                reset_token = serializer.dumps(email, salt=app.config['SECURITY_PASSWORD_SALT'])
                reset_link = url_for("reset_password", token=reset_token, _external=True)
                email_subject = "Password Reset Request"
                email_body = f"You requested a password reset. Click the link below to set a new password. This link is valid for 1 hour:\n\n{reset_link}\n\nIf you did not request this, please ignore this email."

                if send_email(email, email_subject, email_body):
                    message = "If an account with that email exists, a password reset link has been sent."
                else:
                    error = "Failed to send reset email. Please try again later or contact support."
            except Exception as e:
                print(f"Error generating reset token or sending email: {e}")
                error = "An error occurred. Please try again."
        else:
             message = "If an account with that email exists, a password reset link has been sent."

    return render_template("forgot.html", message=message, error=error)

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    error = None
    message = None

    try:
        email = serializer.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'], max_age=3600)
    except SignatureExpired:
        error = "The password reset link has expired. Please request a new one."
        return render_template("reset_password.html", error=error, token=None)
    except BadTimeSignature:
        error = "Invalid password reset link."
        return render_template("reset_password.html", error=error, token=None)
    except Exception as e:
        print(f"Token verification error: {e}")
        error = "Invalid or corrupted password reset link."
        return render_template("reset_password.html", error=error, token=None)

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or not confirm_password:
            error = "Please enter and confirm your new password."
            return render_template("reset_password.html", error=error, token=token)

        if new_password != confirm_password:
            error = "Passwords do not match."
            return render_template("reset_password.html", error=error, token=token)

        try:
            cur = mysql.connection.cursor()
            cur.execute("UPDATE users SET password = %s WHERE email = %s", (new_password, email))
            mysql.connection.commit()
            cur.close()
            print(f"Password updated successfully for email: {email}")
            message = "Your password has been reset successfully. You can now log in."
            return redirect(url_for("login"))
        except Exception as e:
            mysql.connection.rollback()
            print(f"Error updating password for {email}: {e}")
            error = "Failed to update password. Please try again."
            return render_template("reset_password.html", error=error, token=None)

    return render_template("reset_password.html", error=error, message=message, token=token)

@app.route("/index")
@login_required
def index():
    driving_date_str = session.get("date", "")
    formatted_date = driving_date_str
    try:
        driving_date_obj = datetime.strptime(driving_date_str, '%Y-%m-%d')
        formatted_date = driving_date_obj.strftime('%B %d, %Y')
    except (ValueError, TypeError):
        print(f"Could not parse date string: {driving_date_str}")

    user_details = {
        "user": session.get("user"),
        "car": session.get("car"),
        "car_number": session.get("car_number"),
        "date": formatted_date,
        "email": session.get("email")
    }
    return render_template("index.html", **user_details)

@app.route("/start_video")
@login_required
def start_video():
    global is_running, cap
    with camera_lock:
        if not is_running:
            try:
                print("Attempting to open camera...")
                cap = cv2.VideoCapture(0)
                if not cap.isOpened():
                    print("Default camera (0) failed, trying alternatives...")
                    for i in range(1, 4):
                        cap = cv2.VideoCapture(i)
                        if cap.isOpened():
                            print(f"Camera opened successfully on index {i}")
                            break
                    if not cap.isOpened():
                        raise IOError("Unable to access any camera.")

                is_running = True
                global start_time, alert_active, drowsy_frames, dashboard_alert_status
                start_time = None
                alert_active = False
                drowsy_frames = 0
                dashboard_alert_status = None
                if current_user in last_alert_time_per_user:
                    del last_alert_time_per_user[current_user]

                # --- MOTOR CONTROL: Start motor on video start ---
                motor_status = get_motor_status()
                if motor_status != "ON":
                    print("Motor is OFF, starting motor...")
                    start_motor() # <--- START MOTOR HERE
                    motor_status = get_motor_status() # Re-check status after command
                    print(f"Motor status after starting: {motor_status}")
                else:
                    print("Motor is already ON.")
                # --- END MOTOR CONTROL ---

                print(f"Video started for user {current_user}")
                return jsonify({
                    "status": "started",
                    "running": is_running,
                    "motor_status": motor_status # Return current motor status
                })

            except Exception as e:
                print(f"Error starting camera: {e}")
                is_running = False
                if cap:
                    cap.release()
                cap = None
                # Attempt to stop motor if an error occurred during startup
                # stop_motor() # Optional: Decide if motor should stop on camera error
                return jsonify({"status": "error", "message": f"Unable to access the camera: {e}"})
        else:
            # If already running, just return current status including motor
            motor_status = get_motor_status()
            print("Video already running request received.")
            return jsonify({
                "status": "already_running",
                "running": is_running,
                "motor_status": motor_status
            })

@app.route("/stop_video")
@login_required
def stop_video():
    global is_running, cap
    stopped = False
    motor_status_before_stop = get_motor_status() # Get status before stopping
    print(f"Stop video request received. Current motor status: {motor_status_before_stop}")

    with camera_lock:
        if is_running:
            is_running = False # Stop the video feed loop first
            stop_alerts() # Stop any active sound/voice alerts

            if cap and cap.isOpened():
                print("Releasing camera...")
                cap.release()
                cap = None
                print("Camera released.")

            # --- MOTOR CONTROL: Stop motor on manual video stop ---
            print("Stopping motor...")
            stop_motor() # <--- STOP MOTOR HERE
            # --- END MOTOR CONTROL ---

            stopped = True
            print(f"Video stopped for user {current_user}. Motor commanded OFF.")

    if stopped:
        # Return 'OFF' as the commanded status after stopping
        return jsonify({"status": "stopped", "running": is_running, "motor_status": "OFF"})
    else:
        print("Stop video request received, but video was already stopped.")
        # Return the last known status if it was already stopped
        return jsonify({"status": "already_stopped", "running": is_running, "motor_status": motor_status_before_stop})


@app.route("/video_feed")
@login_required
def video_feed():
    def generate():
        global is_running, cap, start_time, alert_active, drowsy_frames
        global current_user, current_car, current_car_number, current_user_email
        global dashboard_alert_status

        print(f"Video feed starting generation for user {current_user}")

        while is_running: # Check the flag in each iteration
            frame = None
            if not is_running: # Double check before acquiring frame
                 print("Video feed loop detected is_running is false, breaking.")
                 break
            with camera_lock:
                if cap is None or not cap.isOpened():
                    print("Video feed: Camera not available or closed.")
                    is_running = False # Ensure loop terminates
                    break # Exit the loop

                success, frame = cap.read()
                if not success or frame is None:
                    print("Warning: Failed to capture frame or empty frame received.")
                    time.sleep(0.1) # Wait a bit before retrying
                    continue # Skip processing for this iteration

            # --- Frame Processing and Drowsiness Detection ---
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector(gray)
                status = "Normal"
                color = (0, 255, 0)
                ear = -1.0 # Default EAR if no face/landmarks

                if not faces:
                    status = "No Face Detected"
                    color = (255, 165, 0) # Orange
                    if alert_active:
                        print("No face detected, resetting ongoing alert.")
                        stop_alerts()
                        # --- MOTOR CONTROL: Restart motor if alert cleared due to no face ---
                        print("Restarting motor as alert cleared (no face).")
                        start_motor() # <--- RESTART MOTOR HERE
                        # --- END MOTOR CONTROL ---
                        alert_active = False # Reset alert flag
                    start_time = None
                    drowsy_frames = 0

                for face in faces:
                    landmarks = predictor(gray, face)

                    if landmarks.num_parts != 68:
                        print("Warning: Incorrect number of landmarks detected.")
                        continue # Skip this face

                    # Draw face rectangle (optional)
                    # x, y, w, h = face.left(), face.top(), face.width(), face.height()
                    # cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)

                    try:
                        left_eye = np.array([(landmarks.part(n).x, landmarks.part(n).y) for n in range(36, 42)], dtype=np.int32)
                        right_eye = np.array([(landmarks.part(n).x, landmarks.part(n).y) for n in range(42, 48)], dtype=np.int32)
                    except Exception as e:
                        print(f"Error accessing landmark parts: {e}")
                        continue # Skip this face if landmarks are bad

                    left_ear = eye_aspect_ratio(left_eye)
                    right_ear = eye_aspect_ratio(right_eye)
                    ear = (left_ear + right_ear) / 2.0

                    # Draw eye landmarks (optional)
                    # draw_eye_landmarks(frame, landmarks, color=(0, 255, 0))

                    # --- Drowsiness Logic ---
                    if ear < params["EAR_THRESHOLD"]:
                        drowsy_frames += 1

                        if drowsy_frames >= params["CONSECUTIVE_FRAMES"]:
                            if start_time is None:
                                start_time = time.time() # Start timer only when consecutive frames are met

                            # Check if eyes have been closed long enough
                            if time.time() - start_time >= params["CLOSED_EYE_TIME_THRESHOLD"]:
                                status = "Drowsy!"
                                color = (0, 0, 255) # Red

                                # --- Trigger Alert Actions ---
                                if not alert_active:
                                    print(f"ALERT triggered for {current_user} at EAR {ear:.2f}!")
                                    alert_active = True # Set alert flag

                                    # --- MOTOR CONTROL: Stop motor on drowsiness alert ---
                                    print("Stopping motor due to drowsiness alert.")
                                    stop_motor() # <--- STOP MOTOR HERE
                                    # --- END MOTOR CONTROL ---

                                    # Start alerts in separate threads
                                    threading.Thread(target=sound_alarm, daemon=True).start()
                                    threading.Thread(target=voice_alert, daemon=True).start()

                                    # Send email alert
                                    if current_user and current_user_email:
                                        send_alert_email(current_user_email, current_user, current_car, current_car_number)
                                    else:
                                        print("Cannot send alert email: User details missing.")
                                        dashboard_alert_status = "Drowsiness detected! User details missing for email."
                                # --- End Trigger Alert Actions ---

                            else:
                                # Eyes closed, but not long enough for full alert yet
                                status = "Eyes Closing"
                                color = (0, 255, 255) # Yellow
                        else:
                            # EAR is low, but not for enough consecutive frames
                            status = "Blinking?"
                            color = (0, 255, 255) # Yellow
                            start_time = None # Reset timer if eyes opened briefly

                    else: # ear >= EAR_THRESHOLD (Eyes are open)
                         # --- Reset Alert State if eyes are open ---
                        if alert_active:
                            print(f"Eyes open (EAR {ear:.2f}), resetting alert for {current_user}.")
                            stop_alerts() # Stop sound/voice

                            # --- MOTOR CONTROL: Restart motor when alert is cleared ---
                            print("Restarting motor as alert cleared (eyes open).")
                            start_motor() # <--- RESTART MOTOR HERE
                            # --- END MOTOR CONTROL ---

                            alert_active = False # Reset the alert flag
                        # --- End Reset Alert State ---

                        # Reset counters and status for normal operation
                        drowsy_frames = 0
                        start_time = None
                        status = "Normal"
                        color = (0, 255, 0) # Green

                # --- Draw Status Text on Frame ---
                cv2.putText(frame, f"Status: {status}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                if ear >= 0: # Only display EAR if calculated
                    cv2.putText(frame, f"EAR: {ear:.2f}", (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                else: # Display N/A if no face or error
                     cv2.putText(frame, "EAR: N/A", (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            except Exception as e:
                print(f"Error during frame processing: {e}")
                # Optionally draw error on frame
                if frame is not None:
                     cv2.putText(frame, "Processing Error", (10, 90),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2) # Red text for error

            # --- Encode and Yield Frame for Streaming ---
            if frame is not None:
                try:
                    ret, buffer = cv2.imencode('.jpg', frame)
                    if not ret:
                        print("Warning: Failed to encode frame to JPEG.")
                        continue # Skip this frame if encoding failed
                    frame_bytes = buffer.tobytes()
                    # Yield the frame in the required format for multipart streaming
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                except Exception as e:
                    print(f"Error encoding/yielding frame: {e}")
                    is_running = False # Stop loop on encoding error
                    break # Exit loop
            else:
                print("Frame was None before encoding.")


            # Small sleep to prevent high CPU usage (optional, adjust as needed)
            time.sleep(0.01)
        # --- End of while is_running loop ---

        print(f"Video feed generation loop stopped for user {current_user}")
        # --- Cleanup after loop ends ---
        with camera_lock:
            if cap and cap.isOpened():
                print("Releasing camera in generate function cleanup...")
                cap.release()
                cap = None
        stop_alerts() # Ensure alerts are stopped
        # Ensure motor is stopped if the loop exited while an alert was active
        if alert_active:
             print("Stopping motor during video feed cleanup as alert was active.")
             stop_motor() # <--- Ensure motor stops if loop breaks unexpectedly during alert
             alert_active = False
        # --- End Cleanup ---

    # Return the Response object with the generator function
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
@app.route("/get_alert_status")
@login_required
def get_alert_status():
    global dashboard_alert_status
    return jsonify({"alert_message": dashboard_alert_status})

@app.route("/test_email")
@login_required
def test_email():
    recipient = session.get("email")
    user = session.get("user")
    car = session.get("car")
    car_num = session.get("car_number")

    if not recipient:
        return jsonify({"status": "failed", "message": "No email found for the logged-in user."}), 400

    print(f"Attempting to send test alert email to {recipient} for user {user}")
    subject = f"TEST EMAIL: Drowsiness Alert System"
    body = f"""
    This is a test email from the Drowsiness Detection System.

    If you received this, the email sending functionality is working correctly for your account.

    Driver ID: {user} (Test)
    Email: {recipient}
    Vehicle: {car} ({car_num}) (Test)
    Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    System: Drowsiness Detection System
    """
    if send_email(recipient, subject, body):
        return jsonify({"status": "success", "message": f"Test email sent successfully to {recipient}!"})
    else:
        return jsonify({"status": "failed", "message": f"Failed to send test email to {recipient}. Check logs."}), 500

@app.route("/motor_control/<command>")
@login_required
def motor_control(command):
    if command == "start":
        start_motor()
        return jsonify({"status": "motor_started"})
    elif command == "stop":
        stop_motor()
        return jsonify({"status": "motor_stopped"})
    return jsonify({"status": "invalid_command"}), 400


if __name__ == "__main__":
    try:
        print("Initializing systems...")
        init_engine()
        print("Starting Flask application...")
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
    finally:
        print("Shutting down application...")
        if cap and cap.isOpened():
            cap.release()
        stop_alerts()
        stop_motor()