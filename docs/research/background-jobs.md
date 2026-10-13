# Фоновые задачи и расписание в проде

Исследование к тикету `stage-3/02-background-jobs`. Дата: 2026-10-13.

## Вопрос

Как запускать фоновые задачи (долгий ход Ассистента, отправка Web Push) и задачи по расписанию (вечерняя и недельная Сводки) в нашем деплое? Сейчас там `docker-compose.prod.yml` с одним сервисом `web` (gunicorn, 3 sync-воркера), внешний Postgres через `DATABASE_URL`, Redis нет, Django `>=6.1,<6.2`.

## Вывод

1. **Задачи надо писать на `django.tasks`** (фреймворк задач Django 6) с бэкендом **`django-tasks-db`** (`django_tasks_db.DatabaseBackend`). Задачи хранятся в нашем Postgres. В compose добавляется **один** сервис `worker`: тот же образ, команда `python manage.py db_worker`. Redis, брокер и новые внешние сервисы не нужны.
2. **Расписание.** Сводки должна запускать периодическая задача-«тик», например раз в 5 минут. Тик смотрит в БД, кому Сводка уже положена и ещё не отправлена, и ставит в очередь отправку. Тогда пропущенный из-за рестарта запуск отрабатывает на следующем тике, а повторный запуск ничего не дублирует. Запускать тик можно через `django-crontask` (`python manage.py crontask`) отдельным процессом или контейнером `scheduler`. Вариант проще: сделать тик management-командой и вызывать её cron'ом (supercronic) в том же образе.
3. **Чего у `django-tasks-db` нет и что придётся сделать самим** (проверено по исходникам 0.13.0):
   - автоматических повторов;
   - восстановления задачи, у которой процесс убили посреди выполнения: она навсегда останется в `RUNNING`;
   - очистки старых результатов. Для неё есть команда `prune_db_task_results`, её надо запускать по расписанию.

   Это решается так: задачи делаем идемпотентными, контейнеру воркера даём `stop_grace_period`, раз в тик переставляем «зависшие» `RUNNING`.
4. Celery/RQ отпадают: им нужен Redis (или другой брокер). У django-q2 и procrastinate надёжность выше «из коробки», но у них свой API задач, не `django.tasks`. Они остаются запасным вариантом, если ручные повторы и восстановление станут обузой. Ближе всех к нашему стеку из них procrastinate: только Postgres, heartbeat, автоматический перезапуск зависших задач, встроенные периодические задачи.

## Что даёт сам Django

