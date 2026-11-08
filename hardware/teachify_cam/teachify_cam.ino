// Teachify desk camera for the Seeed XIAO ESP32S3 Sense (OV2640).
// Not yet compiled or run on hardware.
//
// Core: esp32 by Espressif, 3.x. Board: XIAO_ESP32S3.
// Tools > PSRAM: OPI PSRAM (required; off by default for this board).
// Tools > Partition Scheme: Default with spiffs (3MB APP/1.5MB SPIFFS).
// arduino-cli FQBN: esp32:esp32:XIAO_ESP32S3:PSRAM=opi
// Library: Adafruit NeoPixel. esp_camera, WiFi and HTTPClient ship with the core.
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Adafruit_NeoPixel.h>
#include "esp_camera.h"

#ifndef BOARD_HAS_PSRAM
#error "UXGA capture needs PSRAM. Set Tools > PSRAM to OPI PSRAM."
#endif

const char *WIFI_SSID = "YOUR_WIFI";
const char *WIFI_PASS = "YOUR_PASSWORD";
// Your Cloud Run URL, or http://<laptop-ip>:8080 on a local network. No trailing slash.
const char *SERVER_URL = "https://YOUR-SERVICE.run.app";
const char *DEVICE_ID = "desk-07";
const char *STUDENT_ID = "12";
const char *PROBLEM_ID = "dist";
const char *DEVICE_TOKEN = "";  // Leave empty when the server has no DEVICE_TOKEN set

// The D0-D2 assignments are our own choice, not fixed by the board, so match them to your
// wiring: D1 is the button (to ground), D2 is the ring data line, and D0 is left free.
// Power the ring from 5V and share ground with the board.
const int BUTTON_PIN = D1;
const int LED_PIN = D2;
const int LED_COUNT = 16;
const uint8_t RING_BRIGHTNESS = 90;  // Limits the 5V draw at full white

const unsigned long POLL_MS = 1000;
const unsigned long DEBOUNCE_MS = 40;
const unsigned long RED_MS = 1500;
const unsigned long WIFI_RETRY_MS = 15000;
const int32_t CONNECT_TIMEOUT_MS = 5000;
const unsigned long TLS_HANDSHAKE_S = 5;
const uint16_t POLL_TIMEOUT_MS = 4000;
const uint16_t UPLOAD_TIMEOUT_MS = 30000;  // The server reads the page before it replies
const unsigned long UPLOAD_LOCK_MS = 12000;  // Longer than the slowest poll

// Camera pins for the XIAO ESP32S3 Sense, from CAMERA_MODEL_XIAO_ESP32S3 in the
// Espressif CameraWebServer camera_pins.h. They match Seeed's pin table.
const int PWDN_GPIO_NUM = -1;
const int RESET_GPIO_NUM = -1;
const int XCLK_GPIO_NUM = 10;
const int SIOD_GPIO_NUM = 40;
const int SIOC_GPIO_NUM = 39;
const int Y9_GPIO_NUM = 48;
const int Y8_GPIO_NUM = 11;
const int Y7_GPIO_NUM = 12;
const int Y6_GPIO_NUM = 14;
const int Y5_GPIO_NUM = 16;
const int Y4_GPIO_NUM = 18;
const int Y3_GPIO_NUM = 17;
const int Y2_GPIO_NUM = 15;
const int VSYNC_GPIO_NUM = 38;
const int HREF_GPIO_NUM = 47;
const int PCLK_GPIO_NUM = 13;

enum LedState { LED_IDLE, LED_WAITING, LED_COMING, LED_RESOLVED };

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
volatile LedState ledState = LED_IDLE;  // Written by the poll task, read by loop()
SemaphoreHandle_t netLock;  // One TLS session at a time, so the upload has the memory
bool cameraReady = false;
uint32_t shownColor = 0;
bool ringDrawn = false;
unsigned long redUntil = 0, lastEdge = 0, lastWifiAttempt = 0;
int stableButton = HIGH, lastRead = HIGH;
bool wasOnline = false;

void setRing(uint32_t color) {
  if (ringDrawn && color == shownColor) return;
  ring.fill(color);
  ring.show();
  shownColor = color;
  ringDrawn = true;
}

void updateRing() {
  if ((long)(redUntil - millis()) > 0) {
    setRing(ring.Color(255, 0, 0));
    return;
  }
  switch (ledState) {
    case LED_WAITING: setRing(ring.Color(255, 120, 0)); break;
    case LED_COMING: setRing(ring.Color(0, 0, 255)); break;
    case LED_RESOLVED: setRing(ring.Color(0, 255, 0)); break;
    default: setRing(0); break;
  }
}

