from flask import Flask, request, jsonify
import serial
import time

app = Flask(__name__)

SERIAL_PORT = 'COM3'  # Update as per your system (e.g., 'COM4', '/dev/ttyUSB0')
BAUD_RATE = 9600

current_status = "OFF"  # Initial motor status

# Connect to Arduino
try:
    arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)  # Wait for Arduino to initialize
    print(f"Connected to Arduino on {SERIAL_PORT}")
except serial.SerialException as e:
    arduino = None
    print(f"Could not open serial port {SERIAL_PORT}: {e}")

def send_command_to_arduino(command):
    global current_status
    if arduino and arduino.is_open:
        print(f"🔁 Sending command to Arduino: {command}")
        arduino.write((command + '\n').encode('utf-8'))
        current_status = command  # Update internal state
    else:
        print("❌ Serial connection not available.")

@app.route('/trigger', methods=['POST'])
def trigger():
    data = request.get_json()
    command = data.get("command", "").strip().upper()

    if command in ["ON", "OFF"]:
        send_command_to_arduino(command)
        return jsonify({"status": "success", "message": f"Relay {command} command sent"}), 200
    else:
        return jsonify({"status": "error", "message": "Invalid command"}), 400

@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": current_status})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5001, debug=False)
