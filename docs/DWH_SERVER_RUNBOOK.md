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
6. nginx: шаблон `/opt/dwh/current/src/deploy/nginx/dwh.conf` скопировать в `/etc/nginx/sites-available/dwh.conf`, вписать DNS-имя, включить. Пока нет сертификата УЦ — временный самоподписанный (браузер предупредит); сертификат УЦ ставит `dwh_tls.py` (раздел 4e):
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
- один слот (`-np 1`, с 25.11.2026): помощник отправляет модели один запрос за раз; без него `llama-server` держал 4 слота по `LLM_CONTEXT` токенов, служба упиралась в 26 ГБ и уходила в подкачку. Проверка после перезапуска — в журнале строка `n_slots = 1`, а `VmSwap` процесса близок к нулю:
  ```bash
  sudo journalctl -u dwh-llm -b --no-pager | grep -E "n_slots|n_ctx" | tail -3; grep VmSwap /proc/$(systemctl show -p MainPID --value dwh-llm)/status
  ```

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

Логин служебной учётной записи вводите как `user@vtbaze.local` (вид `VTBAZE\user` инструмент переводит сам: systemd убирает обратную косую черту из `dwh.env`). `--base-dn` — полный путь папки (DN) вида `OU=…,OU=…,DC=vtbaze,DC=local`, его показывает на ПК в домене `whoami /fqdn` (без первой части `CN=…,`) или `([adsisearcher]"(&(objectCategory=organizationalUnit)(name=<папка>))").FindAll()`.

Без `--apply` — только план. Имена контроллеров, порт, отпечатки и папки — ваши (в документы не пишутся):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ldap_setup.py --server <контроллер 1>:<порт> --server <контроллер 2>:<порт> --pin <SHA-256 контроллера 1> --pin <SHA-256 контроллера 2> --base-dn "<OU 1>" --base-dn "<OU 2>" --domain vtbaze.local --netbios VTBAZE
```
Те же параметры с `--apply` — применить. Проверка без изменений (с `--user <логин>` — найти пользователя в домене без пароля):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_ldap_setup.py --check --user <логин>
```
Выключить вход через домен (локальный вход не меняется): `--disable`. Новый логин/пароль служебной учётной записи: `--apply --rotate-password` с теми же параметрами. Причины отказов входа — в журнале безопасности (`LOGIN_FAILED`: `INVALID_CREDENTIALS`, `LDAP_ACCOUNT_DISABLED`, `LDAP_ACCOUNT_LOCKED`, `LDAP_PASSWORD_EXPIRED`, `LDAP_NOT_FOUND`, `LDAP_GUID_MISMATCH`, `LDAP_UNAVAILABLE`), подробности недоступности — в журнале службы: `journalctl -u dwh-api`.

## 4e. Сертификат HTTPS от УЦ и DNS-имя (I13-10)

Сертификат внутреннего УЦ в файле PFX (от администраторов) ставит `tools/server/dwh_tls.py` (план `docs/DWH_I13_10_TLS_CERTIFICATE_PLAN_APPROVED_20261120.md`). PFX — в `/etc/dwh/tls/pending/` (права 600), пароль PFX вводится без показа.

План без изменений (проверки сертификата и разница файла nginx):
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py install --pfx /etc/dwh/tls/pending/dwh.pfx --name dwh.vtbaze.local --ip 10.1.20.20
```
Те же параметры с `--apply` — установить: копия текущих файлов в `/etc/dwh/tls/backup/<время>/`, новые `dwh.crt`, `dwh.key`, `ca.crt`, файл nginx из шаблона, `nginx -t`, `reload`, живая проверка (ждёт новые процессы nginx до 20 с, `--wait`); при ошибке — возврат прежних файлов. Результат — `TLS_RESULT`. После `PASS` PFX на сервере не нужен (`sudo rm /etc/dwh/tls/pending/dwh.pfx`).

Проверка без изменений и ручной откат:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py check --name dwh.vtbaze.local
```
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py rollback
```
Любой другой адрес перенаправляется на `https://dwh.vtbaze.local`. С 20.11.2026 сервер на `10.1.57.2`, в сертификате остался прежний адрес `10.1.20.20` (решение пользователя — не перевыпускать): входить только по имени. Продление — новый PFX и тот же `install`; срок проверяет `dwh_check_network.py` (за 30 дней).

## 4f. Проверка SecretStore (I02-03)

