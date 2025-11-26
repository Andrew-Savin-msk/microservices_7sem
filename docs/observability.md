# Observability: Логирование и Мониторинг

## Выбранные технологии

### Логирование
**Стек: Loki + Promtail + Grafana**

- **Loki** — система агрегации и хранения логов (легковесная альтернатива ELK)
- **Promtail** — агент для сбора логов из контейнеров Docker
- **Grafana** — визуализация логов (используется также для метрик)

**Почему этот выбор:**
- Легковесный стек, хорошо работает в Docker
- Простая интеграция с Docker Compose
- Grafana универсальна для логов и метрик
- Низкие требования к ресурсам для учебного проекта

### Мониторинг
**Стек: Prometheus + Grafana**

- **Prometheus** — сбор и хранение метрик
- **Grafana** — визуализация метрик через дашборды

**Почему этот выбор:**
- Стандарт индустрии для мониторинга микросервисов
- Простая интеграция с Python через `prometheus_client`
- Богатая экосистема готовых дашбордов
- Отлично работает с FastAPI

## Запуск системы

Для запуска всей системы (микросервисы + observability) выполните:

```bash
docker-compose up --build
```

Или в фоновом режиме:

```bash
docker-compose up -d --build
```

## Доступ к системам

После запуска `docker-compose up` доступны следующие интерфейсы:

- **Grafana**: http://localhost:3000 
  - Логин: `admin`
  - Пароль: `admin`
  - Дашборды автоматически импортируются при первом запуске
  
- **Prometheus**: http://localhost:9090
  - UI для просмотра метрик и выполнения запросов PromQL
  
- **Loki**: http://localhost:3100 (API)
  - REST API для доступа к логам

## Структура логов

Все микросервисы логируют в JSON-формате в stdout со следующими полями:
- `timestamp` — время события (ISO 8601)
- `level` — уровень логирования (INFO, ERROR, WARNING, DEBUG)
- `service` — название сервиса
- `message` — сообщение
- `request_id` — ID запроса (для HTTP-запросов)
- `method`, `path`, `status_code` — для HTTP-запросов

## Метрики

Каждый микросервис экспортирует метрики Prometheus на эндпоинте `/metrics`:

- `http_requests_total` — общее количество HTTP-запросов (с лейблами: method, endpoint, status_code, service)
- `http_request_duration_seconds` — гистограмма времени обработки запросов
- `http_errors_total` — количество ошибок 5xx

## Дашборды Grafana

В системе настроены два дашборда:

1. **Microservices Overview** (`services_overview.json`)
   - RPS (запросов в секунду) по сервисам
   - Процент/количество ошибок (5xx)
   - P95/P99 latency по сервисам
   - Общая статистика по запросам

2. **Logs Overview** (`logs_overview.json`)
   - Просмотр логов всех сервисов
   - Распределение по уровням логирования
   - Логи ошибок
   - HTTP-запросы

Дашборды автоматически импортируются при запуске Grafana и доступны в меню Dashboards.

## Структура файлов

```
.
├── common/
│   ├── logging_config.py      # Настройка структурированного логирования
│   └── middleware.py          # Middleware для логирования и метрик
├── logging/
│   ├── loki-config.yml         # Конфигурация Loki
│   └── promtail-config.yml     # Конфигурация Promtail
├── monitoring/
│   ├── prometheus.yml          # Конфигурация Prometheus
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/    # Автоматическая настройка источников данных
│       │   └── dashboards/     # Автоматический импорт дашбордов
│       └── dashboards/         # JSON-файлы дашбордов
└── docs/
    └── observability.md        # Эта документация
```

## Проверка работы

1. **Проверка метрик**: Откройте http://localhost:9090 и выполните запрос:
   ```
   http_requests_total
   ```

2. **Проверка логов**: В Grafana перейдите в раздел Explore, выберите источник данных Loki и выполните запрос:
   ```
   {service=~".*-service"}
   ```

3. **Проверка дашбордов**: В Grafana перейдите в Dashboards и откройте "Microservices Overview" или "Logs Overview"

## Устранение неполадок

- Если метрики не отображаются: проверьте, что сервисы запущены и доступны на портах, указанных в `prometheus.yml`
- Если логи не собираются: проверьте, что Promtail имеет доступ к Docker socket (`/var/run/docker.sock`)
- Если дашборды не импортируются: проверьте права доступа к файлам в `monitoring/grafana/dashboards/`

