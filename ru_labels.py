"""
Человекочитаемые названия параметров вместо французских мнемоник DiagBox.

`MP_COMMANDE_ECLAIRAGE_LATERAL_EXTERIEUR` читать невозможно, поэтому имена
переводятся по словарю слов, а для важных параметров заданы ручные ярлыки.
Ручной список всегда важнее автоперевода.

Префикс MP_ (mesure parametre) отбрасывается, CFG_ становится пометкой
"конфиг", служебные коды блоков (BSI, GMP, CMM) раскрываются.
"""

import re

# Ручные ярлыки для того, что смотришь чаще всего. Здесь важна краткость.
CURATED = {
    0xDBA8: "Обороты двигателя",
    0xDB61: "Скорость",
    0xDA44: "Напряжение питания BSI",
    0xDA46: "Напряжение АКБ",
    0xDA45: "Задание напряжения генератора",
    0xDA21: "Заряд АКБ",
    0xDA47: "Температура АКБ",
    0xDB82: "Температура масла",
    0xDB84: "Уровень масла (замер)",
    0xDB86: "Уровень масла (индикация)",
    0xDD18: "Положение ключа",
    0xDD03: "Состояние силового агрегата",
    0xDB60: "Положение селектора передач",
    0xD870: "Габаритные огни",
    0xD871: "Поворотник правый",
    0xD872: "Поворотник левый",
    0xD873: "Задние противотуманные",
    0xD874: "Фонари заднего хода",
    0xD875: "Стоп-сигналы",
    0xD877: "Лампа в кнопке аварийки",
    0xD826: "Автопереключение дальний/ближний доступно",
    0xDB8B: "Скорость рыскания",
    0xDC44: "Стартер",
    0xDCF2: "Число пусков двигателя",
    0xDB8D: "Пробег общий",
    0xDB9A: "Пробег поездка 1",
    0xDB9B: "Пробег поездка 2",
    0xD8C5: "Топливо в баке",
    0xD8C3: "Топливо в баке (замер)",
    0xD8C7: "Расход мгновенный",
    0xD8CA: "Расход текущий",
    0xD8E1: "Запас хода",
    0xD912: "Температура за бортом",
    0xD920: "Температура за бортом (расчёт)",
    0xDB83: "Температура масла (замер)",
    0xDA4D: "Напряжение АКБ в покое",
    0xDA00: "Напряжение BSI при пробуждении",
    0xD8C6: "Лючок топливного бака",
}

# Расшифровка кодов блоков и систем - иначе в названиях остаются аббревиатуры.
ACRONYMS = {
    "BSI": "BSI", "GMP": "силового агрегата", "CMM": "ЭБУ двигателя",
    "DAE": "ЭУР", "ABS": "ABS", "ESP": "ESP", "FAP": "сажевого фильтра",
    "BVA": "АКПП", "BVMP": "робота КПП", "CAV": "датчика угла",
    "HF": "радиоканала", "DMTR": "пуска", "MUX": "мультиплекса",
    "APC": "зажигания", "CPC": "доп. питания", "VTH": "отопителя",
    "RCD": "магнитолы", "LIN": "LIN", "CAN": "CAN", "SEV": "борт. сети",
    "ADC": "АЦП", "AAG": "автосвета", "LVV": "ограничителя",
    "RVV": "круиз-контроля", "FSE": "электроручника",
}