- Во встроенном фреймворке задач только два бэкенда, `ImmediateBackend` (по умолчанию) и `DummyBackend`. Оба «for development and testing only». Воркера Django не поставляет: «It does not provide a worker mechanism to run Tasks… Production systems should rely on backends that supply a worker process and a durable queue implementation» ([Django 6.1 — Tasks](https://docs.djangoproject.com/en/6.1/topics/tasks/)).
- В Django 6.1 по задачам изменились две мелочи: `task()` принимает `**kwargs`, а `Task`/`TaskResult` теперь можно pickle'ить. Новых бэкендов, повторов и расписания не появилось ([release notes 6.1](https://docs.djangoproject.com/en/6.1/releases/6.1/), релиз 2026-08-05).
- API ([reference](https://docs.djangoproject.com/en/6.1/ref/tasks/)):
  - `@task`, `.enqueue()`/`.aenqueue()`;
  - `.using(priority=, queue_name=, run_after=)`;
  - `get_result(id)`;
  - `TaskContext.attempt`;
  - статусы `READY/RUNNING/FAILED/SUCCESSFUL`.

  Аргументы проходят через JSON round-trip. API повторов нет: `attempt`/`attempts` только счётчики.
- Транзакции: ставить задачу в очередь нужно через `transaction.on_commit(partial(task.enqueue, ...))`, иначе воркер может не увидеть ещё не закоммиченные строки ([topics/tasks](https://docs.djangoproject.com/en/6.1/topics/tasks/)).
- Тесты: `ImmediateBackend` «may also be useful in tests, to bypass the need to run a real background worker». `DummyBackend` хранит поставленные задачи в `default_task_backend.results` ([topics/tasks](https://docs.djangoproject.com/en/6.1/topics/tasks/)). Это укладывается в наше правило «тесты через HTTP-шов»: в тестовых настройках `ImmediateBackend`, и после запроса проверяем состояние БД.

## `django-tasks-db`: бэкенд на Postgres

Источники: [PyPI](https://pypi.org/project/django-tasks-db/), [GitHub](https://github.com/RealOrangeOne/django-tasks-db), исходники wheel 0.13.0.

- Версия 0.13.0 от 2026-08-28, статус «4 - Beta». Классификаторы называют Django 5.2 и 6.0, `requires_dist` — `django>=5.2` без верхней границы. `compat.py` импортирует `django.tasks`, когда тот есть, поэтому на 6.1 пакет должен работать, но 6.1 в классификаторах не заявлена. **Это нужно проверить при внедрении.** Автор пакета — автор `django.tasks` в ядре. С 0.12.0 DB- и RQ-бэкенды вынесены из `django-tasks` в отдельные пакеты ([django-tasks на PyPI](https://pypi.org/project/django-tasks/)).
- Настройка: `INSTALLED_APPS += ["django_tasks_db"]`, `TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend", "QUEUES": ["default"]}}`. Таблицы создаются обычными миграциями.
- Возможности бэкенда (`backend.py`): `supports_defer`, `supports_get_result`, `supports_priority`, `supports_async_task` — все `True`. Значит, есть `run_after` (отложенный запуск) и чтение результата из view, например для htmx-поллинга хода Ассистента.
- Воркер `db_worker` (`management/commands/db_worker.py`):
  - Ключи: `--queue-name` (через запятую или `*`), `--exclude-queues`, `--interval` (по умолчанию 1 с), `--batch`, `--max-tasks`, `--reload` (по умолчанию `DEBUG`; в проде не нужен, потому что «tasks may not be stopped cleanly»), `--worker-id`.
  - Задачу забирает `select_for_update(skip_locked=True)` с последующим `claim()`. Поэтому несколько воркеров безопасно работают параллельно.
  - Один процесс выполняет задачи **по одной**. Параллельность — это число процессов или реплик.
  - Опрашивает БД раз в `--interval` секунд, без LISTEN/NOTIFY.
- Рестарт:
  - На SIGTERM/SIGINT воркер дожидается конца текущей задачи и выходит. Второй сигнал прерывает задачу (`shutdown()`).
  - Docker по умолчанию шлёт SIGKILL через 10 с после SIGTERM, поэтому для долгого хода Ассистента нужен `stop_grace_period` с запасом.
  - Если процесс убит посреди задачи, строка остаётся в `RUNNING`. Кода, который возвращает такие задачи в очередь, в пакете **нет**.
  - Исключение в задаче даёт `FAILED`, автоматического повтора **нет**.
- Очистка: `prune_db_task_results --min-age-days 14 [--failed-min-age-days N] [--dry-run]`.

## Расписание

`django.tasks` расписания не даёт. Варианты:

| Вариант | Как | Плюсы | Минусы |
|---|---|---|---|
| **django-crontask** 2.2.0 (2026-10-10, Django 6.0/6.1, Production/Stable) | `@cron("*/5 * * * *")` над `@task`, процесс `manage.py crontask` ([GitHub](https://github.com/codingjoe/django-crontask)) | Сделан для `django.tasks`: по расписанию просто вызывает `enqueue`; есть heartbeat | Ещё один процесс; лок от двойного запуска только через Redis (опционально, при одном экземпляре не нужен); тянет APScheduler. Пропущенные во время простоя запуски, по всей видимости, не догоняются: в README про это ничего нет, это вывод из того, что расписание живёт в памяти |
| **cron-контейнер** (supercronic или cron в том же образе) + management-команда | `*/5 * * * * python manage.py <тик>` | Ничего не надо изучать, команда тестируется как обычная функция | Ещё один контейнер или процесс и cron-обвязка в образе; у cron в контейнере свои тонкости (env, логи в stdout) |
| **Самоперепланирование** через `run_after` | Задача в конце ставит себя же с `run_after=+5 мин` | Без нового процесса | Хрупко: сбой или потеря цепочки останавливает расписание навсегда, нужен «стартер». Не рекомендую |

Какой бы вариант ни выбрали, сам тик должен быть **идемпотентным и опираться на состояние в БД**: «кому сейчас пора Сводка и ещё не отправлена». Тогда точность и надёжность планировщика почти перестают иметь значение. Тот же тик может перезапускать зависшие `RUNNING` старше порога и вызывать очистку старых результатов.

## Альтернативы

- **Celery** 5.6.3, **RQ** 2.12.0 / **django-rq** 4.2.0. Нужен брокер (RQ требует `redis`). Это плюс контейнер Redis, плюс воркер, плюс beat/scheduler. По критерию «без Redis» отпадают. `django-tasks-rq` (Beta) даёт `django.tasks` поверх RQ, но тоже требует Redis. `django-tasks-scheduler` 4.4.0 требует `redis>=6`.
- **django-q2** 1.11.1 (2026-08-26, Production/Stable). Брокер на Django ORM подходит, Redis не нужен, но сами авторы предупреждают: «should probably not be your first choice for a high traffic setup» ([brokers](https://django-q2.readthedocs.io/en/master/brokers.html)). Есть delivery receipts и повторная постановка при неполучении квитанции; retry должен быть больше timeout. Расписание встроено: модель `Schedule`, cron через `croniter`, `catch_up` догоняет пропущенное ([schedules](https://django-q2.readthedocs.io/en/master/schedules.html)). Минусы: свой API (`async_task`), не `django.tasks`; хранит pickle (`django-picklefield`); один контейнер `qcluster`.
- **procrastinate** 3.10.0 (2026-09-23, Production/Stable). Работает только на Postgres, есть интеграция с Django (модели, миграции, admin), периодические задачи встроены (зависит от `croniter`). Воркеры шлют heartbeat раз в 10 с. Задачи воркеров без heartbeat считаются зависшими и перезапускаются через `retry_stalled_jobs` ([docs](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html), [Django](https://procrastinate.readthedocs.io/en/stable/howto/django.html)). Минусы: свой API, не `django.tasks`; воркер асинхронный, на `psycopg[pool]`.

## Сравнение по критериям

| | Доп. контейнеры/сервисы | Рестарт | Тестируемость | Зрелость |
|---|---|---|---|---|
| `django.tasks` + `django-tasks-db` | `worker` (+ `scheduler` или cron) | Мягкий стоп ждёт задачу; при SIGKILL задача висит в `RUNNING`, повторов нет | Лучшая: штатные `ImmediateBackend`/`DummyBackend` | API в ядре Django; бэкенд Beta |
| cron + management-команда (без очереди) | `cron` | Расписание надёжно, но долгий ход Ассистента и Push сюда не подходят | Хорошая | Классика |
| Celery / RQ | Redis + worker + beat | Хорошо (ack/повторы) | Средняя | Высокая |
| django-q2 (ORM) | `qcluster` | Квитанции и перепостановка | Средняя (sync-режим) | Production/Stable |
| procrastinate | `worker` (расписание внутри) | Heartbeat и автоперезапуск | Средняя | Production/Stable |

## Предлагаемая схема

```yaml
  worker:
    image: ${IMAGE:-food-web:latest}
    restart: unless-stopped
    env_file: …  # те же переменные, что у web
    command: python manage.py db_worker --queue-name '*'
    stop_grace_period: 2m   # долгий ход Ассистента должен успеть закончиться
```

Расписание: либо `python manage.py crontask` отдельным сервисом `scheduler` (одна реплика), либо supercronic в `worker`. Миграции по-прежнему выполняет только `web`.

## Открытые вопросы

- Подтвердить, что `django-tasks-db` 0.13 работает на Django 6.1 (в классификаторах её нет).
- Сколько длится ход Ассистента: от этого зависят `stop_grace_period` и порог для «зависших» `RUNNING`.
- Если параллельно может идти несколько ходов Ассистента, нужен ли второй воркер или отдельная очередь, чтобы Push не ждал Ассистента?
