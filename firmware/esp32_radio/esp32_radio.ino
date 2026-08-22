/*
 * Радиомост телефон <-> Teensy для перехватчика дальнего света.
 *
 * ESP32 здесь только радио: он не трогает CAN и ничего не решает. Всё, что
 * приходит с телефона, уходит в UART к Teensy, и наоборот. Так сделано потому,
 * что у ESP32 один контроллер TWAI, а перехвату нужны две независимые шины -
 * одна к подрулевому, другая к BSI. CAN остаётся на Teensy, радио здесь.
 *
 * Две радиосвязи одновременно, чтобы не зависеть от платформы телефона:
 *   Bluetooth Classic SPP - Android, любой терминал вроде Serial Bluetooth
 *                           Terminal, там есть кнопки-макросы
 *   BLE UART (Nordic UUID) - iOS, где SPP без MFi недоступен
 * Оба пишут в один и тот же UART. Именно за это взят WROOM-32: у C3 и S3
 * Bluetooth Classic нет вовсе, и путь для Android пропал бы.
 *
 * Провода: Serial2 (GPIO16 RX, GPIO17 TX) на Teensy Serial2 (пины 7 TX, 8 RX),
 * крест-накрест. Оба модуля на 3.3 В, согласование уровней не нужно. Питание
 * ESP32 - с преобразователя 5 В на его вход VIN, а НЕ с вывода 3.3 В Teensy:
 * при включении радио ток скачет, и линейного стабилизатора Teensy не хватит.
 *
 * Команды - по одному знаку, чтобы вешались на кнопку-макрос:
 *   R - форсировать дальний   r - отпустить
 *   H - пульс (нужен, пока форсирование включено, иначе само отвалится)
 *   S - состояние             L - поток кадров шины включить/выключить
 * Всё это просто пересылается в Teensy, разбирает их он.
 */

#include <BluetoothSerial.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Nordic UART Service: его понимают все готовые BLE-терминалы,
// поэтому своё приложение писать не нужно.
#define NUS_SERVICE "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_RX      "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  // телефон -> нам
#define NUS_TX      "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  // нам -> телефону

static const char *NAME = "C4-Beam";
static const int   PIN_RX = 16;   // от Teensy
static const int   PIN_TX = 17;   // к Teensy

BluetoothSerial spp;
BLECharacteristic *bleTx = nullptr;
volatile bool bleConnected = false;

// Всё, что пришло по любому каналу, уходит в Teensy без разбора: единственный,
// кто знает смысл команд, - прошивка Teensy, и дублировать её знание тут нельзя,
// иначе они разъедутся.
static void toTeensy(const uint8_t *p, size_t n) {
  Serial2.write(p, n);
}

class RxCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    std::string v = c->getValue();
    if (!v.empty()) toTeensy((const uint8_t *)v.data(), v.size());
  }
};

class SrvCb : public BLEServerCallbacks {
  void onConnect(BLEServer *) override { bleConnected = true; }
  void onDisconnect(BLEServer *s) override {
    bleConnected = false;
    // Без повторного анонса телефон больше не найдёт модуль после разрыва.
    s->getAdvertising()->start();
  }
};

void setup() {
  Serial.begin(115200);                      // отладка через USB
  Serial2.begin(115200, SERIAL_8N1, PIN_RX, PIN_TX);

  spp.begin(NAME);                           // Bluetooth Classic, Android

  BLEDevice::init(NAME);                     // BLE, iOS
  BLEServer *srv = BLEDevice::createServer();
  srv->setCallbacks(new SrvCb());
  BLEService *svc = srv->createService(NUS_SERVICE);
  bleTx = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  bleTx->addDescriptor(new BLE2902());
  BLECharacteristic *rx = svc->createCharacteristic(
      NUS_RX, BLECharacteristic::PROPERTY_WRITE);
  rx->setCallbacks(new RxCb());
  svc->start();
  BLEDevice::getAdvertising()->addServiceUUID(NUS_SERVICE);
  BLEDevice::getAdvertising()->start();

  Serial.println("радио поднято: SPP + BLE");
}

void loop() {
  // телефон -> Teensy
  while (spp.available()) {
    uint8_t b = spp.read();
    toTeensy(&b, 1);
  }
  while (Serial.available()) {               // и с USB, удобно на столе
    uint8_t b = Serial.read();
    toTeensy(&b, 1);
  }

  // Teensy -> телефон. Строкой, а не побайтно: BLE-уведомление на каждый байт
  // упирается в интервал соединения и поток кадров шины начинает отставать.
  static char buf[192];
  static size_t n = 0;
  while (Serial2.available()) {
    char c = Serial2.read();
    if (n < sizeof(buf) - 1) buf[n++] = c;
    if (c == '\n' || n >= sizeof(buf) - 1) {
      buf[n] = 0;
      if (spp.hasClient()) spp.write((uint8_t *)buf, n);
      if (bleConnected && bleTx) {
        bleTx->setValue((uint8_t *)buf, n);
        bleTx->notify();
      }
      Serial.write(buf, n);
      n = 0;
    }
  }
}
