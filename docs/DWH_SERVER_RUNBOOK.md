# DWH на сервере Debian 13 — инструкция (I16)

Основа — утверждённый план `docs/DWH_I16_SERVER_MIGRATION_PLAN_APPROVED_20261112.md`: сервер подготовлен по чек-листу, раздел 4.

Команды для сервера — bash, выполняются через SSH от администратора с `sudo`. Команды для ПК — PowerShell.

## 1. Как устроено на сервере

| Что | Где |
|---|---|
| Выпуски (код, окружение Python, собранный frontend) | `/opt/dwh/releases/<версия>`; действующий — ссылка `/opt/dwh/current` |
| Настройки (пароли `dwh_app`, `dwh_etl` и т. п.) | `/etc/dwh/dwh.env` (`root:dwh`, `0640`), шаблон — `deploy/dwh.env.example` |
| Мастер-ключи SecretStore | `/etc/dwh/keyring/<key_id>.cred` (`root`, `0700`); `/etc/dwh/keyring.env` и drop-in `/etc/systemd/system/dwh-.service.d/10-keyring.conf` пишет `secret_key_tool.py activate` |
| Зашифрованные учётные данные источников | `/var/lib/dwh/secrets` (`dwh`, `0700`) |
| Снимки CRM ETL и рабочие файлы (`data/`, `runtime/` кода) | `/var/lib/dwh/data`, `/var/lib/dwh/runtime` — переживают обновления |
| HTTPS-сертификат | `/etc/dwh/tls/dwh.crt`, `/etc/dwh/tls/dwh.key` |
| Копии базы | `/backup/dwh` (`postgres`, `0700`) |
| Службы | `dwh-api` (API на `127.0.0.1:8000`), `dwh-dispatcher` (заявки), таймер `dwh-backup` (копия в 02:30) |
| Вход пользователей | nginx: `https://<имя сервера>` (`/etc/nginx/sites-available/dwh.conf`, шаблон — `deploy/nginx/dwh.conf`) |

## 2. Пакет выпуска (ПК)

1. Влить PR и обновить папку проекта:
   ```powershell
   git -C "C:\Users\elshad.asgarov\Desktop\DWH\dwh" pull
   ```
2. Собрать пакет (новая версия при каждом выпуске). Нужны чистая ветка `main`, равная `origin/main`, `npm` и доступ ПК к PyPI:
   ```powershell
   & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\make_release.ps1" -Version 2026.11.1
   ```
   Результат: `runtime\releases\dwh_release_2026.11.1.zip` и `.sha256`, в конце `RELEASE_RESULT: PASS`.
3. Скопировать оба файла на сервер в `/srv/dwh/incoming/` (WinSCP / `scp`).

## 2a. Проверка подготовленного сервера (шаги 4–5 плана)

Обе проверки только читают и ничего не меняют. Скрипты копируются с ПК, потому что до первой установки DWH на сервере ещё нет.
- `tools/server/dwh_check_server.py` — сверка с чек-листом раздела 4 плана: ОС, ядра, память, диски (SSD/HDD), пользователь `dwh`, PostgreSQL 17, Python 3.13, программы, часовой пояс, порты. Результат — `SERVER_RESULT`.
- `tools/server/dwh_check_network.py` — источники, локальный PostgreSQL, время, закрытый интернет, сертификат. Результат — `NETWORK_RESULT`.

## 3. Первая установка (один раз, шаги 6–9 плана)

1. Папки и права:
   ```bash
   sudo install -d -m 0755 -o root -g root /opt/dwh /opt/dwh/releases
   sudo install -d -m 0750 -o root -g dwh /etc/dwh /etc/dwh/tls
   sudo install -d -m 0700 -o root -g root /etc/dwh/keyring
   sudo install -d -m 0700 -o dwh -g dwh /var/lib/dwh /var/lib/dwh/secrets /var/lib/dwh/data /var/lib/dwh/runtime
   sudo install -d -m 0750 -o dwh -g dwh /var/log/dwh
   sudo install -d -m 0700 -o postgres -g postgres /backup/dwh
   ```
2. Настройки: шаблон `deploy/dwh.env.example` (из репозитория или пакета) скопировать в `/etc/dwh/dwh.env`, заполнить пароли; права `root:dwh`, `0640`.
3. Проверить пакет, взять из него установщик, поставить первый выпуск. Пока нет базы и ключей, службы не будут здоровы — результат `UPDATE_RESULT: INSTALLED`, это ожидаемо:
   ```bash
   cd /srv/dwh/incoming && sha256sum -c dwh_release_2026.11.1.sha256
   unzip -o -j dwh_release_2026.11.1.zip dwh_update.py -d /root/dwh_install
   sudo python3 /root/dwh_install/dwh_update.py /srv/dwh/incoming/dwh_release_2026.11.1.zip --first-install
   ```
