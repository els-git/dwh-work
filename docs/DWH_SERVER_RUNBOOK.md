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
6. nginx: шаблон `/opt/dwh/current/src/deploy/nginx/dwh.conf` скопировать в `/etc/nginx/sites-available/dwh.conf`, вписать DNS-имя, включить. Пока нет сертификата УЦ — временный самоподписанный (браузер предупредит), потом заменить файлы:
   ```bash
   sudo openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes -keyout /etc/dwh/tls/dwh.key -out /etc/dwh/tls/dwh.crt -subj "/CN=app-dwh-srv" -addext "subjectAltName=IP:10.1.20.20,DNS:app-dwh-srv" && sudo chmod 600 /etc/dwh/tls/dwh.key
   ```
   ```bash
   sudo ln -s /etc/nginx/sites-available/dwh.conf /etc/nginx/sites-enabled/dwh.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```
7. Перезапуск служб после настроек и ключей:
   ```bash
   sudo systemctl daemon-reload && sudo systemctl restart dwh-api dwh-dispatcher
   ```

## 3a. Вход на сервер (I13-12)

**С 13.11.2026 (решение пользователя) вход по SSH снова разрешён и по паролю, и по ключу** — до выхода в промышленную эксплуатацию (задача I13-12 `DEFERRED`). Включить «только по ключу» снова: файл `/etc/ssh/sshd_config.d/10-dwh-keys-only.conf` с `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, затем `sudo sshd -t && sudo systemctl reload ssh`.
- Ключ администратора — на ПК (`C:\Users\elshad.asgarov\.ssh\id_ed25519`, с passphrase); копия хранится вне ПК, рядом с копиями `.dwhkey`.
- Новый администратор: его открытый ключ добавляется в `~/.ssh/authorized_keys` нужного пользователя (права `600`, папка `.ssh` — `700`).
- При потере всех ключей — консоль виртуальной машины у администраторов; вернуть вход по паролю: `sudo rm /etc/ssh/sshd_config.d/10-dwh-keys-only.conf && sudo systemctl reload ssh`.

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

## 4a. SQL-пакеты (с пакета 016)

Файлы пакета приходят с выпуском (`/opt/dwh/current/src/database`), SHA256 сверяются с `tools/server/sql_packages.json`. Каждый этап — только с разрешения пользователя.

E1 — на копии `dwh` (рабочая база только читается; копия остаётся с пакетом):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_sql_package.py --package 016 --stage e1
```
Порядок: состояние `dwh` до пакета (check предыдущего пакета — все PASS) → `pg_dump` → копия → пакет одной транзакцией → check и тест → откат пакета → пакет снова → check. Итог — `E1_RESULT: PASS`.

E2 — на рабочей `dwh`:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_sql_package.py --package 016 --stage e2
```
Порядок: состояние `dwh` → копия `/backup/dwh` с меткой `pre-016` → число строк → ввод `APPLY` → пакет одной транзакцией (`lock_timeout` 10 с) → check → число строк не изменилось, кроме добавленных пакетом. Итог — `E2_RESULT: PASS`. Отчёт этапа — `/srv/dwh/incoming/<этап>_<пакет>_<время>.txt`.

**Откат вручную** на установленный выпуск (например, если ошибка проявилась уже после `PASS`). Команду берут из выпуска, в котором она есть (с `2026.11.2`):
```bash
sudo python3 /opt/dwh/releases/<новый выпуск>/src/tools/server/dwh_update.py --rollback-to <прежний выпуск>
```
Переключает `/opt/dwh/current`, ставит файлы служб того выпуска, перезапускает службы и проверяет `/api/health`. Результат — `ROLLBACK_RESULT: PASS`; если выпуск не запустился, возвращается текущий и выводится `FAIL`. База не меняется: копия перед каждым обновлением лежит в `/backup/dwh` с меткой `pre-update-<версия>`.

## 4b. ИИ-помощник: служба модели (I17)

Модель — служба `dwh-llm`:
- llama.cpp `llama-server`, слушает только `127.0.0.1:8081`, сетевые соединения кроме localhost запрещены;
- пользователь создаётся systemd на время работы;
- до 26 ГБ памяти; загрузка после запуска — до 15 минут.

Обновление выпуска её не перезапускает. Настройки модели — в `/etc/dwh/llm.env`.

Установка и проверка — `tools/server/dwh_ai_setup.py`:
- `/etc/dwh/llm.env` и служба `dwh-llm`;
- `kernel.numa_balancing = 0`;
- память PostgreSQL рядом с моделью (`shared_buffers` 2 ГБ), с перезапуском PostgreSQL;
- случайный пароль `dwh_ai_ro` — только в `/etc/dwh/dwh.env`, на экран не выводится;
- перезапуск `dwh-api`, `dwh-dispatcher`.

Без `--apply` инструмент только показывает план:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py
```
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py --apply
```
Итог — `AI_SETUP_RESULT: PASS`: модель отвечает, `dwh_ai_ro` входит только на чтение и видит только `dm`.

Проверка без изменений:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ai_setup.py --check
```
Состояние и журнал службы:
```bash
systemctl status dwh-llm
```
```bash
sudo journalctl -u dwh-llm -n 50 --no-pager
```

