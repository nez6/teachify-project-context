// Teachify desk camera for the Seeed XIAO ESP32S3 Sense.
// Not yet compiled or run on hardware.
//
// Board: "XIAO_ESP32S3" in the esp32 by Espressif core, with Tools > PSRAM set to "OPI PSRAM".
// Library: Adafruit NeoPixel. esp_camera ships with the core.
// Wiring: button from D0 to GND; WS2812 ring data to D1, power to 5V and GND.
//
// A button press takes a photo and POSTs the JPEG to /api/capture/<device>.
// The ring then follows GET /api/device/<device>?poll=true once a second.
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Adafruit_NeoPixel.h>
#include "esp_camera.h"

#if !__has_include("config.h")
#error "Copy config.example.h to config.h and fill in your Wi-Fi, server and token."
#endif
#include "config.h"

const int BUTTON_PIN = D0;  // Button to ground, internal pull-up
const int LED_PIN = D1;
const int LED_COUNT = 16;
const unsigned long POLL_MS = 1000;
const unsigned long DEBOUNCE_MS = 200;
const uint16_t POLL_TIMEOUT_MS = 4000;
const uint16_t UPLOAD_TIMEOUT_MS = 30000;  // The server may read the page before it replies.

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
String led = "idle";
unsigned long lastPoll = 0;
volatile bool capturePressed = false;
volatile unsigned long lastPressMs = 0;

void IRAM_ATTR onButton() {
  unsigned long now = millis();
  if (now - lastPressMs > DEBOUNCE_MS) {
    lastPressMs = now;
    capturePressed = true;
  }
}

void showLed() {
  uint32_t color = 0;
  if (led == "waiting") color = ring.Color(255, 120, 0);
  else if (led == "coming") color = ring.Color(0, 70, 255);
  else if (led == "resolved") color = ring.Color(0, 220, 90);
  ring.fill(color);
  ring.show();
}

bool startCamera() {
  if (!psramFound()) {
    Serial.println("No PSRAM. Set Tools > PSRAM to OPI PSRAM.");
    return false;
  }
  camera_config_t config = {};
  // Camera pins for the XIAO ESP32S3 Sense expansion board.
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = 10;
  config.pin_sccb_sda = 40;
  config.pin_sccb_scl = 39;
  config.pin_d7 = 48;
  config.pin_d6 = 11;
  config.pin_d5 = 12;
  config.pin_d4 = 14;
  config.pin_d3 = 16;
  config.pin_d2 = 18;
  config.pin_d1 = 17;
  config.pin_d0 = 15;
  config.pin_vsync = 38;
  config.pin_href = 47;
  config.pin_pclk = 13;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_UXGA;  // 1600 x 1200, enough to read a page of handwriting
  config.jpeg_quality = 10;            // Lower is sharper, 0 to 63
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

bool beginRequest(HTTPClient &http, WiFiClientSecure &secure, WiFiClient &plain, const String &path) {
  String url = String(SERVER_URL) + path;
  if (url.startsWith("https")) {
    // Demo only: skips TLS certificate checks. Pin a root CA before real use.
    secure.setInsecure();
    return http.begin(secure, url);
  }
  return http.begin(plain, url);
}

void uploadPhoto() {
  // The driver keeps frames ready, so drop them to photograph the page as it is now.
  for (int i = 0; i < 2; i++) {
    camera_fb_t *stale = esp_camera_fb_get();
    if (stale) esp_camera_fb_return(stale);
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Capture failed");
    return;
  }
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  if (beginRequest(http, secure, plain, "/api/capture/" DEVICE_ID)) {
    http.setTimeout(UPLOAD_TIMEOUT_MS);
    http.addHeader("Content-Type", "image/jpeg");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    int code = http.POST(fb->buf, fb->len);
    Serial.printf("Uploaded %u bytes: HTTP %d\n", (unsigned)fb->len, code);
    http.end();
  } else {
    Serial.println("Could not open upload connection");
  }
  esp_camera_fb_return(fb);
}

void pollLed() {
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  if (!beginRequest(http, secure, plain, "/api/device/" DEVICE_ID "?poll=true")) return;
  http.setTimeout(POLL_TIMEOUT_MS);
  int code = http.GET();
  if (code >= 200 && code < 300) {
    String reply = http.getString();
    if (reply.indexOf("\"waiting\"") >= 0) led = "waiting";
    else if (reply.indexOf("\"coming\"") >= 0) led = "coming";
    else if (reply.indexOf("\"resolved\"") >= 0) led = "resolved";
    else led = "idle";
    showLed();
  }
  http.end();
}

void connectWifi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    ring.fill(ring.Color(30, 30, 30)); ring.show(); delay(200);
    ring.clear(); ring.show(); delay(200);
  }
  Serial.println(WiFi.localIP());
  showLed();
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  ring.begin();
  ring.setBrightness(90);
  ring.clear();
  ring.show();
  if (!startCamera()) {
    // Fast red blink: the camera or PSRAM setup is wrong, and nothing else can work.
    while (true) {
      ring.fill(ring.Color(120, 0, 0)); ring.show(); delay(150);
      ring.clear(); ring.show(); delay(150);
    }
  }
  WiFi.setAutoReconnect(true);
  connectWifi();
  attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), onButton, FALLING);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (capturePressed) {
    uploadPhoto();
    capturePressed = false;  // Presses during an upload are ignored.
    pollLed();
    lastPoll = millis();
  }
  if (millis() - lastPoll >= POLL_MS) {
    lastPoll = millis();
    pollLed();
  }
  delay(10);
}
