@echo off
cd /d "C:\Users\Kriti Dev\Downloads\drowsiness detection\drowsiness detection"

echo Starting serial_relay_control.py (production mode)...
start cmd /k "python serial_relay_control.py"

timeout /t 3 >nul

echo Starting app.py...
start cmd /k "python app.py"

pause >nul
