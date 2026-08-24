/*
 * Перехватчик кадра подрулевого переключателя. Teensy 4.0, FlexCAN_T4.
 *
 * Ровно та логика, что проверена на ноутбуке в mitm_logic.py и mitm_sim.py, плюс
 * то, чего в модели нет: собственно мост. Модель решала только судьбу кадра
 * 0x094; здесь важно, что разрыв рвёт весь сегмент, поэтому в разомкнутом
 * состоянии через нас должно ходить ВСЁ, в обе стороны, и только у 0x094
 * поправляется бит. Иначе BSI перестанет слышать подрулевой целиком.
 *
 * Четыре правила из модели, повторены здесь буквально:
 *   1. Пока реле замкнуто - не передавать. Оба трансивера тогда на одном
 *      сегменте, и передача есть столкновение с самим собой.
 *   2. Рычаг главнее: любое изменение положения снимает форсирование сразу.
 *   3. Нет пульса с телефона - форсирование само отваливается.
 *   4. По умолчанию прозрачно: реле замкнуто, машина электрически стоковая.
 *
 * Провода:
 *   CAN1 (пины 22 TX, 23 RX) - трансивер в сторону ПОДРУЛЕВОГО
 *   CAN2 (пины  0 TX,  1 RX) - трансивер в сторону BSI
 *   Serial2 (пины 7 TX, 8 RX) - ESP32. Именно Serial2, а не Serial1:
 *       Serial1 сидит на пинах 0 и 1, то есть на CAN2, и они бы столкнулись.
 *   пин 2 - управление реле, высокий уровень размыкает (см. ниже)
 *
 * Реле включено НОРМАЛЬНО ЗАМКНУТЫМ через разрыв: без питания и без исправной
 * прошивки половины шины просто соединены, и машина ведёт себя как без врезки.
 * Модуль ставится в режим срабатывания по высокому уровню, на входе подтяжка
 * 10 кОм к массе - тогда "не управляем" и "загружаемся" однозначно значат
 * замкнуто. Проверять модуль ДО подключения: у 12-вольтовых плат на входе в
 * режиме низкого уровня стоит почти напряжение питания, а Teensy 4.0 не терпит
 * даже 5 В.
 *
 * Битовая карта НЕ проверена на этой машине - взята из описания COM2008P.
 * Поэтому и номер кадра, и номер бита меняются командой с телефона: пока не
 * снят настоящий обмен, это гипотеза, а не константа.
 */

#include <FlexCAN_T4.h>
#include <Watchdog_t4.h>

FlexCAN_T4<CAN1, RX_SIZE_256, TX_SIZE_16> stalkBus;   // сторона подрулевого
FlexCAN_T4<CAN2, RX_SIZE_256, TX_SIZE_16> bsiBus;     // сторона BSI

// Сторожевой таймер. Нашлось на наглядной модели, и это была настоящая дыра.
//
// Реле нормально замкнутое, и защита строилась на том, что при отказе вывод
// перестанет удерживать высокий уровень. Для пропажи питания это верно. А вот
// зависшая прошивка вывод НЕ отпускает: он остаётся в высоком уровне, обмотки
// под током, шина разорвана - и водитель на ходу теряет поворотники и
// стеклоочистители, ровно то, чего вся конструкция должна избегать.
//
// Сторожевой таймер это закрывает: если loop() перестал его кормить, процессор
// перезапускается. На время перезапуска вывод становится входом, и подтяжка
// 10 кОм к массе гасит обмотки - половины шины соединяются. То есть подтяжка
// здесь не подстраховка, а часть защиты.
//
// Два с половиной - с запасом: обычный проход loop() занимает микросекунды.
static const float WDT_SECONDS = 2.5f;   // float: в uint32_t 2.5 обрезалось бы до 2
WDT_T4<WDT1> wdt;

static const int PIN_RELAY = 2;
static const uint32_t BITRATE = 125000;      // шина комфорта AEE2010; проверить замером
static const uint32_t BEAT_TIMEOUT_MS = 3000;

// Гипотезы, которые уточняются замером на шине.
uint32_t stalkId = 0x094;
uint8_t  beamBit = 4;

bool force = false;          // просят форсировать дальний
bool relayOpen = false;      // разомкнуто ли реле, то есть сидим ли в разрыве
uint32_t lastBeat = 0;
int stalkBit = -1;           // что рычаг просил в прошлом кадре, -1 - ещё не знаем
bool trace = false;          // поток кадров в телефон

static void relay(bool open) {
  relayOpen = open;
  digitalWrite(PIN_RELAY, open ? HIGH : LOW);
}

static void say(const char *s) {
  Serial2.println(s);
  Serial.println(s);
}

