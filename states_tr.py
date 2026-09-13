"""
Перевод текстов состояний из базы DiagBox на русский.

Почему словарь целых фраз, а не пословный разбор как в dtc_tr.py. Описания кодов
формульные ("circuit short to battery") и их 370 - там пословный перевод выигрывает.
Тексты состояний другие: их всего 320 различных на все параметры, что мы читаем, и
почти все - короткие ярлыки ("Absent", "Appuyé", "Moteur tournant"), где пословный
перевод даёт косноязычие. Закрытый набор переводится целиком.

Правило то же: чего нет в словаре, то остаётся французским и видно через --left.
Выдумывать перевод хуже, чем оставить исходник.

    ./.venv/bin/python states_tr.py > states_ru.py
    ./.venv/bin/python states_tr.py --left        # что осталось непереведённым
"""

import re
import sys

# Фразы целиком. Ключ - точный текст из базы, включая опечатки самой базы
# ("Incative", "multiplixe", "porte arière gauche") и потерянную диакритику.
TR = {
    # да/нет, наличие, активность
    "Oui": "да", "OUI": "да", "Non": "нет", "NON": "нет",
    "ON": "вкл", "OFF": "выкл", "Etat off": "выключено",
    "Absent": "нет", "Absente": "нет", "Présent": "есть", "Présente": "есть",
    "Actif": "активно", "Active": "активно", "Activé": "включено",
    "Activée": "включено", "Non activée": "не включено",
    "Inactif": "неактивно", "Inactive": "неактивно", "Incative": "неактивно",
    "NonActive": "не активно", "Disponible": "доступно",
    "Indisponible": "недоступно", "indisponible": "недоступно",
    "Libre": "свободно", "Hors Service": "не работает",
    "Neutralisé": "отключено", "inhibé": "запрещено",
    "Autorisé": "разрешено", "Interdit": "запрещено",
    "Attente": "ожидание", "En attente": "ожидание",
    "Instable": "неустойчиво",
    "Suffisant": "достаточно", "Insuffisant": "недостаточно",
    "Détectée": "обнаружено",
    "Synchronisée": "синхронизировано", "Désynchronisée": "рассинхронизировано",
    "Controlé": "проверено", "Non controlé": "не проверено",
    "Non diagnostiqué": "не диагностировано",
    "programmé": "запрограммировано", "non programmé": "не запрограммировано",
    "Non configuré": "не сконфигурировано", "non configuré": "не сконфигурировано",
    "Client": "клиентский", "Confort": "комфорт", "Auto": "авто",
    "Fixe": "постоянно", "Clignotante": "мигает", "Clignotant": "мигает",

    # исправность
    "Pas de défaut": "неисправностей нет", "Absence de défaut": "неисправностей нет",
    "En défaut": "неисправно", "en défaut": "неисправно", "defaut": "неисправность",
    "Défectueux": "неисправен",
    "Fonctionnement nominal": "штатная работа",
    "Fonctionnement normal": "штатная работа",
    "Valide": "достоверно", "Invalide": "недостоверно", "invalide": "недостоверно",
    "NonValide": "недостоверно",
    "Valeur Invalide": "недопустимое значение",
    "Valeur invalide": "недопустимое значение",
    "Valeur invalide ou indisponible": "недопустимое или недоступное значение",
    "Indéfini": "не определено", "Indefini": "не определено",
    "Indetermine": "не определено", "Indeterminée": "не определено",
    "Non defini": "не определено", "non_defini": "не определено",

    # положения и нажатия
    "Appuyé": "нажато", "Relâché": "отпущено", "Relaché": "отпущено",
    "Actionné": "задействовано", "Non actionné": "не задействовано",
    "Ouvert": "открыто", "Ouverte": "открыто",
    "Fermé": "закрыто", "Fermée": "закрыто",
    "Verrouillé": "заперто", "Verrouille": "заперто",
    "Déverrouillé": "отперто",
    "Déverrouillé conducteur": "отперта дверь водителя",
    "Superverrouillé": "суперблокировка",
    "Cabine avant déverrouillé": "передняя кабина отперта",
    "Compartiment de charge arrière déverrouillé": "задний грузовой отсек отперт",
    "Allumée": "горит", "Eteinte": "погашена",
    "Contact mis": "зажигание включено",
    "Contact ouvert": "контакт разомкнут",
    "Contact ferme (demande d'activation par BSI)": "контакт замкнут (запрос от BSI)",

    # двигатель и трансмиссия
    "Moteur": "двигатель",
    "Moteur tournant": "двигатель работает", "Tournant": "работает",
    "Non tournant": "не работает",
    "Moteur arrete": "двигатель остановлен",
    "Arrêt": "остановлен", "Arret": "остановлен", "Arrete": "остановлен",
    "Arrêté": "остановлен", "Arrêtée": "остановлен",
    "Arrêté (Stop And Start)": "остановлен (Stop&Start)",
    "Demarrage": "пуск", "En démarrage": "пуск",
    "Demarrage autonome": "автономный пуск",
    "En redémarrage": "повторный пуск",
    "Redemarrage autonome": "автономный повторный пуск",
    "Redemarrage degrade": "аварийный повторный пуск",
    "Redemarrage entraine": "принудительный повторный пуск",
    "En preparation": "подготовка",
    "Embrayage ouvert": "сцепление разомкнуто",
    "Embrayage fermé": "сцепление замкнуто",
    "Première": "первая", "Deuxième": "вторая", "Troisième": "третья",
    "Quatrième": "четвёртая", "Cinquième": "пятая", "Sixième": "шестая",
    "Marche arrière": "задний ход", "Parc": "стоянка", "Parking": "стоянка",
    "Maintien de la cylindrée": "сохранение рабочего объёма",
    "Réduction à 5 % de la cylindrée": "снижение рабочего объёма до 5 %",
    "Débrayage du compresseur": "отключение компрессора",

    # скорости вентилятора и стеклоочистителя
    "PremiereVitesse": "первая скорость", "DeuxièmeVitesse": "вторая скорость",
    "Petitevitesse": "малая скорость", "GrandeVitesse": "большая скорость",
    "PetiteVitesseOuVitessevariable": "малая или переменная скорость",
    "PasDeCommande": "команды нет",
    "Essuyage Avant": "передний стеклоочиститель",
    "Automatique ou intermittence": "автоматический или прерывистый",

    # свет
    "Veilleuses": "габариты", "Codes": "ближний свет",
    "Appel de phares": "моргание дальним",
    "Appel de phares ou feux de route activés": "моргание дальним или дальний включён",
    "ClignotantDroit": "правый поворотник", "ClignotantGauche": "левый поворотник",
    "PositionRoute": "загородный режим", "PositionVille": "городской режим",
    "Avertisseur sonore": "звуковой сигнал",
    "Visibilité": "видимость",

    # запирание и сигнализация
    "Télécommande à haute fréquence": "радиобрелок",
    "Clé mécanique": "механический ключ",
    "Menu de personnalisation": "меню настроек",
    "Verrouillage en roulant": "запирание на ходу",
    "Contacteur de verrouillage centralisé": "кнопка центрального замка",
    "Ouverture d?une porte à la poignée intérieur": "открытие двери внутренней ручкой",
    "Reverrouillage automatique": "автоматическое повторное запирание",
    "Fonction main libre": "бесключевой доступ",
    "Sécurité enfant": "детский замок",
    "Choc véhicule": "удар по кузову",
    "Fonctionnement Usine": "заводской режим",
    "Commande de l'alarme": "команда сигнализации",
    "Activation de l'alarme en cours": "идёт постановка на охрану",
    "Attente d'activation de l'alarme suite à rebond du verrouillage":
        "ожидание постановки на охрану после повторного запирания",
    "Attente par l'alarme de la fermeture du dernier ouvrant":
        "сигнализация ждёт закрытия последнего проёма",
    "Détection d'une infraction par l'alarme": "сигнализация зафиксировала проникновение",
    "Changement d'état d'un ouvrant": "изменение состояния проёма",
    "Changement d'état de verrouillage/déverrouillage du véhicule":
        "изменение состояния запирания машины",

    # проёмы и электроприводы
    "Ouverture ou fermeture de la porte conducteur": "открытие или закрытие двери водителя",
    "Ouverture ou fermeture de la porte passager": "открытие или закрытие двери пассажира",
    "Ouverture ou fermeture de la Porte arrière Droite":
        "открытие или закрытие задней правой двери",
    "Ouverture ou fermeture de la porte arière gauche":
        "открытие или закрытие задней левой двери",
    "Ouverture ou fermeture du capot": "открытие или закрытие капота",
    "Ouverture ou fermeture du coffre": "открытие или закрытие багажника",
    "Ouverture ou fermeture de la lunette": "открытие или закрытие заднего стекла",
    "Appui sur le bouton extérieur d'ouverture du coffre":
        "нажата наружная кнопка открытия багажника",
    "Appui sur le bouton extérieur d'ouverture de la lunette arrière":
        "нажата наружная кнопка открытия заднего стекла",
    "Appui sur le commutateur de démarrage moteur": "нажата кнопка пуска двигателя",
    "Mise de la clé en position Contact ou appui sur le commutateur de démarrage moteur":
        "ключ переведён в зажигание или нажата кнопка пуска",
    "Demande d'ouverture extérieure du Coffre motorisé":
        "запрос наружного открытия электропривода багажника",
    "Demande de mouvement électrique du siège conducteur":
        "запрос электрорегулировки сиденья водителя",
    "Demande de mouvement électrique du siège passager":
        "запрос электрорегулировки сиденья пассажира",
    "Mouvement électrique des rétroviseurs": "электропривод зеркал",
    "Mouvement électrique des vitres": "электропривод стеклоподъёмников",
    "Mouvement électrique du siège conducteur": "электропривод сиденья водителя",
    "Mouvement électrique du siège passager": "электропривод сиденья пассажира",
    "Mouvement de la Colonne de direction": "регулировка рулевой колонки",
    "Mouvement de la vision tête haute": "регулировка проекционного дисплея",

    # запросы
    "Demande": "запрос", "Demandé": "запрошено", "demandé": "запрошено",
    "Non demandé": "не запрошено", "Pas de demande": "запроса нет",
    "Demande Active": "запрос активен", "Demande Inactive": "запрос неактивен",
    "Demande absente": "запроса нет", "Demande présente": "запрос есть",

    # подушки безопасности
    "Allumeur opérationnel": "пиропатрон исправен",
    "sortie allumeur disponible": "выход на пиропатрон доступен",
    "Mise à feu effectuée": "подрыв произведён",
    "mise à feu effectuée": "подрыв произведён",
    "Pas de choc enregistré": "ударов не записано",
    "chocs enregistrés": "удары записаны",
    "Shunt absent": "перемычки нет", "Résistant": "с сопротивлением",

    # прицеп
    "Remorque presente": "прицеп подключён",
    "BSR absente": "блока прицепа нет", "BSR presente": "блок прицепа есть",
    "Inhibition conducteur": "запрет водителем",
    "Inhibition remorque": "запрет из-за прицепа",
    "Activation des Feux de détresse": "включение аварийной сигнализации",
    "Feux de détresse activés (avec remorque présente)":
        "аварийка включена (прицеп подключён)",
    "Détection du branchement d'un remorque (Feux de détresse activés)":
        "обнаружено подключение прицепа (аварийка включена)",

    # круиз и ограничитель
    "Aucune Selection": "ничего не выбрано",
    "Régulation de vitesse": "круиз-контроль",
    "Limitation de vitesse": "ограничитель скорости",
    "Régulation de vitesse adaptative": "адаптивный круиз-контроль",

    # подсветка салона
    "Mode accueil actif": "режим встречи активен",
    "Mode ambiance actif": "режим подсветки салона активен",
    "Mode d'accueil et d'ambiance inactif": "режимы встречи и подсветки выключены",
    "Activation de l'eclaireur d'accueil": "включение освещения при встрече",
    "Désactivation du balisage latéral (Batterie faible ou fin de temporisation)":
        "отключение боковой подсветки (слабый аккумулятор или конец выдержки)",

    # BSI: пробуждение и удержание
    "Aucun Maintien": "удержания нет", "Aucun Réveil": "пробуждения нет",
    "Reset du boîtier de servitude intelligent": "сброс BSI",
    "Etat de fonctionnement du boîtier de servitude intelligent": "режим работы BSI",
    "Dernière Condition de réveil du boîtier de servitude intelligent":
        "последняя причина пробуждения BSI",
    "Dernière condition de maintien du boîtier de servitude intelligent":
        "последняя причина удержания BSI",
    "Dernière condition de réveil des réseaux CAN intersystème":
        "последняя причина пробуждения межсистемных сетей CAN",
    "Dernière Condition abusive de maintien des réseaux CAN intersystème":
        "последняя недопустимая причина удержания межсистемных сетей CAN",
    "Etat d'autorisation de fonctions énegétiques": "разрешение энергозависимых функций",
    "Maintien par : Activation de l'Eclaireur de coffre": "удержание: подсветка багажника",
    "Maintien par : Activation des Feux de détresse": "удержание: аварийная сигнализация",
    "Maintien par : Activation des feux de positions": "удержание: габаритные огни",
    "Maintien par : Activation du Plafonnier arrière": "удержание: задний плафон",
    "Maintien par : Activation du plafonnier avant": "удержание: передний плафон",
    "Maintien par : Besoin d'alimentation en plus commutateur de puissance centralisé":
        "удержание: требуется питание центрального силового коммутатора",
    "Maintien par : Commande de la ligne de réveil commandé à distance (Sous capot)":
        "удержание: линия дистанционного пробуждения (под капотом)",
    "Maintien par : Pilotage de la ou des serrures de porte":
        "удержание: управление дверными замками",
    "Maintien par : Pilotage de la serrure de la lunette arrière":
        "удержание: замок заднего стекла",
    "Maintien par : Pilotage de la serrure de la trappe à carburant":
        "удержание: замок лючка бака",
    "Maintien par : Pilotage de la serrure du coffre": "удержание: замок багажника",
    "Maintien par : Présence de l'alimentation plus accessoire":
        "удержание: наличие питания аксессуаров",
    "Maintien par : Réseau LIN_1 réveillé": "удержание: сеть LIN_1 разбужена",
    "Maintien par : Réseaux CAN LS (Habitacle) réveillés":
        "удержание: сети CAN LS (салон) разбужены",
    "Mise en route de la Radio ou de la Télématique": "включение радио или телематики",
    "Mise en route du préconditionnement thermique": "включение предварительного прогрева",
    "Préconditionnement thermique habitacle": "предварительный прогрев салона",
    "Ventilation habitacle en fonctionnement": "вентиляция салона работает",
    "Radio": "радио", "Télématique": "телематика",

    # выходы BSI
    "Sortie plafonnier avant": "выход переднего плафона",
    "Sortie plafonnier arrière": "выход заднего плафона",
    "Sortie trappe à carburant": "выход лючка бака",
    "Sortie éclairage intérieur d'ambiance": "выход подсветки салона",
    "Sortie éclaireur de coffre": "выход подсветки багажника",
    "Sortie éclaireurs des boutons poussoirs intérieurs":
        "выход подсветки внутренних кнопок",
    "Relais après contact maintenu fermé": "реле после зажигания удерживается замкнутым",

    # достоверность отдельных сигналов
    "Etat de la ceinture conducteur invalide": "состояние ремня водителя недостоверно",
    "Etat du verrouillage du véhicule invalide": "состояние запирания машины недостоверно",
    "Etat verrouillage et ceinture invalide": "состояния запирания и ремня недостоверны",
    "Affichage de l'état frein de service électrique":
        "индикация электрического стояночного тормоза",

    # блокировка блока, сеансы, ТО
    "Calculateur verrouillé définitivement": "блок заблокирован окончательно",
    "Calculateur non verrouillé définitivement": "блок не заблокирован окончательно",
    "Calculateur verrouillé en après-vente": "блок заблокирован в сервисе",
    "Calculateur non verrouillé en après-vente": "блок не заблокирован в сервисе",
    "Session diagnostic avec un autre calculateur": "диагностический сеанс с другим блоком",
    "Session diagnostic ou téléchargement": "диагностический сеанс или загрузка ПО",
    "Non (Seuil de maintenance non atteint)": "нет (порог ТО не достигнут)",
    "Oui (Seuil de maintenance dépassé)": "да (порог ТО превышен)",
    "Niveau d'état de charge de la batterie": "уровень заряда аккумулятора",
    "Status de l'estimation de charge batterie": "состояние оценки заряда аккумулятора",
    "XX,X Volts": "XX,X В",

    # физический уровень и прочее
    "analogique": "аналоговый", "multiplixe": "мультиплексный",
    "assiste": "с усилителем", "non assiste": "без усилителя",
}

