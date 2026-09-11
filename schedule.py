"""Расписание вылазок службы: что читать, как часто, из какого блока.

Задача - за 30-минутную поездку успеть прочитать всё, но важное читать часто.
Одна вылазка (выход с BSI, вход в блок, чтение, возврат) стоит 2-4 с, в это
время поток BSI 2 Гц стоит. Зазор между вылазками MIN_GAP держит эту дыру
в разумных рамках (~10-15 % времени), а интервалы задач делят этот бюджет.

У KWP-блоков (двигатель, ABS, ГУР) один запрос отдаёт целую запись, поэтому
"горячий" набор двигателя подобран так, чтобы укладываться в ДВА запроса:
21C08001 (температура, обороты, лямбды, коррекция, адсорбер, питание, впрыск)
и 21CB8001 (вентилятор). Полный снимок двигателя - 14 запросов - раз в 5 минут.
UDS-блоки маленькие (10-40 параметров), их читаем целиком.

Задача = блок + набор имён (None - весь каталог) + интервал в секундах.
Планировщик в drive.py на каждом такте берёт самую просроченную задачу.
За поездку в 30 минут: двигатель горячий 20 раз, ABS и ГУР по 10, полный
двигатель, блок реле, подрулевой и щиток по 6, редкие блоки по 2.

UDS-блоки читаются пачками по 10 DID; отвергнутая пачка перечитывается по одному
(poll_all.poll_ecu), иначе один лишний DID скрывает девять рабочих - так блок реле
отдавал 12 параметров вместо 69, а модуль двери 1 вместо 11.
"""

MIN_GAP = 20.0      # секунд между любыми двумя вылазками

ENGINE_HOT = (
    "MP_TEMPERATURE_D_EAU_MOTEUR_d",              # ОЖ - следим за новым ДТОЖ
    "MP_REGIME_MOTEUR",
    "MP_TENSION_ALIMENTATION_CALCULATEUR_CONTROLE_MOTEUR",
    "MP_TENSION_SONDE_A_OXYGENE_AMONT",           # лямбды - без катализатора обе живые
    "MP_TENSION_SONDE_A_OXYGENE_AVAL",
    "MP_ETAT_REGULATION_SONDE_A_OXYGENE_AMONT",
    "MP_FACTEUR_CORRECTION_RICHESSE_AMONT",       # топливная коррекция
    "MP_RCOAVAL",
    "MP_CHARGE_ESTIMEE_CANISTER",                 # адсорбер: насыщение и продувка
    "MP_CDERCOELECPURGE",
    "MP_TEMPS_INJECTION_CYLINDRE_01",
    "MP_CONSIGNE_VITESSE_GMV_C5",                 # вентилятор: задание и реле
    "MP_ETAT_RELAIS_GMV",
)

SCHEDULE = [
    dict(addr=0x6A8, every=90,  names=ENGINE_HOT, label="двигатель, горячие"),
    dict(addr=0x6A8, every=300, names=None,       label="двигатель, полный"),
    dict(addr=0x6AD, every=180, names=None,       label="ABS/ESP"),
    dict(addr=0x6B5, every=180, names=None,       label="электронасос ГУР"),
    dict(addr=0x747, every=300, names=None,       label="блок реле"),
    dict(addr=0x742, every=300, names=None,       label="подрулевой"),
    dict(addr=0x75F, every=300, names=None,       label="щиток приборов"),
    dict(addr=0x744, every=900, names=None,       label="подушки"),
    dict(addr=0x75D, every=900, names=None,       label="парктроник"),
    dict(addr=0x730, every=900, names=None,       label="датчик дождя и света"),
    dict(addr=0x765, every=900, names=None,       label="дисплей"),
    dict(addr=0x77B, every=900, names=None,       label="панель управления"),
    dict(addr=0x731, every=900, names=None,       label="модуль двери"),
]
