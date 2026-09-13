"""
Перевод описаний кодов неисправностей с французского на русский.

Описания в базе DiagBox формульные: 370 описаний собраны примерно из полусотни
терминов ("défaut de cohérence du signal ...", "court-circuit au +"). Поэтому
переводим не фразы целиком, а термины, длинные раньше коротких. Что не нашлось,
остаётся французским и видно в отчёте - лучше честная дырка, чем выдумка.

    ./.venv/bin/python dtc_tr.py          # собрать dtc_names_ru.py
    ./.venv/bin/python dtc_tr.py --left   # показать непереведённые слова
"""

import re
import sys

# Длинные выражения ПЕРВЫМИ: "court-circuit au +" должен сработать раньше, чем
# отдельные "court-circuit" и "au".
PHRASES = [
    ("court-circuit au + après consommateur", "замыкание на плюс после потребителя"),
    ("court-circuit à la masse", "замыкание на массу"),
    ("court-circuit au +", "замыкание на плюс"),
    ("circuit ouvert ou court-circuit au +", "обрыв или замыкание на плюс"),
    ("circuit ouvert", "обрыв цепи"),
    ("coussin gonflable", "подушка безопасности"),
    ("température eau moteur", "температуры охлаждающей жидкости"),
    ("eau moteur", "охлаждающей жидкости"),
    ("sonde à oxygène amont", "верхнего датчика кислорода"),
    ("sonde à oxygène aval", "нижнего датчика кислорода"),
    ("sonde à oxygène", "датчика кислорода"),
    ("pédale d'accélérateur", "педали акселератора"),
    ("pédale de frein", "педали тормоза"),
    ("papillon motorisé", "электропривода дроссельной заслонки"),
    ("boîtier papillon", "дроссельного узла"),
    ("prétensionneur de ceinture", "преднатяжителя ремня"),
    ("calculateur moteur", "блока управления двигателем"),
    ("réseau multiplexé", "мультиплексной шины"),
    ("mise à la masse", "замыкание на массу"),
    ("capteur de cliquetis", "датчика детонации"),
    ("débitmètre d'air", "расходомера воздуха"),
    ("vanne de purge", "клапана продувки"),
    ("groupe motoventilateur", "вентилятора охлаждения"),
    ("boîte de vitesses", "коробки передач"),
    ("colonne de direction", "рулевой колонки"),
    ("frein de parking", "стояночного тормоза"),
    ("niveau de carburant", "уровня топлива"),
    ("rapport cyclique", "скважность"),
    ("valeur hors limites", "значение вне допустимого диапазона"),
    ("hors limites", "вне допустимого диапазона"),
    ("non conforme", "не соответствует норме"),
    ("absence de signal", "нет сигнала"),
    ("pas de communication", "нет связи"),
    ("défaut de cohérence", "недостоверные показания"),
    ("défaut électrique", "электрическая неисправность"),
    ("défaut interne", "внутренняя неисправность"),
]

