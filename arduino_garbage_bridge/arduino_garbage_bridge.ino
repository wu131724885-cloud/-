// 橋接程式：接收 Python 命令後切換 LED 狀態，作為分類控制示範。
// 三個 LED 分別代表可回收、水果類與待機狀態。
const int recyclableLedPin = 8;
const int fruitLedPin = 9;
const int idleLedPin = LED_BUILTIN;

// 暫存從序列埠收到的一整行命令。
String command = "";

void setLeds(bool recyclableOn, bool fruitOn, bool idleOn) {
  // 統一控制所有 LED，避免在不同分支重複寫 digitalWrite。
  digitalWrite(recyclableLedPin, recyclableOn ? HIGH : LOW);
  digitalWrite(fruitLedPin, fruitOn ? HIGH : LOW);
  digitalWrite(idleLedPin, idleOn ? HIGH : LOW);
}

void handleCommand(String value) {
  // 先整理字串格式，避免換行或大小寫造成比對失敗。
  value.trim();
  value.toUpperCase();

  // 可回收類命令。
  if (value == "BOTTLE" || value == "CUP" || value == "WINE_GLASS") {
    setLeds(true, false, false);
    Serial.println("ACK: recyclable");
    return;
  }

  // 水果類命令。
  if (value == "BANANA" || value == "APPLE" || value == "ORANGE") {
    setLeds(false, true, false);
    Serial.println("ACK: fruit");
    return;
  }

  // 其他情況包含 NONE，都回到待機狀態。
  setLeds(false, false, true);
  Serial.println("ACK: idle");
}

void setup() {
  // 初始化輸出腳位。
  pinMode(recyclableLedPin, OUTPUT);
  pinMode(fruitLedPin, OUTPUT);
  pinMode(idleLedPin, OUTPUT);

  // 開機先進入待機狀態。
  setLeds(false, false, true);

  // 啟動與 Python 相同鮑率的序列通訊。
  Serial.begin(9600);
  while (!Serial) {
  }

  Serial.println("Arduino bridge ready");
}

void loop() {
  // 持續讀取序列資料，直到拼出一整行命令為止。
  while (Serial.available() > 0) {
    char incoming = Serial.read();

    if (incoming == '\n') {
      // 收到換行代表命令結束，立即處理並清空緩衝。
      handleCommand(command);
      command = "";
      continue;
    }

    if (incoming != '\r') {
      // Windows 常見的 CRLF 只保留真正的字元內容。
      command += incoming;
    }
  }
}