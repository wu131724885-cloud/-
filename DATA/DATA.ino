#include <Servo.h>

// 展示程式：讀取四組超音波距離，做基本避障並同步掃描舵機。

// 定義 4 組感測器 (Trig, Echo)
const int s1[2] = {12, 11}; // 障礙左
const int s2[2] = {13, 10}; // 障礙右
const int s3[2] = {A0, A1}; // 距離 A
const int s4[2] = {A2, A3}; // 距離 B

const int servoPin = 9;
const int RPWM = 5, LPWM = 6, R_EN = 7, L_EN = 8;

Servo myservo;

void setup() {
  Serial.begin(9600);
  
  // 初始化所有感測器引腳
  int pins[] = {12, 11, 13, 10, A0, A1, A2, A3};
  for(int i=0; i<8; i+=2) {
    pinMode(pins[i], OUTPUT);   // Trig
    pinMode(pins[i+1], INPUT);  // Echo
  }

  // BTS7960 初始化
  pinMode(RPWM, OUTPUT); pinMode(LPWM, OUTPUT);
  pinMode(R_EN, OUTPUT); pinMode(L_EN, OUTPUT);
  digitalWrite(R_EN, HIGH); digitalWrite(L_EN, HIGH);

  myservo.attach(servoPin);
  Serial.println("R4 WiFi 穩定版系統啟動...");
}

int readDist(int trig, int echo) {
  // 送出 10us 的 trig 脈波觸發量測。
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  // timeout 時回傳 999，表示超出可用範圍。
  long duration = pulseIn(echo, HIGH, 25000);
  if (duration == 0) return 999;
  return duration * 0.034 / 2;
}

void loop() {
  // 依序讀 4 組感測器，並插入小延遲避免互相干擾。
  int d1 = readDist(s1[0], s1[1]); delay(10);
  int d2 = readDist(s2[0], s2[1]); delay(10);
  int d3 = readDist(s3[0], s3[1]); delay(10);
  int d4 = readDist(s4[0], s4[1]);

  Serial.print("D1-4: ");
  Serial.print(d1); Serial.print(" ");
  Serial.print(d2); Serial.print(" ");
  Serial.print(d3); Serial.print(" ");
  Serial.println(d4);

  // 簡單避障邏輯
  if (d1 < 20 || d2 < 20) {
    analogWrite(RPWM, 0); // 停止大馬達
    myservo.write(90);    // 舵機回正
  } else {
    analogWrite(RPWM, 150); // 前進
    sweepServo();
  }
  delay(30);
}

void sweepServo() {
  // 以固定步進在 0~180 度來回掃描。
  static int pos = 0, step = 5;
  pos += step;
  if (pos <= 0 || pos >= 180) step = -step;
  myservo.write(pos);
}
