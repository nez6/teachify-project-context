// Teachify desk node for an ESP32 board. Not yet compiled or run on hardware.
// Arduino libraries: Adafruit NeoPixel. Set the pins for your board.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Adafruit_NeoPixel.h>

const char *WIFI_SSID = "YOUR_WIFI";
const char *WIFI_PASSWORD = "YOUR_PASSWORD";
// Your Cloud Run URL, or http://<laptop-ip>:8080 on a local network.
const String SERVER = "https://YOUR-SERVICE.run.app";
const String DEVICE = "desk-07";

const int BUTTON_PIN = 3;   // Button to ground, internal pull-up
const int LED_PIN = 4;
const int LED_COUNT = 16;   // Use 1 for a single WS2812 on a breadboard
const unsigned long POLL_MS = 1000;
const unsigned long DEBOUNCE_MS = 40;

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
String led = "idle";
unsigned long lastPoll = 0, lastEdge = 0;
int stableButton = HIGH, lastRead = HIGH;

bool request(const String &path, const char *body, String *reply) {
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  bool isHttps = SERVER.startsWith("https");
  // Demo only: skips TLS certificate checks. Pin a root CA before real use.
  if (isHttps) secure.setInsecure();
  bool opened = isHttps ? http.begin(secure, SERVER + path) : http.begin(plain, SERVER + path);
  if (!opened) return false;
  http.setTimeout(4000);
  int code;
  if (body) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
  } else {
    code = http.GET();
  }
  bool ok = code >= 200 && code < 300;
  if (ok && reply) *reply = http.getString();
  http.end();
  return ok;
}

void showLed(unsigned long now) {
  uint8_t r = 0, g = 0, b = 0;
  if (led == "waiting") { r = 255; g = 120; }
  else if (led == "coming") { g = 70; b = 255; }
  else if (led == "resolved") { g = 220; b = 90; }
  float pulse = (led == "waiting") ? 0.45 + 0.35 * sin(now / 260.0) : 0.8;
  if (led == "idle") { r = g = b = 4; pulse = 1; }
  ring.fill(ring.Color(r * pulse, g * pulse, b * pulse));
  ring.show();
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  ring.begin();
  ring.setBrightness(90);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    ring.fill(ring.Color(30, 30, 30)); ring.show(); delay(200);
    ring.clear(); ring.show(); delay(200);
  }
  Serial.println(WiFi.localIP());
}

void loop() {
  unsigned long now = millis();
  int reading = digitalRead(BUTTON_PIN);
  if (reading != lastRead) { lastEdge = now; lastRead = reading; }
  if (now - lastEdge > DEBOUNCE_MS && reading != stableButton) {
    stableButton = reading;
    if (stableButton == LOW) {
      if (request("/api/device/" + DEVICE + "/press", "{\"source\":\"hardware\"}", nullptr)) led = "waiting";
      else Serial.println("press failed");
    }
  }
  if (now - lastPoll > POLL_MS) {
    lastPoll = now;
    String reply;
    if (request("/api/device/" + DEVICE + "?poll=true", nullptr, &reply)) {
      if (reply.indexOf("waiting") >= 0) led = "waiting";
      else if (reply.indexOf("coming") >= 0) led = "coming";
      else if (reply.indexOf("resolved") >= 0) led = "resolved";
      else led = "idle";
    }
  }
  showLed(now);
  delay(10);
}