static void drop(const char *why) {
  force = false;
  relay(false);
  Serial2.print("отпущено: "); Serial2.println(why);
  Serial.print("отпущено: ");  Serial.println(why);
}

// Пересылка кадра на другую сторону. Только когда мы в разрыве: правило 1.
static void forward(const CAN_message_t &in, bool toBsi) {
  CAN_message_t out = in;
  if (toBsi && in.id == stalkId && force && in.len > 0) {
    out.buf[0] |= (1 << beamBit);
  }
  if (toBsi) bsiBus.write(out);
  else       stalkBus.write(out);
}

static void onStalkFrame(const CAN_message_t &msg) {
  if (msg.id == stalkId && msg.len > 0) {
    int asked = (msg.buf[0] & (1 << beamBit)) ? 1 : 0;
    if (stalkBit < 0) {
      stalkBit = asked;
    } else if (asked != stalkBit) {
      // Правило 2: водитель тронул рычаг. Снимаем форсирование ДО того, как
      // этот кадр уйдёт дальше, чтобы наружу ушло уже его настоящее желание.
      stalkBit = asked;
      if (force) drop("рычаг перебил");
    }
  }
  if (trace) {
    char b[64];
    snprintf(b, sizeof(b), "S %03lX %02X%02X%02X%02X%02X%02X%02X%02X",
             (unsigned long)msg.id, msg.buf[0], msg.buf[1], msg.buf[2], msg.buf[3],
             msg.buf[4], msg.buf[5], msg.buf[6], msg.buf[7]);
    Serial2.println(b);
  }
  if (relayOpen) forward(msg, true);
}

static void status() {
  char b[128];
  snprintf(b, sizeof(b),
           "форс=%d реле=%s рычаг=%d кадр=%03lX бит=%u пульс=%lu мс назад",
           (int)force, relayOpen ? "разомкнуто" : "замкнуто", stalkBit,
           (unsigned long)stalkId, beamBit,
           (unsigned long)(force ? millis() - lastBeat : 0));
  Serial2.println(b);
  Serial.println(b);
}

// Числовой аргумент команды: "I094" - кадр, "B4" - бит. Пока карта не проверена
// на машине, менять их надо с телефона, а не перепрошивкой.
static uint32_t arg(char *s, int base) {
  return strtoul(s + 1, nullptr, base);
}

static void handle(char *line) {
  switch (line[0]) {
    case 'R': force = true;  lastBeat = millis(); relay(true);
              say("форсирую дальний"); break;
    case 'r': drop("команда"); break;
    case 'H': if (force) lastBeat = millis(); break;
    case 'S': status(); break;
    case 'L': trace = !trace; say(trace ? "поток включён" : "поток выключен"); break;
    case 'I': stalkId = arg(line, 16); status(); break;
    case 'B': beamBit = (uint8_t)arg(line, 10); status(); break;
    default: break;
  }
}

void setup() {
  pinMode(PIN_RELAY, OUTPUT);
  relay(false);                 // правило 4: с самого начала прозрачно

  Serial.begin(115200);
  Serial2.begin(115200);

  stalkBus.begin(); stalkBus.setBaudRate(BITRATE);
  bsiBus.begin();   bsiBus.setBaudRate(BITRATE);
  // Пока реле замкнуто, оба трансивера на одном сегменте: слушаем и молчим.
  stalkBus.enableFIFO();
  bsiBus.enableFIFO();

  WDT_timings_t cfg;
  cfg.timeout = WDT_SECONDS;
  wdt.begin(cfg);

  say("перехватчик готов, режим прозрачный");
}

void loop() {
  // Кормить сторожевой таймер первым делом: пока loop() жив, перезапуска нет.
  wdt.feed();

  CAN_message_t msg;
  while (stalkBus.read(msg)) onStalkFrame(msg);
  // Обратное направление: в разрыве BSI должна доходить до подрулевого, иначе он
  // остаётся без подтверждений на шине и уходит в пассивное состояние по ошибкам.
  while (bsiBus.read(msg)) { if (relayOpen) forward(msg, false); }

  // Правило 3: пропал пульс - отпускаем.
  if (force && millis() - lastBeat > BEAT_TIMEOUT_MS) drop("пульс пропал");

  static char line[32];
  static size_t n = 0;
  while (Serial2.available() || Serial.available()) {
    char c = Serial2.available() ? Serial2.read() : Serial.read();
    if (c == '\r' || c == '\n') {
      if (n) { line[n] = 0; handle(line); n = 0; }
    } else if (n < sizeof(line) - 1) {
      line[n++] = c;
      // Одиночные команды без перевода строки: кнопка-макрос в терминале
      // часто отправляет ровно один знак и всё.
      if (n == 1 && strchr("RrHSL", c)) { line[1] = 0; handle(line); n = 0; }
    }
  }
}