bool startCamera() {
  if (!psramFound()) {
    Serial.println("No PSRAM found. Set Tools > PSRAM to OPI PSRAM.");
    return false;
  }
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_UXGA;  // 1600x1200
  config.jpeg_quality = 10;  // Lower is better; much lower can overflow the frame buffer
  // One buffer, refilled only when empty: the frame waiting there is always stale,
  // so discarding exactly one gives a fresh picture.
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

bool openRequest(HTTPClient &http, WiFiClientSecure &secure, WiFiClient &plain,
                 const String &url, uint16_t timeoutMs) {
  bool isHttps = url.startsWith("https");
  if (isHttps) {
    // Demo only: skips TLS certificate checks. Pin a root CA before real use.
    secure.setInsecure();
    secure.setHandshakeTimeout(TLS_HANDSHAKE_S);
  }
  bool opened = isHttps ? http.begin(secure, url) : http.begin(plain, url);
  if (!opened) return false;
  http.setReuse(false);
  http.setConnectTimeout(CONNECT_TIMEOUT_MS);
  http.setTimeout(timeoutMs);
  return true;
}

// Blocks for the whole request. Only the upload path calls this from loop().
bool postFrame(const uint8_t *data, size_t length) {
  if (xSemaphoreTake(netLock, pdMS_TO_TICKS(UPLOAD_LOCK_MS)) != pdTRUE) return false;
  bool ok = false;
  {
    WiFiClientSecure secure;
    WiFiClient plain;
    HTTPClient http;
    String url = String(SERVER_URL) + "/api/capture?student_id=" + STUDENT_ID +
                 "&problem_id=" + PROBLEM_ID + "&device_id=" + DEVICE_ID;
    if (openRequest(http, secure, plain, url, UPLOAD_TIMEOUT_MS)) {
      http.addHeader("Content-Type", "image/jpeg");
      if (DEVICE_TOKEN[0] != '\0') http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
      int code = http.POST(const_cast<uint8_t *>(data), length);
      ok = code >= 200 && code < 300;
      Serial.printf("Upload of %u bytes: %d\n", (unsigned)length, code);
      if (code < 0) Serial.println(HTTPClient::errorToString(code));
      http.end();
    }
  }
  xSemaphoreGive(netLock);
  return ok;
}

bool captureAndUpload() {
  if (!cameraReady || WiFi.status() != WL_CONNECTED) return false;

  setRing(ring.Color(255, 255, 255));
  camera_fb_t *stale = esp_camera_fb_get();
  if (stale) esp_camera_fb_return(stale);
  camera_fb_t *frame = esp_camera_fb_get();
  updateRing();
  if (!frame) {
    Serial.println("Capture failed");
    return false;
  }

  // No early return between here and the framebuffer release.
  bool ok = frame->format == PIXFORMAT_JPEG && postFrame(frame->buf, frame->len);
  esp_camera_fb_return(frame);
  return ok;
}

bool parseLed(const String &body, LedState &out) {
  int key = body.indexOf("\"led\"");
  if (key < 0) return false;
  int colon = body.indexOf(':', key);
  if (colon < 0) return false;
  int open = body.indexOf('"', colon + 1);
  if (open < 0) return false;
  int close = body.indexOf('"', open + 1);
  if (close < 0) return false;

  String value = body.substring(open + 1, close);
  if (value == "idle") out = LED_IDLE;
  else if (value == "waiting") out = LED_WAITING;
  else if (value == "coming") out = LED_COMING;
  else if (value == "resolved") out = LED_RESOLVED;
  else return false;
  return true;
}

void pollOnce() {
  // Skip this round rather than queue behind an upload.
  if (xSemaphoreTake(netLock, 0) != pdTRUE) return;
  {
    WiFiClientSecure secure;
    WiFiClient plain;
    HTTPClient http;
    String url = String(SERVER_URL) + "/api/device/" + DEVICE_ID + "?poll=true";
    if (openRequest(http, secure, plain, url, POLL_TIMEOUT_MS)) {
      int code = http.GET();
      if (code >= 200 && code < 300) {
        LedState next;
        if (parseLed(http.getString(), next)) ledState = next;
      }
      http.end();
    }
  }
  xSemaphoreGive(netLock);
}

// HTTPClient blocks through the TLS handshake, so polling runs in its own task
// to keep loop() free for the button, the ring and the Wi-Fi check.
void pollTask(void *) {
  for (;;) {
    unsigned long started = millis();
    if (WiFi.status() == WL_CONNECTED) pollOnce();
    unsigned long elapsed = millis() - started;
    vTaskDelay(pdMS_TO_TICKS(elapsed < POLL_MS ? POLL_MS - elapsed : 10));
  }
}

void maintainWifi() {
  bool online = WiFi.status() == WL_CONNECTED;
  if (online != wasOnline) {
    wasOnline = online;
    if (online) Serial.println(WiFi.localIP());
    else Serial.println("Wi-Fi down");
  }
  if (online || millis() - lastWifiAttempt < WIFI_RETRY_MS) return;
  lastWifiAttempt = millis();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void checkButton() {
  unsigned long now = millis();
  int reading = digitalRead(BUTTON_PIN);
  if (reading != lastRead) { lastEdge = now; lastRead = reading; }
  if (now - lastEdge > DEBOUNCE_MS && reading != stableButton) {
    stableButton = reading;
    if (stableButton == LOW && !captureAndUpload()) {
      Serial.println("Capture or upload failed");
      redUntil = millis() + RED_MS;
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  ring.begin();
  ring.setBrightness(RING_BRIGHTNESS);
  setRing(0);

  cameraReady = startCamera();
  if (!cameraReady) Serial.println("Camera unavailable; button presses will show red.");

  // Single reconnect path: maintainWifi() retries, so the core's own retry stays off.
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiAttempt = millis();

  netLock = xSemaphoreCreateMutex();
  // Core 0, alongside the Wi-Fi stack; loop() stays on core 1.
  xTaskCreatePinnedToCore(pollTask, "poll", 12288, nullptr, 1, nullptr, 0);
}

void loop() {
  maintainWifi();
  checkButton();
  updateRing();
  delay(5);
}