WORDS = {
    "ETAT": "состояние", "COMMANDE": "команда", "CMD": "команда",
    "DEMANDE": "запрос", "DMD": "запрос", "ARRET": "останов",
    "TELECOMMANDE": "пульт", "DERNIER": "последний", "DERNIERE": "последняя",
    "COMPT": "счётчик", "COMPTEUR": "счётчик", "NOMBRE": "количество",
    "NB": "количество", "INTERDIT": "запрещён", "INTERDICTION": "запрет",
    "INHIB": "запрет", "INHIBITION": "запрет", "MOT": "двигатель",
    "MOTEUR": "двигатель", "AVANT": "передний", "AV": "перед.",
    "ARRIERE": "задний", "AR": "задн.", "VITESSE": "скорость",
    "STOP": "стоп", "ECLAIRAGE": "освещение", "ECLAIREUR": "подсветка",
    "SECURITE": "безопасность", "APPUI": "нажатие", "AUTORISATION": "разрешение",
    "CARBURANT": "топливо", "DETECTION": "обнаружение", "NON": "не",
    "CLE": "ключ", "BATTERIE": "АКБ", "COND": "условие",
    "KILOMETRAGE": "пробег", "KM": "км", "MAINT": "обслуживание",
    "AFFICHAGE": "индикация", "GAUCHE": "левый", "G": "лев.",
    "DROIT": "правый", "DROITE": "правый", "D": "прав.",
    "NIVEAU": "уровень", "MENU": "меню", "BOUTON": "кнопка",
    "POSITION": "положение", "ACTIVATION": "включение", "ACTIF": "активен",
    "TEMPERATURE": "температура", "CLIMATISATION": "климат",
    "TENSION": "напряжение", "COURANT": "ток", "PUISSANCE": "мощность",
    "REGIME": "обороты", "HUILE": "масло", "EAU": "вода",
    "FEU": "огонь", "FEUX": "огни", "POSITION_ET": "габарит и",
    "ROUTE": "дальний", "CROISEMENT": "ближний", "BROUILLARD": "туман",
    "ANTIBROUILLARD": "противотуманные", "CLIGNOTANT": "поворотник",
    "CLIGNOTANTS": "поворотники", "DETRESSE": "аварийка",
    "DIURNE": "ходовые", "DIURNES": "ходовые", "RECUL": "задний ход",
    "PLAFONNIER": "плафон", "COFFRE": "багажник", "PORTE": "дверь",
    "PORTES": "двери", "OUVRANT": "открывающийся", "OUVRANTS": "проёмы",
    "SERRURE": "замок", "VERROUILLAGE": "запирание",
    "DEVERROUILLAGE": "отпирание", "ALARME": "сигнализация",
    "SIRENE": "сирена", "AVERTISSEUR": "сигнал", "SONORE": "звуковой",
    "ESSUYAGE": "стеклоочистка", "ESSUIE": "стеклоочиститель",
    "LAVAGE": "омыв", "VITRE": "стекло", "VITRES": "стёкла",
    "VITESSES": "передачи", "LEVIER": "селектор", "FREIN": "тормоз",
    "PEDALE": "педаль", "EMBRAYAGE": "сцепление", "CEINTURE": "ремень",
    "SIEGE": "сиденье", "RETROVISEUR": "зеркало",
    "RETROVISEURS": "зеркала", "CAPOT": "капот", "TOIT": "крыша",
    "VOLANT": "руль", "COLONNE": "колонка", "DIRECTION": "рулевое",
    "COMMUTATEUR": "переключатель", "CONTACT": "зажигание",
    "DEMARRAGE": "пуск", "DEMARREUR": "стартер", "REDEMARRAGE": "перезапуск",
    "REVEIL": "пробуждение", "ENDORMISSEMENT": "засыпание",
    "VEILLE": "ожидание", "DEFAUT": "неисправность", "DEF": "неисправность",
    "PRESENCE": "наличие", "ABSENCE": "отсутствие", "MESURE": "замер",
    "CALCULE": "расчётный", "CALCULATEUR": "блок", "AFFICHE": "отображаемый",
    "MEMORISE": "сохранённый", "MEMORISEE": "сохранённая",
    "CONSIGNE": "задание", "SEUIL": "порог", "DUREE": "длительность",
    "TEMPS": "время", "DELAI": "задержка", "AUTOMATIQUE": "автоматический",
    "AUTO": "авто", "MANUEL": "ручной", "TOTAL": "всего",
    "PARTIEL": "частичный", "MOYENNE": "среднее", "INSTANTANE": "мгновенный",
    "LATERAL": "боковой", "EXTERIEUR": "наружный", "INTERIEUR": "внутренний",
    "AMBIANCE": "подсветка салона", "SEUILS": "пороги",
    "PLAQUE": "номерной знак", "POLICE": "знак", "REMORQUE": "прицеп",
    "GABARIT": "габарит", "STATIQUE": "статический", "DEDIE": "выделенный",
    "SUPPLEMENTAIRE": "дополнительный", "ADDITIONNEL": "дополнительный",
    "REPETITEUR": "повторитель", "TEMOIN": "лампа", "VOYANT": "лампа",
    "IMPULSIONNELLE": "импульсный", "INVERSEUR": "инвертор",
    "COMMUTATION": "переключение", "DISPONIBILITE": "доступность",
    "FONCTION": "функция", "MODE": "режим", "TYPE": "тип",
    "CONFIG": "конфигурация", "VEHICULE": "автомобиль", "VHL": "автомобиля",
    "HABITACLE": "салон", "PARC": "стоянка", "USINE": "заводской",
    "DIAG": "диагностика", "DIAGNOSTIC": "диагностика",
    "PASSERELLE": "шлюз", "RESEAU": "сеть", "RESEAUX": "сети",
    "TRAME": "кадр", "ALIM": "питание", "ALIMENTION": "питание",
    "ALIMENTATION": "питание", "PROTECTION": "защита",
    "THERMIQUE": "тепловая", "SURCHAUFFE": "перегрев",
    "CHARGE": "заряд", "DECHARGE": "разряд", "ECO": "эконом",
    "ECONOMIE": "экономия", "ENERGIE": "энергия",
    "PREMIER": "первый", "SECOND": "второй", "PRED": "прогноз",
    "PREDITE": "прогнозная", "ESTIMEE": "оценочная",
    "INTERNE": "внутреннее", "RESISTANCE": "сопротивление",
    "REPOS": "покой", "ACC": "доп. потребители", "ACCESSOIRE": "аксессуар",
    "GRAVE": "серьёзная", "DEFAILLANCE": "отказ", "PASSAGE": "переход",
    "PROCHAIN": "следующий", "PREMAINT": "до ТО", "AUTOROUTE": "шоссе",
    "VIRAGE": "поворот", "DYN": "динамический", "LACET": "рыскание",
    "LONGITUDINALE": "продольная", "INFORMATION": "информация",
    "INFO": "инфо", "GENERAL": "общий", "PRINCIPAL": "основной",
    "SEMI": "полу", "STATIONNEMENT": "парковка",
}

