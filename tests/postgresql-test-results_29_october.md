# Проверка PostgreSQL и датасета BiletFlow

Дата: 29 октября 2026. Репозиторий: https://github.com/renatuly/biletflow
Ревизия: ff03c356bcca8c9a954348326144fa88e6d0c412.

## План проверки

Изолированный PostgreSQL 17 в контейнере biletflow-qa-20261029, без опубликованных портов. Только синтетические данные. Исходники не меняются.
Проверки: загрузка BiletFlow_MVP_schema.sql; положительная цепочка пользователь → организатор → событие → тип билета; уникальность email без учёта регистра; внешние ключи; перепродажа; отрицательные цены и количества; порядок дат события; повторная загрузка схемы. Ожидается отклонение некорректных данных. Каждый запрос ограничен 30 секундами. Отдельно проверить соответствие SQL текущим ORM-моделям. Python-тесты и dev_seed отмечать как незапущенные при отсутствии зависимостей.

## Итог

Статус: **частично выполнено**. SQL-схема проверена на реальном PostgreSQL; демодатасет приложения и pytest не запускались из-за отсутствия Python-зависимостей. Это ограничение окружения, а не доказательство ошибки приложения.

Сервер: PostgreSQL 17.11, aarch64, Alpine. Созданы 22 таблицы. Положительная цепочка из четырёх связанных записей создана успешно. Девять негативных сценариев выполнены по два раза, каждый в отдельной транзакции с откатом. Состояние исходных записей после проверок сохранено.

## Результаты

| Проверка | Наблюдение | Оценка |
|---|---|---|
| Загрузка SQL в пустую базу | Код завершения 0, 22 таблицы | PASS |
| Пользователь → организатор → событие → тип билета | Все четыре INSERT успешны; users/events/ticket_types = 1/1/1 | PASS |
| Email QA@EXAMPLE.COM после qa@example.com | unique violation, код 3; два запуска | PASS |
| Тип билета с отсутствующим событием | foreign key violation, код 3; два запуска | PASS |
| quantity_sold=11 при quantity_total=10 | check violation, код 3; два запуска | PASS |
| price_kzt=-1 | UPDATE 1, accepted; два запуска | F1: нет защиты в SQL |
| quantity_sold=-1 | UPDATE 1, accepted; два запуска | F2: нет защиты в SQL |
| quantity_total=-1, quantity_sold=-2 | UPDATE 1, accepted; два запуска | F3: нет защиты в SQL |
| max_per_order=0 | UPDATE 1, accepted; два запуска | F4: нет защиты в SQL |
| ends_at раньше starts_at | UPDATE 1, accepted; два запуска | F5: нет защиты в SQL |
| capacity=-1 | UPDATE 1, accepted; два запуска | F6: нет защиты в SQL |
| Состояние после откатов | 1000.00 / 10 / 0 / 10; ends_at>starts_at=true | PASS |
| Повторная загрузка SQL | Ошибка type "user_role" already exists, код 3 | Неидемпотентный DDL; повторное применение не заявлено |

## Найденные ограничения

F1–F6 подтверждены повторным выполнением одинаковых SQL-запросов. Для проверки целостности ожидалось отклонение отрицательных значений, нулевого лимита и обратного интервала дат. Схема их принимает. У F3 меняются оба счётчика: это необходимо, чтобы выполнить существующее условие quantity_sold <= quantity_total и отдельно показать отсутствие нижней границы quantity_total. Рекомендуются CHECK-ограничения с учётом бизнес-правил. Результаты не доказывают, что такие данные можно отправить через API: его валидация здесь не проверялась.

F7 — статическое расхождение SQL-файла и текущих моделей приложения:

- SQL users содержит locale и отдельную user_roles; ORM ожидает preferred_locale, roles, email_verified, is_active.
- SQL ticket_types содержит price_kzt, quantity_total, quantity_sold; ORM ожидает price_minor, quantity, sold_count и reserved_count.
- SQL orders использует buyer_user_id, buyer_email и total_amount; ORM использует user_id, attendee_email и total_minor. В SQL есть статус paid, в ORM — confirmed.
- SQL tickets использует qr_code_token; ORM — qr_credential и дополнительные связи event_id/ticket_type_id/owner_id.