4. База (шаг 7).
   - На ПК `tools\export_dwh_for_server.ps1` выгружает базу из службы PostgreSQL 17 Windows (`127.0.0.1:5433`, штатные `pg_dump`/`pg_dumpall`, пароль администратора запрашивается) в `runtime\server_export\dwh_export_<время>\`:
     - роли **без паролей**;
     - копию базы (`pg_dump -Fc`);
     - сведения о базе;
     - число строк каждой таблицы;
     - `SHA256SUMS`.
   - Папка копируется на сервер в `/srv/dwh/incoming/`.
   - На сервере `sudo python3 dwh_import_db.py <папка>`:
     1. сверяет SHA256;
     2. создаёт роли; владельцу базы вход запрещён;
     3. восстанавливает базу (`pg_restore --create`); локаль Windows заменяется на `en_US.UTF-8` (`--locale`), кодировка — `UTF8`;
     4. сравнивает каждую таблицу и число объектов с ПК;
     5. задаёт **новые случайные пароли** `dwh_app` и `dwh_etl` и сам пишет их в `/etc/dwh/dwh.env` — пароли никому не показываются.
   - Повторный перенос (окончательный, перед переходом): с `--replace`, службы DWH остановлены.
5. Ключи (шаг 8): восстановление из `.dwhkey` и активация — `docs/DWH_SECRETSTORE_KEY_RUNBOOK.md`, раздел «Linux-сервер». Python для команд: `/opt/dwh/current/venv/bin/python`, скрипт `/opt/dwh/current/src/tools/secret_key_tool.py`. Зашифрованные учётные данные источников с ПК (`DWH_SECRET_STORE_DIR`) — в `/var/lib/dwh/secrets`.
6. nginx: шаблон `/opt/dwh/current/src/deploy/nginx/dwh.conf` скопировать в `/etc/nginx/sites-available/dwh.conf`, вписать DNS-имя, включить:
   ```bash
   sudo ln -s /etc/nginx/sites-available/dwh.conf /etc/nginx/sites-enabled/dwh.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```
7. Перезапуск служб после настроек и ключей:
   ```bash
   sudo systemctl daemon-reload && sudo systemctl restart dwh-api dwh-dispatcher
   ```

## 4. Обновление

```bash
cd /srv/dwh/incoming && sha256sum -c dwh_release_<версия>.sha256
sudo python3 /opt/dwh/current/src/tools/server/dwh_update.py /srv/dwh/incoming/dwh_release_<версия>.zip
```

Что делает команда:
1. сверяет SHA256 пакета и каждого файла;
2. ставит код и окружение Python из пакета, без интернета;
3. делает копию базы `pre-update-<версия>`;
4. переключает `/opt/dwh/current` и перезапускает службы — диспетчер сначала доделывает текущую заявку;
5. проверяет `/api/health` и службы.

Результаты:
- `UPDATE_RESULT: PASS` — новый выпуск работает;
- `UPDATE_RESULT: ROLLED_BACK` — новый выпуск не запустился, работает прежний;
- `UPDATE_RESULT: FAIL` — ошибка до переключения, ничего не изменено.

База обновлением не меняется: SQL-пакеты ставятся отдельно с подтверждением `APPLY`. Хранятся 3 последних выпуска.

## 5. Проверки и задачи

- Сеть (шаг 5), только чтение:
  ```bash
  sudo python3 /opt/dwh/current/src/tools/server/dwh_check_network.py --name <DNS-имя>
  ```
  Результат — `NETWORK_RESULT`.
- Задачи ETL вручную (аналог `run_etl.ps1`, от пользователя `dwh` с ключами служб):
  ```bash
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py check-crm
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py run-object --object-id 16
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py plan-snapshot --snapshot-date 2026-11-04
  ```
- Копии базы:
  - список: `sudo ls -l /backup/dwh`;
  - копия вручную: `sudo systemctl start dwh-backup`;
  - журнал: `journalctl -u dwh-backup`.
- Проверочное восстановление (раз в месяц; создаёт и удаляет `dwh_restore_test`):
  ```bash
  sudo -u postgres python3 /opt/dwh/current/src/tools/server/dwh_backup.py --verify-restore /backup/dwh/<файл>.dump
  ```
- Журналы служб: `journalctl -u dwh-api -n 200`, `journalctl -u dwh-dispatcher -n 200`.
