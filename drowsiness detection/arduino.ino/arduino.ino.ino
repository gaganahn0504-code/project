// arduino_serial.ino

int relayPin = 7; // Change to your actual relay pin

void setup() {
  pinMode(relayPin, OUTPUT);
  digitalWrite(relayPin, LOW); // Initially motor ON (assuming LOW turns OFF)
  Serial.begin(9600);
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim(); // Remove trailing newline or spaces

    if (command == "ON") {
      digitalWrite(relayPin, LOW);  // Turn motor ON
      Serial.println("Motor ON");
    } else if (command == "OFF") {
      digitalWrite(relayPin, HIGH);   // Turn motor OFF
      Serial.println("Motor OFF");
    } else {
      Serial.println("Unknown command");
    }
  }
}