# Дополнение словаря: слова, оставшиеся непереведёнными после первого прохода.
WORDS.update({
    'ABAV': 'подушка переднего пассажира',
    'ACCUEIL': 'встреча',
    'ADDITIF': 'присадка',
    'AFFICHEE': 'отображаемая',
    'ALLUMAGE': 'включение',
    'AMORTISSEUR': 'амортизатор',
    'ANNEE': 'год',
    'ANTISOULEVEMENT': 'от подъёма',
    'APPRIS': 'обучен',
    'AUS': 'выкл',
    'AUTONOMIE': 'запас хода',
    'BADGE': 'метка',
    'BATT': 'АКБ',
    'BOUCLEE': 'застёгнут',
    'BOUGIE': 'свеча',
    'BOUGIES': 'свечи',
    'BRISE': 'лобовое',
    'CAMERA': 'камера',
    'CAPTEUR': 'датчик',
    'CENTRAL': 'центральный',
    'CHANGEMENT': 'смена',
    'CHAUFFANTS': 'с подогревом',
    'CITY': 'город',
    'COMPRESSEUR': 'компрессор',
    'CONDUCTEUR': 'водитель',
    'CONDUITE': 'вождение',
    'CONSOMMATION': 'расход',
    'COULISSANT': 'сдвижной',
    'COURS': 'в процессе',
    'CREVAISON': 'прокол',
    'CUMUL': 'накопленный',
    'DEFLEC': 'дефлектор',
    'DEGAZAGE': 'продувка',
    'DEGIVRAGE': 'обогрев',
    'DERN': 'последний',
    'DESACTIVATION': 'отключение',
    'DID': 'DID',
    'DISTANCE': 'расстояние',
    'DURANT': 'в течение',
    'DURAVMAINT': 'срок до ТО',
    'ECLAIREURS': 'подсветка',
    'EFFRACTION': 'взлом',
    'ELCTRONIQUE': 'электронный',
    'ELECTRONIQUE': 'электронный',
    'ELEMENT': 'элемент',
    'ENFANT': 'детский',
    'ESCAMOTABLE': 'убирающийся',
    'EXT': 'наружн',
    'FILAIRE': 'проводной',
    'FIXE': 'постоянный',
    'FREQUENCE': 'частота',
    'GAZOLE': 'дизель',
    'GONFLAGE': 'подкачка',
    'GRV': 'серьёзная',
    'HAUTE': 'высокая',
    'HEURE': 'час',
    'ID': 'ид',
    'IHNIB': 'запрет',
    'INSTANT': 'мгновенный',
    'INT': 'внутр',
    'JAUGE': 'датчик уровня',
    'JOUR': 'день',
    'JOURNALIER': 'дневной',
    'KMS': 'км',
    'LATERAUX': 'боковые',
    'LAVAGEPROJ': 'омыватель фар',
    'LECTEUR': 'считыватель',
    'LIMITATION': 'ограничитель',
    'LIN2': 'LIN2',
    'LITRE': 'литр',
    'LS': 'LS',
    'LUMINOSITE': 'освещённость',
    'LUNETTE': 'стекло двери багажника',
    'MAE': 'вкл',
    'MAINTENANCE': 'обслуживание',
    'MANOEUVRE': 'манёвр',
    'MANUELLE': 'ручной',
    'MAX': 'макс',
    'MENUS': 'меню',
    'MIN': 'мин',
    'MINUTE': 'минута',
    'MIROIR': 'зеркало',
    'MOBILE': 'подвижный',
    'MOIS': 'месяц',
    'MOTORISE': 'с приводом',
    'MOY': 'сред',
    'MOYEN': 'средний',
    'NUM': 'номер',
    'OBSTACLE': 'препятствие',
    'OPTMAE': 'опция',
    'OUVERTURE': 'открытие',
    'OUVRANTE': 'открывающаяся',
    'PARAMETRAGE': 'настройка',
    'PARCOURUE': 'пройденное',
    'PARE': 'стекло',
    'PARK': 'парковка',
    'PARTIELLE': 'частичная',
    'PERIMETRIQUE': 'периметр',
    'PHARE': 'фара',
    'PHARES': 'фары',
    'PLUIE': 'дождь',
    'PNEU': 'шина',
    'PNEUS': 'шины',
    'POCHE': 'ящик',
    'POSTE': 'пост',
    'PRECONDITIONNEMENT': 'предподготовка',
    'PREPOSTCHAUFFAGE': 'подогрев',
    'PRESSION': 'давление',
    'PROJ': 'фары',
    'PROJECTEUR': 'фара',
    'PROJECTEURS': 'фары',
    'PROXIMITE': 'близость',
    'PUSH': 'нажатие',
    'RABATTEMENT': 'складывание',
    'RADAR': 'радар',
    'RAZ': 'сброс',
    'RECEPTION': 'приём',
    'RECHAUFFEUR': 'подогреватель',
    'REFRIGERATION': 'охлаждение',
    'REGENERATION': 'регенерация',
    'REGULATION': 'круиз-контроль',
    'RESERVE': 'резерв',
    'RESERVOIR': 'бак',
    'RESIST': 'сопротивление',
    'ROUE': 'колесо',
    'SAUT': 'скачок',
    'SECONDE': 'секунда',
    'SEMIAUTO': 'полуавтомат',
    'SIEGES': 'сиденья',
    'SLMAINT200': 'межсервисный интервал',
    'SLPREMAINT': 'предупреждение о ТО',
    'SOLEIL': 'солнце',
    'SONDE': 'датчик',
    'SOUS': 'под',
    'START': 'старт',
    'STT': 'старт-стоп',
    'SURVEILLANCE': 'контроль',
    'SUSPENSION': 'подвеска',
    'SYNCHRONISEE': 'синхронизирован',
    'TEMP': 'температура',
    'TOTALISATEUR': 'суммарный',
    'TOURELLE': 'поворотный',
    'TRACTION': 'тяга',
    'TRAJET1': 'поездка 1',
    'TRAJET2': 'поездка 2',
    'TRAPPE': 'лючок',
    'UCECAN': 'блок CAN',
    'UDS': 'UDS',
    'UTILISATION': 'использование',
    'VAPEURS': 'пары',
    'VELUM': 'шторка',
    'VERROUILLE': 'заблокирован',
    'VIDE': 'вещевой',
    'VOLUMETRIQUE': 'объёмный',
})