# Шаблонные тексты, различающиеся только хвостом.
PREFIX = {
    "Code fournisseur ": "код поставщика ",
    "Indice de télécodage ": "индекс телекодирования ",
    "Position ": "положение ",
    "Valeur ": "значение ",
    "Vitesse": "скорость ",
}


def _lost(t):
    """Совпадение по тексту с утраченной диакритикой.

    В части таблиц образа диакритика потеряна ещё при сборке DiagBox: в поле лежит
    буквально байт 0x3F, то есть "Moteur arr?t?" вместо "Moteur arrêté" (проверено
    чтением столбца как OCTETS - колонка объявлена без кодировки, так что это не
    наша перекодировка потеряла символы, а исходные данные такие).

    Поэтому "?" сопоставляется как ЛЮБОЙ один символ, и перевод берётся только
    если подошла ровно одна фраза словаря: неоднозначное совпадение оставляем
    непереведённым, а не выбираем наугад.
    """
    if "?" not in t:
        return None
    pat = re.compile("".join("." if c == "?" else re.escape(c) for c in t), re.I)
    hits = {v for k, v in TR.items() if pat.fullmatch(k)}
    return hits.pop() if len(hits) == 1 else None


def tr(text):
    """Перевод одного текста. None - перевода нет, оставить французский."""
    t = text.strip()
    if t in TR:
        return TR[t]
    lost = _lost(t)
    if lost:
        return lost
    if t.isdigit():                       # чистые номера состояний оставляем как есть
        return t
    for p, ru in PREFIX.items():
        if t.startswith(p) and t[len(p):].strip():
            return ru + t[len(p):].strip()
    return None