Источники: BiletFlow_MVP_schema.sql; app/users/models.py; app/tickets/models.py; app/orders/models.py. Совместимость API с базой, созданной этим SQL-файлом, не подтверждена. README предписывает создание таблиц через приложение. Поэтому SQL-файл нельзя считать проверенным способом подготовки базы для текущего dev_seed.

## Датасет и непройденные проверки

В репозитории найден генератор app/dev_seed.py, а не готовый дамп PostgreSQL. README обещает идемпотентность генератора и отказ в APP_ENV=production; эти свойства не проверены исполнением. В этом запуске использованы только четыре синтетические записи для SQL-проверок.

Не выполнены: запуск dev_seed и повторный запуск, сверка количества/связности его записей, финансовые итоги, pytest на PostgreSQL, конкурентные продажи и check-in, нагрузка и производительность. Python 3.14 по умолчанию не соответствует требованию проекта >=3.11,<3.14. Найден Python 3.13, но pytest отсутствует; в Python по умолчанию проверка импорта также остановилась на отсутствии pytest. Полный комплект зависимостей не установлен и не проверен.

Для завершения проверки датасета нужно установить штатные зависимости в изолированное окружение Python 3.13, создать две отдельные пустые PostgreSQL-базы (для seed и pytest), выполнить python -m app.dev_seed дважды с APP_DATABASE_URL, затем TEST_DATABASE_URL=... pytest. В tests/conftest.py перед тестом выполняется Base.metadata.drop_all(engine): использовать только специально созданную тестовую базу. Проверка идемпотентности должна сравнивать ключи и число записей, а не только код завершения.

Код проекта, тесты и настройки не исправлялись. Тестовый контейнер после проверки удалён вместе с его временными данными; исходники сохранены в рабочей копии. Длительность всей сессии не измерялась. Практическое наблюдение для повторной проверки: SQL-файл и ORM следует проверять как разные варианты схемы.

## Воспроизведение и полные наблюдения

Загрузка схемы: `docker exec -i biletflow-qa-20261029 psql -U postgres -v ON_ERROR_STOP=1 < BiletFlow_MVP_schema.sql`. Код завершения: 0.
Далее каждый запрос выполнялся через `docker exec -i biletflow-qa-20261029 psql -X -U postgres -v ON_ERROR_STOP=1 -At`, с лимитом процесса 30 секунд. Код 3 у ожидаемых отклонений означает успешную работу соответствующего ограничения.

Ниже сохранены точные запросы, коды завершения, stdout и stderr. Все значения синтетические. Для повторной загрузки SQL вместо дублирования полного исходника указан файл из зафиксированной ревизии.

### PostgreSQL version

```sql
SELECT version();
```

Код завершения: 0

stdout:
```text
PostgreSQL 17.11 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
```

stderr:
```text
```

### Table count

```sql
SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';
```

Код завершения: 0

stdout:
```text
22
```

stderr:
```text
```

### Positive fixture

```sql
INSERT INTO users(id,email,password_hash) VALUES ('00000000-0000-0000-0000-000000000001','qa@example.com','synthetic');
INSERT INTO organizer_profiles(id,user_id) VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001');
INSERT INTO events(id,organizer_id,title,starts_at,ends_at) VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002','QA event','2027-01-01','2027-01-02');
INSERT INTO ticket_types(id,event_id,name,price_kzt,quantity_total) VALUES ('00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000003','QA ticket',1000,10);
SELECT (SELECT count(*) FROM users),(SELECT count(*) FROM events),(SELECT count(*) FROM ticket_types);
```

Код завершения: 0

stdout:
```text
INSERT 0 1
INSERT 0 1
INSERT 0 1
INSERT 0 1
1|1|1
```

stderr:
```text
```

### Case-insensitive email uniqueness #1

```sql
BEGIN; INSERT INTO users(email,password_hash) VALUES ('QA@EXAMPLE.COM','synthetic'); SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  duplicate key value violates unique constraint "users_email_key"
DETAIL:  Key (email)=(QA@EXAMPLE.COM) already exists.
```

### Case-insensitive email uniqueness #2

```sql
BEGIN; INSERT INTO users(email,password_hash) VALUES ('QA@EXAMPLE.COM','synthetic'); SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  duplicate key value violates unique constraint "users_email_key"
DETAIL:  Key (email)=(QA@EXAMPLE.COM) already exists.
```