DROP = {"MP", "CFG", "DE", "DU", "DES", "LA", "LE", "LES", "EN", "PAR", "POUR",
        "ET", "OU", "A", "AU", "AUX", "SUR", "New", "NEW", "000"}


def humanise(mnemonic: str) -> str:
    """Собрать читаемое название из французской мнемоники."""
    is_cfg = mnemonic.startswith("CFG_")
    parts = [p for p in re.split(r"_+", mnemonic) if p]
    out = []
    for p in parts:
        up = p.upper()
        if up in DROP:
            continue
        if up in ACRONYMS:
            out.append(ACRONYMS[up])
        elif up in WORDS:
            out.append(WORDS[up])
        elif re.fullmatch(r"\d+", p):
            out.append(p)
        else:
            # неизвестное слово оставляем как есть, но в нижнем регистре
            out.append(p.lower())
    text = " ".join(out).strip()
    if not text:
        text = mnemonic
    text = text[0].upper() + text[1:]
    return ("Конфиг: " + text) if is_cfg else text


def label(did: int, mnemonic: str) -> str:
    """Ярлык параметра: сначала ручной список, потом автоперевод."""
    return CURATED.get(did) or humanise(mnemonic)


if __name__ == "__main__":
    from live_dids import LIVE
    unknown = 0
    for d, n in LIVE:
        lab = label(d, n)
        mark = " " if d in CURATED else ("!" if re.search(r"[a-z]{4,}", lab.replace("Конфиг: ", "")) and
                                        any(w.isupper() for w in re.split(r"_+", n) if w.upper() not in WORDS
                                            and w.upper() not in ACRONYMS and w.upper() not in DROP) else " ")
        if mark == "!":
            unknown += 1
        print(f"{mark} {d:04X}  {lab[:66]:<66} {n[:44]}")
    print(f"\nвсего {len(LIVE)}, из них с непереведёнными словами: {unknown}")