def collisions():
    """Фразы словаря, неразличимые после потери диакритики - перевод был бы лотереей."""
    bad = []
    for k in TR:
        masked = re.sub(r"[^\x00-\x7f']", "?", k).replace("'", "?")
        if _lost(masked) is None and "?" in masked:
            bad.append(k)
    return bad


def main():
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import states_fr
    left = set()
    out = {}
    for key, states in states_fr.STATES.items():
        ru = {}
        for v, text in states.items():
            r = tr(text)
            if r is None:
                left.add(text)
                r = text                  # честнее оставить французский, чем выдумать
            ru[v] = r
        out[key] = ru
    if "--collisions" in sys.argv:
        bad = collisions()
        for k in bad:
            print(f"  {k!r}")
        print(f"неразличимых после потери диакритики: {len(bad)}")
        return 0
    if "--left" in sys.argv:
        print(f"без перевода: {len(left)}")
        for t in sorted(left):
            print(" ", t)
        return 0
    print('"""Состояния параметров по-русски. СГЕНЕРИРОВАН states_tr.py из states_fr.py.\n\n'
          "Непереведённое осталось французским намеренно: выдуманный перевод хуже\n"
          'исходника. Список - states_tr.py --left.\n"""')
    print("\nSTATES_RU = {")
    for k in sorted(out):
        print(f"    {k!r}: {out[k]!r},")
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