Только чтение, ничего не исправляет: права папок ключей и паролей, привязки и файлы, незавершённые операции, права ролей базы на функции SecretStore. Журнал читается от `postgres` в транзакции только чтения; значений паролей в выводе нет.
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_check.py
```
Итог — `SECRET_CHECK_RESULT` (`WARN` — посмотреть, `FAIL` — разобрать по решению пользователя). Файлы ключей — `0600 root` (ключи до выпуска `2026.11.46` создавались `0644`: `sudo chmod 600 /etc/dwh/keyring/*.cred`).

## 4g. Приёмка SecretStore (I02-03, шаг 5)

Инструмент `dwh_secret_accept.py`; порядок — план I02-03, раздел 10.4. Маркер — синтетический пароль, не настоящий.
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py marker
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py journal
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py etl-deny
sudo python3 /opt/dwh/current/src/tools/server/dwh_backup.py --secrets --dir /backup/dwh/secrets --keep 14
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py backup-check
sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py scan
```
Итог каждой команды — `SECRET_ACCEPT_<КОМАНДА>_RESULT`. `etl-deny` добавляет в журнал две записи `DENIED` — это и есть проверка. `scan` выгружает базу в канал (на диск ничего не пишет) и печатает только число совпадений.

## 4h. Тестовый API-сервис PROCESS_VIEWER (I11-05)

Только для проверки источника API: служба `dwh-test-api` (`127.0.0.1:8090`) читает `public.services` базы `process_viewer` учётной записью только на чтение; снаружи — `https://dwh.vtbaze.local/pv-api/`.

```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py            # план
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --apply    # пароль вводится скрыто; затем проверка
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --check
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --show-token   # токен для формы объекта в DWH
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_api_setup.py --remove   # остановить и закрыть /pv-api/
```

Итог — `TEST_API_RESULT`. Настройки, пароль и токен — только в `/etc/dwh/test-api.env` (root, 0600). Служба не входит в `dwh_update.py`: после обновления кода — `sudo systemctl restart dwh-test-api`.

## 4i. Адреса источников API (I11)

Загрузка и «Проверить запрос» обращаются только к хостам из списка `DWH_HTTP_ALLOWED_HOSTS` в `/etc/dwh/dwh.env`:

```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py                           # показать
sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py --add dwh.vtbaze.local    # добавить, перезапуск dwh-api и dwh-dispatcher
sudo python3 /opt/dwh/current/src/tools/server/dwh_http_hosts.py --remove dwh.vtbaze.local
```

Итог — `HTTP_HOSTS_RESULT`. Сертификаты проверяются с корнем `/etc/dwh/tls/ca.crt`.

## 4j. Файлы JSON/XML (I12)

DWH читает файлы файлового источника из `/srv/dwh/files/<код источника>` и ничего там не пишет. Папки создаёт администратор (`root:dwh`, `0750`; файлы `0640`): службы `dwh` их только читают (`ProtectSystem=strict` оставляет `/srv` доступным на чтение). Корень и предел размера можно сменить в `/etc/dwh/dwh.env` (`DWH_FILE_ROOT`, `DWH_FILE_MAX_BYTES`, по умолчанию 200 МБ), затем перезапустить `dwh-api` и `dwh-dispatcher`.

Тестовые файлы `PV_FILES` (I12-07) берут строки `public.services` у тестового API-сервиса (раздел 4h, он должен работать):

```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py              # план
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --apply      # папка и базовый набор
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --add next   # ещё файл: next / late / dup / nodate / broken / dtd / big
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --check      # список файлов
sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --remove     # удалить файлы svc_*
```

Итог — `TEST_FILES_RESULT`. Базовый набор: `svc_full_1..3.json` (для режима «самый новый»), `svc_inc_1..3.json` (для «все новые»), `svc_xml_1..2.xml`, `svc_jsonl_1.jsonl`; дата актуальности — внутри файла (`meta.actual_at`, `export.header.actual_at`, `actual_at` в первой строке).

## 4k. Проверка сервера для приёмки v1 (I14)

Только чтение, ничего не меняет:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_v1_check.py
```
Службы, текущий выпуск, свежесть и SHA-256 последней копии, `dwh_tls.py check` и `dwh_secret_check.py`, применение пакетов 016–038 и check последних пакетов под `dwh_app`, зависшие заявки и запуски (дольше 2 ч), опоздавшие расписания (больше 30 мин); для сведения — объекты по итогу последней заявки, ошибки за 7 дней по видам, события аудита за сутки. Итог — `V1_CHECK_RESULT`, отчёт — `/srv/dwh/incoming/v1_check_<время>.txt`. Свежая заявка в работе — `WARN`, не ошибка. Если DNS-имя другое — `--name <имя>`.

## 4l. Проверка безопасности сервера (I14-07)

Только чтение, ничего не меняет:
```bash
sudo python3 /opt/dwh/current/src/tools/server/dwh_security_check.py
```
Роли базы (Б3): рабочие роли без прав суперпользователя, у `PUBLIC` нет `CONNECT`, `CREATE` и прав на таблицы, функции `SECURITY DEFINER` с `search_path`, `dwh_ai_ro` читает только `dm`, `dwh_dm_sql_ro` — только `ods`, ни одна рабочая роль не меняет журнал аудита и не пишет в `secret_store`; журнал аудита (Б11): защитные триггеры включены; секреты (Б7): в журнале служб за 7 дней и в отчётах `/srv/dwh/incoming` нет паролей, токенов и ключей — печатаются только счётчики и имена файлов; сервер (Б9): порты, nginx, ограничения служб, права файлов `/etc/dwh`, `/var/lib/dwh/secrets`, `/backup/dwh`, SSH. Итог — `SECURITY_CHECK_RESULT`, отчёт — `/srv/dwh/incoming/security_check_<время>.txt`. `WARN` — к сведению (например, вход по SSH по паролю — риск принят в I13-12). С выпуска с исправлениями Н3/Н7/Н8 нет CSP, лимита входа в nginx или `DWH_COOKIE_SECURE=true` — `FAIL`, порядок — раздел 4n.

## 4m. Отрицательные запросы под тестовой учётной записью (I14-07)

Нужна учётная запись **без уровня администратора** (уровень `USER`); пароль вводится в терминале и не печатается. Запросы идут прямо в API (`127.0.0.1:8000`); изменяющие запросы отправляются только в `/api/admin/*` с пустым телом и номером 0 — даже при ошибке в проверке прав ничего не изменится.
```bash
sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_negative_check.py --user <тестовый логин>
```
Флаги: `--bruteforce 10` — 10 неверных паролей, затем верный (учётная запись может заблокироваться); `--ai` — просьба к помощнику выгрузить всю схему `dm` (нужно право `AI_REPORT_RUN`, ответ модели — несколько минут). Итог — `NEGATIVE_CHECK_RESULT`, отчёт — `/srv/dwh/incoming/negative_check_<время>.txt`.

Флаг `--web https://dwh.vtbaze.local` — подбор пароля идёт через nginx (там лимит входа, Н8), и страница должна отдавать `Content-Security-Policy` (Н3); сертификат проверяется по `/etc/dwh/tls/ca.crt`.

## 4n. Защита входа и страницы (I14-07: Н7, Н8, Н3)

1. Cookie сессии только по HTTPS (Н7) — строка в `/etc/dwh/dwh.env`, затем перезапуск API (все входят заново):
   ```bash
   sudo grep -q '^DWH_COOKIE_SECURE=' /etc/dwh/dwh.env && sudo sed -i 's/^DWH_COOKIE_SECURE=.*/DWH_COOKIE_SECURE=true/' /etc/dwh/dwh.env || echo 'DWH_COOKIE_SECURE=true' | sudo tee -a /etc/dwh/dwh.env >/dev/null
   ```
   ```bash
   sudo systemctl restart dwh-api
   ```
2. Файл nginx из шаблона выпуска — лимит входа 5 попыток в минуту с адреса (+5 сразу, дальше `429` с сообщением «Too many login attempts…», Н8) и заголовок `Content-Security-Policy` (Н3). Сертификат не меняется; сначала показ изменений, затем `--apply` (копия прежних файлов в `/etc/dwh/tls/backup/<время>`, `nginx -t`, `reload`, живая проверка; при ошибке — возврат):
   ```bash
   sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py nginx --name dwh.vtbaze.local
   ```
   ```bash
   sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py nginx --name dwh.vtbaze.local --apply
   ```
   Откат — `sudo python3 /opt/dwh/current/src/tools/server/dwh_tls.py rollback`.
3. Проверки — `dwh_security_check.py` (раздел 4l) и `dwh_negative_check.py --web https://dwh.vtbaze.local --bruteforce 10` (раздел 4m).

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
  С пакета 026 (I02-03) `run-object` вручную пароль источника не получает (`CREDENTIAL_UNAVAILABLE`): загрузку объекта запускайте заявкой — «Загрузки → Объекты → Запустить».
- Копии базы:
  - список: `sudo ls -l /backup/dwh`;
  - копия вручную: `sudo systemctl start dwh-backup`;
  - журнал: `journalctl -u dwh-backup`;
  - зашифрованные файлы паролей источников (I02-03): `sudo ls -l /backup/dwh/secrets` — `secrets_<время>.tar` и `.sha256`, без мастер-ключей; восстановление файлов: `sudo tar -xpf /backup/dwh/secrets/secrets_<время>.tar -C /var/lib/dwh/secrets`, затем `dwh_secret_check.py`.
- Проверочное восстановление (раз в месяц; создаёт и удаляет `dwh_restore_test`):
  ```bash
  sudo -u postgres python3 /opt/dwh/current/src/tools/server/dwh_backup.py --verify-restore /backup/dwh/<файл>.dump
  ```
- Журналы служб: `journalctl -u dwh-api -n 200`, `journalctl -u dwh-dispatcher -n 200`.