Смена модели:
1. Изменить `LLM_MODEL` / `LLM_MMPROJ` в `/etc/dwh/llm.env`, затем:
   ```bash
   sudo systemctl restart dwh-llm
   ```
2. Название модели для журнала — `DWH_AI_MODEL_LABEL` в `/etc/dwh/dwh.env`.

Новый пароль `dwh_ai_ro`: `dwh_ai_setup.py --apply --rotate-password`.

## 4c. Витрины по SQL-запросу: роль `dwh_dm_sql_ro` (I23)

Запросы витрин по SQL выполняет роль `dwh_dm_sql_ro` (пакет 023: только `SELECT` на `ods`, транзакции только на чтение). Вход ей настраивает `tools/server/dwh_dm_sql_setup.py`:
- Q1 — случайный пароль только в `/etc/dwh/dwh.env` (`DWH_DM_SQL_DB_PASSWORD`), на экран не выводится; перезапуск `dwh-api`, `dwh-dispatcher`;
- Q2 — `GRANT CONNECT ON DATABASE dwh` (в `dwh` у PUBLIC нет `CONNECT`).

Без `--apply` инструмент только показывает план:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_dm_sql_setup.py
```
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_dm_sql_setup.py --apply
```
Итог — `DM_SQL_SETUP_RESULT: PASS`: роль входит только на чтение, читает таблицу `ods`, отказ на `app` и на запись в `ods`. Проверка без изменений — `--check`, новый пароль — `--apply --rotate-password`.

## 4d. Вход через домен (I20)

Вход пользователей учётной записью домена настраивает `tools/server/dwh_ldap_setup.py` (план `docs/DWH_I20_LDAP_LOGIN_PLAN_APPROVED_20261119.md`):
- P1 — параметры в `/etc/dwh/dwh.env` (`DWH_LDAP_*`): контроллеры (первый — основной), отпечатки SHA-256 их сертификатов, папки (OU), домен, логин служебной учётной записи (спрашивается при первом запуске);
- P2 — пароль служебной учётной записи: вводится без показа, шифруется `systemd-creds` в `/etc/dwh/ldap/dwh-ldap-bind.cred`, открытым текстом нигде не хранится;
- P3 — drop-in `/etc/systemd/system/dwh-api.service.d/20-ldap.conf` (пароль получает только `dwh-api`);
- P4 — перезапуск `dwh-api`.
Затем проверка каждого контроллера (шифрование, отпечаток, вход служебной учётной записи, поиск в папках). Вход через домен включается (`DWH_LDAP_ENABLED=true`) только после `PASS`.

Без `--apply` — только план. Имена контроллеров, порт, отпечатки и папки — ваши (в документы не пишутся):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ldap_setup.py --server <контроллер 1>:<порт> --server <контроллер 2>:<порт> --pin <SHA-256 контроллера 1> --pin <SHA-256 контроллера 2> --base-dn "<OU 1>" --base-dn "<OU 2>" --domain vtbaze.local --netbios VTBAZE
```
Те же параметры с `--apply` — применить. Проверка без изменений (с `--user <логин>` — найти пользователя в домене без пароля):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ldap_setup.py --check --user <логин>
```
Выключить вход через домен (локальный вход не меняется): `--disable`. Новый логин/пароль служебной учётной записи: `--apply --rotate-password` с теми же параметрами. Причины отказов входа — в журнале безопасности (`LOGIN_FAILED`: `INVALID_CREDENTIALS`, `LDAP_ACCOUNT_DISABLED`, `LDAP_ACCOUNT_LOCKED`, `LDAP_PASSWORD_EXPIRED`, `LDAP_NOT_FOUND`, `LDAP_GUID_MISMATCH`, `LDAP_UNAVAILABLE`), подробности недоступности — в журнале службы: `journalctl -u dwh-api`.

## 5. Проверки и задачи

- Сеть (шаг 5), только чтение:
  ```bash
  sudo python3 /opt/dwh/current/src/tools/server/dwh_check_network.py --name <DNS-имя>
  ```
  Результат — `NETWORK_RESULT`.
- Задачи ETL вручную (аналог `run_etl.ps1`, от пользователя `dwh` с ключами служб):
  ```bash
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py check-dwh
  sudo python3 /opt/dwh/current/src/tools/server/dwh_task.py run-object --object-id 16
  ```
  Команды старого контура CRM (`check-crm`, снимки, `validate/transform/refresh`, DQ) удалены в I18: CRM загружается как объекты.
- Копии базы:
  - список: `sudo ls -l /backup/dwh`;
  - копия вручную: `sudo systemctl start dwh-backup`;
  - журнал: `journalctl -u dwh-backup`.
- Проверочное восстановление (раз в месяц; создаёт и удаляет `dwh_restore_test`):
  ```bash
  sudo -u postgres python3 /opt/dwh/current/src/tools/server/dwh_backup.py --verify-restore /backup/dwh/<файл>.dump
  ```
- Журналы служб: `journalctl -u dwh-api -n 200`, `journalctl -u dwh-dispatcher -n 200`.
