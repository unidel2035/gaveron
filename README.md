# Gaveron

ADS-B aircraft tracking server. Принимает данные от приёмника ADS-B (через протоколы Beast, SBS/BaseStation или файл `aircraft.json`) и предоставляет HTTP API для отображения самолётов на карте.

## Архитектура

```
[RTL-SDR антенна]
    → [readsb / dump1090] (декодер радиосигнала)
        → Beast (порт 30005) / SBS (порт 30003) / aircraft.json
            → [Gaveron Server] (этот проект)
                → HTTP API (aircraft.json, receiver.json, history chunks)
                    → Web-фронтенд (карта с самолётами)
```

## Возможности

- Приём данных по протоколу **Beast** (бинарный, порт 30005)
- Приём данных по протоколу **SBS/BaseStation** (текстовый CSV, порт 30003)
- Чтение файла **aircraft.json** от readsb/dump1090
- Полное декодирование **ADS-B** (Mode-S DF17/18):
  - Идентификация (позывной, категория ВС)
  - Координаты (CPR global decode)
  - Скорость и курс
  - Высота (барометрическая и GNSS)
  - Вертикальная скорость
- **HTTP API** совместимый с форматом tar1090
- **История** — чанки с историей для отображения треков
- **Docker** и **systemd** ready

## Быстрый старт

### Установка

```bash
pip install .
# или с поддержкой YAML конфигурации:
pip install ".[yaml]"
```

### Запуск

```bash
# Подключение к Beast-выходу readsb на localhost:30005
python -m gaveron --feed-type beast --feed-host 127.0.0.1 --feed-port 30005

# Подключение к SBS-выходу dump1090
python -m gaveron --feed-type sbs --feed-host 192.168.1.100 --feed-port 30003

# Чтение файла aircraft.json
python -m gaveron --feed-type json_file --json-path /run/readsb/aircraft.json

# С указанием координат приёмника
python -m gaveron --lat 55.7558 --lon 37.6173 --http-port 8080

# С конфигурационным файлом
python -m gaveron --config /etc/gaveron/gaveron.yaml
```

### Docker

```bash
docker-compose up -d
```

Переменные окружения для настройки:
- `GAVERON_FEED_TYPE` — `beast`, `sbs`, `json_file`
- `GAVERON_FEED_HOST` — адрес источника данных
- `GAVERON_FEED_PORT` — порт источника
- `GAVERON_HTTP_PORT` — порт HTTP сервера (по умолчанию 8080)
- `GAVERON_LAT` / `GAVERON_LON` — координаты приёмника
- `GAVERON_LOG_LEVEL` — уровень логирования

## API

| Endpoint | Описание |
|---|---|
| `GET /data/aircraft.json` | Текущие самолёты (формат tar1090) |
| `GET /data/receiver.json` | Метаданные приёмника |
| `GET /data/stats.json` | Статистика |
| `GET /chunks/chunks.json` | Индекс чанков истории |
| `GET /chunks/{filename}` | Файл чанка (gzip) |
| `GET /health` | Healthcheck |

### Формат aircraft.json

```json
{
  "now": 1711100000.0,
  "messages": 12345,
  "aircraft": [
    {
      "hex": "a1b2c3",
      "type": "adsb_icao",
      "flight": "AFL1234",
      "alt_baro": 35000,
      "gs": 450.0,
      "track": 270.5,
      "lat": 55.755800,
      "lon": 37.617300,
      "vert_rate": 0,
      "squawk": "1234",
      "messages": 500,
      "seen": 0.3,
      "seen_pos": 1.2,
      "rssi": -15.2
    }
  ]
}
```

## Systemd

```bash
sudo cp systemd/gaveron.service /etc/systemd/system/
sudo useradd -r -s /bin/false gaveron
sudo mkdir -p /etc/gaveron
sudo cp config/gaveron.yaml /etc/gaveron/
sudo systemctl daemon-reload
sudo systemctl enable --now gaveron
```

## Структура проекта

```
gaveron/
├── gaveron/
│   ├── __init__.py      # Версия
│   ├── __main__.py      # Entry point (CLI)
│   ├── config.py        # Конфигурация (env/yaml/cli)
│   ├── decoder.py       # Декодирование ADS-B (Beast, SBS, Mode-S)
│   ├── feed.py          # Сетевые фиды (Beast, SBS, JSON file)
│   ├── history.py       # Менеджер истории (чанки)
│   └── server.py        # HTTP сервер (aiohttp)
├── config/
│   └── gaveron.yaml     # Пример конфигурации
├── nginx/
│   └── gaveron.conf     # Nginx reverse proxy
├── systemd/
│   └── gaveron.service  # Systemd unit
├── tests/
│   ├── test_decoder.py
│   └── test_server.py
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Требования

- Python 3.10+
- aiohttp
- Источник ADS-B данных (readsb, dump1090-fa, или любой совместимый)