### Foreign key #1

```sql
BEGIN; INSERT INTO ticket_types(event_id,name,quantity_total) VALUES ('00000000-0000-0000-0000-000000000099','orphan',10); SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  insert or update on table "ticket_types" violates foreign key constraint "ticket_types_event_id_fkey"
DETAIL:  Key (event_id)=(00000000-0000-0000-0000-000000000099) is not present in table "events".
```

### Foreign key #2

```sql
BEGIN; INSERT INTO ticket_types(event_id,name,quantity_total) VALUES ('00000000-0000-0000-0000-000000000099','orphan',10); SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  insert or update on table "ticket_types" violates foreign key constraint "ticket_types_event_id_fkey"
DETAIL:  Key (event_id)=(00000000-0000-0000-0000-000000000099) is not present in table "events".
```

### Overselling #1

```sql
BEGIN; UPDATE ticket_types SET quantity_sold=11 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  new row for relation "ticket_types" violates check constraint "ticket_types_check"
DETAIL:  Failing row contains (00000000-0000-0000-0000-000000000004, 00000000-0000-0000-0000-000000000003, QA ticket, null, f, 1000.00, 10, 11, 10, null, null, f, 2026-10-29 06:25:48.902258+00).
```

### Overselling #2

```sql
BEGIN; UPDATE ticket_types SET quantity_sold=11 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 3

stdout:
```text
BEGIN
```

stderr:
```text
ERROR:  new row for relation "ticket_types" violates check constraint "ticket_types_check"
DETAIL:  Failing row contains (00000000-0000-0000-0000-000000000004, 00000000-0000-0000-0000-000000000003, QA ticket, null, f, 1000.00, 10, 11, 10, null, null, f, 2026-10-29 06:25:48.902258+00).
```

### Negative price #1

```sql
BEGIN; UPDATE ticket_types SET price_kzt=-1 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative price #2

```sql
BEGIN; UPDATE ticket_types SET price_kzt=-1 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative sold quantity #1

```sql
BEGIN; UPDATE ticket_types SET quantity_sold=-1 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative sold quantity #2

```sql
BEGIN; UPDATE ticket_types SET quantity_sold=-1 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative total quantity #1

```sql
BEGIN; UPDATE ticket_types SET quantity_total=-1,quantity_sold=-2 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative total quantity #2

```sql
BEGIN; UPDATE ticket_types SET quantity_total=-1,quantity_sold=-2 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Nonpositive per-order limit #1

```sql
BEGIN; UPDATE ticket_types SET max_per_order=0 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Nonpositive per-order limit #2

```sql
BEGIN; UPDATE ticket_types SET max_per_order=0 WHERE id='00000000-0000-0000-0000-000000000004'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Event date order #1

```sql
BEGIN; UPDATE events SET ends_at=starts_at-interval '1 day' WHERE id='00000000-0000-0000-0000-000000000003'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Event date order #2

```sql
BEGIN; UPDATE events SET ends_at=starts_at-interval '1 day' WHERE id='00000000-0000-0000-0000-000000000003'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative event capacity #1

```sql
BEGIN; UPDATE events SET capacity=-1 WHERE id='00000000-0000-0000-0000-000000000003'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Negative event capacity #2

```sql
BEGIN; UPDATE events SET capacity=-1 WHERE id='00000000-0000-0000-0000-000000000003'; SELECT 'accepted'; ROLLBACK;
```

Код завершения: 0

stdout:
```text
BEGIN
UPDATE 1
accepted
ROLLBACK
```

stderr:
```text
```

### Fixture remains intact

```sql
SELECT price_kzt,quantity_total,quantity_sold,max_per_order FROM ticket_types; SELECT capacity,ends_at>starts_at FROM events;
```

Код завершения: 0

stdout:
```text
1000.00|10|0|10
|t
```

stderr:
```text
```

### SQL schema repeated load

```sql
-- Полное содержимое BiletFlow_MVP_schema.sql указанной ревизии
```

Код завершения: 3

stdout:
```text
CREATE EXTENSION
CREATE EXTENSION
```

stderr:
```text
NOTICE:  extension "pgcrypto" already exists, skipping
NOTICE:  extension "citext" already exists, skipping
ERROR:  type "user_role" already exists
```
