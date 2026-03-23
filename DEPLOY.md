# Развёртывание Gaveron на VPS

## Первый раз (полная установка)

```bash
# 1. Подключаемся на VPS
ssh root@185.221.160.175

# 2. Клонируем репозиторий
git clone https://github.com/unidel2035/gaveron.git /opt/gaveron
cd /opt/gaveron

# 3. Создаём virtual environment
python3 -m venv venv

# 4. Устанавливаем зависимости
./venv/bin/pip install aiohttp pyyaml

# 5. Создаём конфиг (скопировать из примера)
cp config/gaveron.yaml /opt/gaveron/config/gaveron.yaml

# 6. Обновляем конфиг если нужно
# Отредактировать /opt/gaveron/config/gaveron.yaml:
# - http_port: 8095
# - feed_type: beast
# - history_dir: /run/gaveron

# 7. Создаём папку для истории
mkdir -p /run/gaveron
chmod 755 /run/gaveron

# 8. Копируем systemd сервис
cp systemd/gaveron.service /etc/systemd/system/

# 9. Обновляем systemd (важно: указываем --config!)
# Отредактировать /etc/systemd/system/gaveron.service:
# ExecStart=/opt/gaveron/venv/bin/python3 -m gaveron --config /opt/gaveron/config/gaveron.yaml

# 10. Перезагружаем systemd и стартуем
systemctl daemon-reload
systemctl enable gaveron
systemctl start gaveron

# 11. Проверяем статус
systemctl status gaveron
```

## Обновления (каждый раз когда новый коммит на GitHub main)

```bash
# На локальном компе
git push origin main

# На VPS (автоматически или вручную)
cd /opt/gaveron
git pull origin main
systemctl restart gaveron

# Проверяем
curl http://localhost:8095/
systemctl status gaveron
```

## Проверка что всё работает

```bash
# На VPS локально
curl http://localhost:8095/ | head -20

# Или с внешнего IP
curl http://185.221.160.175:8095/ | head -20

# Смотрим логи
journalctl -u gaveron -f
```

## Конфигурация

Основной файл: `/opt/gaveron/config/gaveron.yaml`

Важные параметры:
- `feed_type`: beast, sbs, или json_file
- `feed_host`: адрес источника ADS-B данных
- `feed_port`: порт источника (30005 для beast, 30003 для sbs)
- `http_port`: порт веб-интерфейса (8095)
- `http_host`: 0.0.0.0 (слушаем все интерфейсы)
- `history_dir`: где сохранять историю (/run/gaveron)
- `receiver_lat / receiver_lon`: координаты приёмника

## Systemd

Сервис включен и будет автоматически запускаться при перезагрузке.

```bash
# Управление
systemctl start gaveron
systemctl stop gaveron
systemctl restart gaveron
systemctl status gaveron
systemctl enable gaveron     # Автозапуск
systemctl disable gaveron    # Отключить автозапуск

# Логи
journalctl -u gaveron -f          # Live логи
journalctl -u gaveron -n 50       # Последние 50 строк
journalctl -u gaveron --since 1h  # За последний час
```

## Troubleshooting

### Сервис не стартует
```bash
journalctl -u gaveron -n 30
# Смотрим ошибку и исправляем конфиг
```

### Порт занят
```bash
ss -tlnp | grep 8095
# Если другой процесс - убиваем его
kill PID
```

### Нет данных самолётов
```bash
# Проверяем что source (readsb/dump1090) работает
nc -zv 127.0.0.1 30005
# Если не отвечает - запустить readsb на Raspberry Pi
```

### Конфиг не применяется
```bash
# Проверяем что в systemd указан --config
grep ExecStart /etc/systemd/system/gaveron.service

# Если не указан:
systemctl edit gaveron
# И добавить в ExecStart: --config /opt/gaveron/config/gaveron.yaml

systemctl daemon-reload
systemctl restart gaveron
```

## Версии

- **main** — последняя рабочая версия (всегда сюда развёртываем)
- **Конкретный коммит** — если нужна старая версия:
  ```bash
  git checkout COMMIT_HASH
  systemctl restart gaveron
  ```