WORDS = {
    "défaut": "неисправность", "signal": "сигнал", "calculateur": "блок управления",
    "ligne": "цепь", "moteur": "двигателя", "circuit": "цепь", "capteur": "датчика",
    "cohérence": "недостоверные показания", "commande": "команда", "latéral": "боковой",
    "satellite": "выносного датчика", "régulation": "регулирование", "véhicule": "автомобиля",
    "vitesse": "скорости", "information": "сигнал", "gauche": "левый", "droit": "правый",
    "droite": "правая", "papillon": "дроссельной заслонки", "ouvert": "разомкнута",
    "sonde": "датчика", "masse": "масса", "passager": "пассажира", "avant": "передний",
    "arrière": "задний", "oxygène": "кислорода", "couple": "момента", "conducteur": "водителя",
    "niveau": "уровня", "prétensionneur": "преднатяжителя", "secondaire": "вторичный",
    "mise": "включение", "feu": "фонаря", "l'allumeur": "воспламенителя",
    "allumeur": "воспламенителя", "motorisé": "с электроприводом", "plus": "плюс",
    "température": "температуры", "pression": "давления", "admission": "впуска",
    "injection": "впрыска", "allumage": "зажигания", "ralenti": "холостого хода",
    "carburant": "топлива", "huile": "масла", "batterie": "аккумулятора",
    "alimentation": "питания", "tension": "напряжения", "courant": "тока",
    "position": "положения", "rupture": "обрыв", "blocage": "заклинивание",
    "bloquée": "заблокирована", "bloqué": "заблокирован", "dysfonctionnement": "неисправность",
    "détection": "обнаружение", "amont": "верхнего", "aval": "нижнего",
    "chauffage": "подогрева", "relais": "реле", "électrovanne": "электроклапана",
    "pompe": "насоса", "ventilateur": "вентилятора", "embrayage": "сцепления",
    "roue": "колеса", "frein": "тормоза", "direction": "рулевого управления",
    "volant": "руля", "airbag": "подушки безопасности", "ceinture": "ремня",
    "porte": "двери", "coffre": "багажника", "phare": "фары", "clignotant": "указателя поворота",
    "essuie-vitre": "стеклоочистителя", "klaxon": "звукового сигнала",
    "interne": "внутренняя", "externe": "внешняя", "absence": "отсутствие",
    "présence": "наличие", "invalide": "недостоверно", "valide": "достоверно",
    "incohérent": "противоречивый", "défectueux": "неисправен", "coupé": "отключён",
    "central": "центральный", "conforme": "соответствует", "limite": "предел",
    "seuil": "порог", "maximum": "максимум", "minimum": "минимум", "surchauffe": "перегрев",
    "court-circuit": "замыкание", "alternateur": "генератора", "démarreur": "стартера",
    "réseau": "шины", "bus": "шины", "communication": "связь", "mémoire": "памяти",
    "configuration": "конфигурации", "codage": "кодирования", "apprentissage": "обучения",
    "cliquetis": "детонации", "richesse": "состава смеси", "catalyseur": "катализатора",
    "échappement": "выпуска", "turbo": "турбины", "soupape": "клапана", "arbre": "вала",
    "cames": "распредвала", "vilebrequin": "коленвала", "régime": "оборотов",
    "réveil": "пробуждения", "injecteur": "форсунки", "ratés": "пропуски воспламенения",
    "cylindre": "цилиндра", "rideau": "шторки безопасности", "combustion": "сгорания",
    "limitation": "ограничения", "lvv": "ограничителя скорости", "trop": "слишком",
    "valeur": "значение", "neutralisation": "отключения", "frontal": "фронтальная",
    "rvv": "круиз-контроля", "défaillance": "отказ", "surtension": "перенапряжение",
    "sous-tension": "пониженное напряжение", "connecteur": "разъёма", "faisceau": "жгута",
    "actionneur": "исполнительного механизма", "thermostat": "термостата",
    "refroidissement": "охлаждения", "climatisation": "кондиционера", "compresseur": "компрессора",
    "habitacle": "салона", "antidémarrage": "иммобилайзера", "antivol": "противоугонной системы",
    "télécommande": "брелока", "serrure": "замка", "vitre": "стекла", "siège": "сиденья",
    "occupant": "пассажира", "choc": "удара", "impact": "удара", "déclenchement": "срабатывание",
    "défaut": "неисправность", "incorrect": "неверный", "incorrecte": "неверная",
    "manquant": "отсутствует", "trop faible": "слишком низкое", "trop élevé": "слишком высокое",
    "faible": "низкое", "élevé": "высокое", "élevée": "высокая", "permanent": "постоянный",
    "intermittent": "периодический", "gonflable": "безопасности", "coussin": "подушки",
    "rapport": "отношение", "boîtier": "блока", "unité": "блока", "module": "модуля",
    "contacteur": "выключателя", "commutateur": "переключателя", "voyant": "лампы",
    "indicateur": "указателя", "afficheur": "дисплея", "combiné": "щитка приборов",
    "planche": "панели", "bord": "приборов", "essieu": "оси", "roues": "колёс",
    "avant-droit": "переднее правое", "avant-gauche": "переднее левое",
    "consigne": "задания", "mesure": "измерение", "mesuré": "измеренное",
}

SMALL = {"de", "du", "la", "le", "les", "des", "au", "aux", "à", "et", "ou", "en",
         "un", "une", "d'un", "d'une", "l'", "par", "sur", "pour", "dans", "avec", "n"}


def translate(fr: str):
    """Возвращает (перевод, список непереведённых слов)."""
    s = fr.lower()
    for a, b in sorted(PHRASES, key=lambda x: -len(x[0])):
        s = s.replace(a, "\x00" + b + "\x00")
    out, left = [], []
    for tok in re.split(r"(\x00[^\x00]*\x00|[^\w\x00àâçéèêëîïôöùûü'-]+)", s):
        if not tok:
            continue
        if tok.startswith("\x00"):
            out.append(tok.strip("\x00")); continue
        if not re.match(r"[\wàâçéèêëîïôöùûü']", tok) or "\x00" in tok:
            out.append(tok); continue
        w = tok.strip("'")
        if w in SMALL:
            continue
        if w in WORDS:
            out.append(WORDS[w])
        elif re.fullmatch(r"[\d.,%°+\-]+", w):
            out.append(w)
        else:
            out.append(w); left.append(w)
    t = re.sub(r"\s+", " ", " ".join(out)).strip(" ,;")
    return (t[:1].upper() + t[1:]) if t else fr, left


def main():
    import collections
    exec(open("dtc_fr.py").read(), globals())
    left = collections.Counter()
    res = {}
    for code, fr in sorted(DTC_FR.items()):
        ru, miss = translate(fr)
        left.update(miss)
        res[code] = ru
    if "--left" in sys.argv:
        print(f"непереведённых слов: {len(left)}, всего вхождений {sum(left.values())}",
              file=sys.stderr)
        for w, n in left.most_common(30):
            print(f"  {w:24} {n}")
        return 0
    print('"""Описания кодов на русском. Сгенерировано dtc_tr.py из dtc_fr.py."""')
    print("\nDTC_RU = {")
    for k in sorted(res):
        print(f"    {k!r}: {res[k]!r},")
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
