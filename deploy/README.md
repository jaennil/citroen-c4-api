# Развёртывание базы под телеметрию машины

Повторяет конвенцию homelab-infra: свой namespace, свой кластер CloudNativePG,
отдельный источник данных в Grafana - ровно как сделано для life-dashboard.

## Порядок

1. Создать и запечатать секрет (в репозиторий только запечатанный):

       kubectl create secret generic citroen-postgres-credentials \
         --namespace citroen --from-literal=username=car \
         --from-literal=password="$(openssl rand -base64 24)" \
         --dry-run=client -o yaml | kubeseal -o yaml > sealed-secret.yaml

2. Применить `namespace.yaml`, `sealed-secret.yaml`, `postgres.yaml`.
3. Добавить источник данных из `grafana-datasource.snippet.yaml`
   в `monitoring/grafana/configmaps.yaml`.

## Заливка данных с ноутбука

    export CAR_PG="postgresql://car:ПАРОЛЬ@<хост>:5432/car"
    ./.venv/bin/python sync.py

Схема в Postgres создаётся сама при первом запуске. Синк идемпотентный:
шлёт только несинхронизированное и помечает лишь после успешной вставки,
поэтому обрыв связи ничего не теряет и не задваивает.

## Примеры запросов для Grafana

Временной ряд по одному параметру:

    SELECT r.ts AS "time", r.value
    FROM reading r JOIN param p ON p.id = r.param_id
    WHERE p.name = 'MP_REGIME_MOTEUR_AFFICHE' AND $__timeFilter(r.ts)
    ORDER BY 1;

Несколько параметров сразу:

    SELECT r.ts AS "time", p.name AS metric, r.value
    FROM reading r JOIN param p ON p.id = r.param_id
    WHERE p.name = ANY($params) AND $__timeFilter(r.ts)
    ORDER BY 1;
