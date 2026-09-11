"""
Расшифровка кодов состояний для дашборда: число -> подпись.

Только те значения, что ПОДТВЕРЖДЕНЫ данными этой машины (12.09.2026, разбор всех
перечислений против оборотов и скорости в тот же момент), либо бинарные, где смысл
задан именем. Таблицы состояний в базе DiagBox есть, но в клоне от них остались
только номера строк перевода без текста, поэтому "по каталогу" расшифровать нельзя.
Что не подтверждено - остаётся числом, лучше число, чем выдуманная подпись.

Панели с такими параметрами рисуются как state-timeline: цветная полоса с
подписью вместо линии 0..15.
"""

ENUMS = {
    # --- BSI ---
    "MP_POSITION_CLE_CONTACT": {0: "выключено", 1: "зажигание", 2: "стартер", 3: "аксессуары"},
    "MP_ETAT_GMP": {0: "остановлен", 2: "работает"},
    "MP_INFO_GMP": {1: "остановлен", 4: "работает"},
    "MP_ETAT_PRINCIPAL": {105: "стоянка", 150: "двигатель работает"},
    "MP_ETAT_RESEAU_ELECTRIQUE": {0: "выключено", 5: "заряд от генератора"},
    "MP_ETAT_AUTORISATION_DEMARRAGE_DU_CMM": {0: "нет", 1: "разрешён", 2: "двигатель запущен"},
    "MP_ETAT_VERROUILLAGE_VEHICULE": {0: "?", 1: "открыта", 2: "заперта"},
    "MP_MOTIF_VERROUILLAGE_VEHICULE": {1: "ключ/брелок", 8: "автоблокировка на ходу",
                                       16: "стоянка", 32: "открытие"},
    "MP_POSITION_LEVIER_VITESSE": {0: "МКПП", 15: "нет данных"},
    "MP_INFORMATION_JOUR_NUIT": {0: "день", 1: "ночь"},
    "MP_CALCULATEUR_MOTEUR_VERROUILLE": {0: "разблокирован", 1: "заблокирован"},
    "MP_CLE_OU_BADGE_RECONNUE": {0: "нет", 1: "ключ опознан"},
    "MP_FONCTION_DE_REGULATION_A_AFFICHER": {0: "ничего", 2: "ограничитель"},
    "MP_ECLAIRAGE_AMBIANCE_EN_ACCUEIL": {0: "выкл", 1: "встреча", 2: "вкл"},
    # --- двигатель ---
    "V46_32:MP_ETAT_MTH": {1: "остановлен", 5: "работает"},
    "V46_32:MP_ETAT_REVEIL_CALCULATEUR": {1: "сон", 4: "работа"},
    "V46_32:MP_ETAT_REGULATION_SONDE_A_OXYGENE_AMONT": {0: "разомкнуто", 1: "замкнуто"},
    "V46_32:MP_ETAT_REGULATION_SONDE_A_OXYGENE_AVAL": {0: "отключено", 1: "замкнуто"},
    "V46_32:MP_ETAT_SONDE_A_OXYGENE_AMONT": {0: "бедно", 1: "богато"},
    "V46_32:MP_ETAT_SONDE_A_OXYGENE_AVAL": {0: "бедно", 1: "богато"},
    "V46_32:MP_ETAT_CONTACTEUR_PEDALE_FREIN": {0: "отпущена", 1: "нажата"},
    "V46_32:MP_INFORMATION_CAPTEUR_EMBRAYAGE": {0: "отпущено", 1: "выжато"},
    "V46_32:MP_ETATCDEDEM": {0: "выкл", 1: "крутит"},
    "V46_32:MP_ETAT_RELAIS_GMV": {0: "выкл", 1: "вкл"},
    "V46_32:MP_ETAT_GMV_PTIT_C5": {0: "выкл", 1: "вкл"},
    "V46_32:MP_ETAT_REL_GMV_C5": {0: "выкл", 1: "вкл"},
    # --- ГУР ---
    "GEP:MP_ETAT_MOTEUR_THERMIQUE_1_2": {3: "работает"},
    "GEP:APC": {0: "выкл", 1: "зажигание"},
}


def mappings(name):
    """Grafana value mappings для параметра или None."""
    m = ENUMS.get(name)
    if not m:
        return None
    return [{"type": "value",
             "options": {str(k): {"text": v, "index": i} for i, (k, v) in enumerate(sorted(m.items()))}}]
