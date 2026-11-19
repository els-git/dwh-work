"""I17-10: diagnosis of load errors by the AI assistant (plan I17-10, decisions R1-R4, section 3.1).

The server collects a dossier of facts about one error (dwh_app, read-only; the model never queries the
database): the error itself (code, SQLSTATE, step, column, rule, rows, values and keys), the history of the
object's runs, the published STG / ODS settings of the column and the key, the structure drift state, the
connection of the source and, for a mart, its definition. Together with the knowledge base of causes by code
(KNOWLEDGE) it goes to the model, which writes: what happened, where, why (citing facts), what to do, how to
check. The card (card()) is built without the model and shown at once.

Every section of the dossier is read under its own savepoint: a fact the role cannot read is reported as
"not available" instead of failing the whole diagnosis. The mapping and the connection are read through the
functions of their screens (app.integration_object_config_get / _ods_config_get / _source_connection_get), which
check the administrator session of the user. No password or credential is read: the connection section takes
only kind, status, host, port, database and the result of the last check.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg

MAX_TEXT = 1500
HISTORY_RUNS = 8


@dataclass(frozen=True)
class Cause:
    """One code of the knowledge base: short card texts (ru / en / az) and the expert notes for the model."""
    title: dict[str, str]
    action: dict[str, str]
    notes: str
    who: str = "USER"  # USER (in the interface), SOURCE_OWNER, ADMIN (server administrator)


def _c(title_ru: str, title_en: str, title_az: str, action_ru: str, action_en: str, action_az: str, notes: str,
       who: str = "USER") -> Cause:
    return Cause({"ru": title_ru, "en": title_en, "az": title_az}, {"ru": action_ru, "en": action_en, "az": action_az},
                 notes, who)


# Knowledge base of causes: what the code means, typical causes, how to tell them apart by the dossier, the fix.
KNOWLEDGE: dict[str, Cause] = {
    "SOURCE_CONNECT_FAILED": _c(
        "Нет подключения к источнику", "Cannot connect to the source", "Mənbəyə qoşulmaq mümkün deyil",
        "Проверьте подключение источника в «Интеграциях»", "Test the source connection in Integrations",
        "İnteqrasiyalarda mənbənin qoşulmasını yoxlayın",
        "DWH не смог открыть соединение с базой источника. Причины: хост или порт недоступен из сети сервера DWH; "
        "сервер источника не пускает адрес DWH (pg_hba.conf, «no pg_hba.conf entry»); неверный логин или пароль "
        "(28P01); база не существует (3D000). Отличить по тексту ошибки и по последней проверке подключения. "
        "Пароль меняется в «Интеграции → источник → Подключение», допуск адреса — владелец источника, сеть — "
        "администраторы.", "SOURCE_OWNER"),
    "SOURCE_NO_RESPONSE": _c(
        "Источник перестал отвечать", "The source stopped answering", "Mənbə cavab vermir",
        "Повторите запуск; если повторяется — проверка сети и нагрузки источника",
        "Run again; if it repeats, check the network and the source load",
        "Yenidən işə salın; təkrarlanarsa, şəbəkəni və mənbənin yükünü yoxlayın",
        "Соединение открыто, но ответ на запрос не пришёл за отведённое время (сторож DWH разорвал соединение). "
        "Причины: перегрузка или блокировка в источнике, потеря пакетов в сети/VPN, пул соединений источника. "
        "Если раньше этот объект грузился за секунды — сеть или источник; если таблица большая — время запроса.",
        "SOURCE_OWNER"),
    "SOURCE_NOT_READ_ONLY": _c(
        "Источник не включил режим «только чтение»", "The source did not switch to read-only",
        "Mənbə yalnız oxuma rejiminə keçmədi", "Сообщите владельцу источника", "Tell the source owner",
        "Mənbənin sahibinə bildirin",
        "Шлюз DWH перед чтением включает в сессии источника режим только чтения и проверяет его; источник отказал. "
        "Это защита DWH: без режима чтения загрузка не идёт.", "SOURCE_OWNER"),
    "SOURCE_WRITE_BLOCKED": _c(
        "Заблокирована команда записи к источнику", "A write to the source was blocked", "Mənbəyə yazma bloklandı",
        "Сообщите администратору DWH", "Tell the DWH administrator", "DWH administratoruna bildirin",
        "Шлюз DWH пропускает к источникам только SELECT; команда записи остановлена. Это ошибка настройки или кода, "
        "не данных.", "ADMIN"),
    "SOURCE_OBJECT_MISSING": _c(
        "В источнике нет таблицы", "The source table is missing", "Mənbədə cədvəl yoxdur",
        "Снимите снимок структуры и проверьте объект", "Take a structure snapshot and check the object",
        "Struktur görüntüsünü çıxarın və obyekti yoxlayın",
        "Таблица или представление источника, указанные у объекта, не найдены: переименованы, удалены или нет прав.",
        "SOURCE_OWNER"),
    "SOURCE_COLUMN_MISSING": _c(
        "В источнике нет столбца из маппинга", "A mapped column is missing in the source",
        "Mənbədə xəritədəki sütun yoxdur", "Снимок структуры, затем новая версия «Маппинга»",
        "Structure snapshot, then a new mapping version", "Struktur görüntüsü, sonra «Maппинг»in yeni versiyası",
        "Опубликованный маппинг читает столбец, которого больше нет в источнике (переименован или удалён)."),
    "SOURCE_TYPE_CHANGED": _c(
        "Изменился тип столбца в источнике", "A source column changed its type", "Mənbə sütununun tipi dəyişib",
        "Новая версия «Маппинга» с новым типом", "A new mapping version with the new type",
        "Yeni tiplə «Маппинг»in yeni versiyası",
        "Тип столбца источника отличается от записанного при публикации маппинга."),
    "SCHEMA_DRIFT": _c(
        "Структура источника изменилась", "The source structure changed", "Mənbənin strukturu dəyişib",
        "Снимок структуры, затем новая версия «Маппинга»", "Structure snapshot, then a new mapping version",
        "Struktur görüntüsü, sonra «Маппинг»in yeni versiyası",
        "Таблица источника изменилась после публикации маппинга (добавлены, удалены или изменены столбцы). "
        "Детали — в состоянии маппинга (status_details)."),
    "TARGET_DRIFT": _c(
        "Таблица DWH не совпадает с маппингом", "The DWH table does not match the mapping",
        "DWH cədvəli xəritə ilə uyğun deyil", "Опубликуйте маппинг заново", "Publish the mapping again",
        "Xəritəni yenidən dərc edin",
        "Таблица STG/ODS в DWH отличается от опубликованной версии (столбцы или типы). Публикация в «Маппинге» "
        "приводит таблицу к версии."),
    "TYPE_CONVERSION": _c(
        "Значения не подходят к типу поля", "Values do not fit the field type", "Dəyərlər sahənin tipinə uyğun deyil",
        "Исправьте строки в источнике или измените тип / преобразование поля",
        "Fix the rows in the source or change the field type / conversion",
        "Mənbədəki sətirləri düzəldin və ya sahənin tipini / çevrilməsini dəyişin",
        "Значения столбца нельзя привести к типу поля STG или к преобразованию ODS (дата из текста с форматом, "
        "число, длина varchar). В ошибке: поле, правило, сколько строк, примеры значений с количеством и ключи "
        "строк. Сравните примеры с форматом/типом: если это мусор в источнике (например, текст в поле даты) — "
        "исправить строки в источнике; если формат другой у всех строк — сменить формат или тип в маппинге; "
        "длина больше varchar(n) — увеличить n."),
    "KEY_NULL": _c(
        "Пустое значение ключа", "Empty key value", "Açarın dəyəri boşdur",
        "Заполните ключ в источнике или выберите другой ключ", "Fill the key in the source or choose another key",
        "Mənbədə açarı doldurun və ya başqa açar seçin",
        "В строках источника пуст столбец, выбранный ключом объекта; такие строки нельзя записать. Если пустых много "
        "— выбран не тот ключ (возьмите первичный ключ источника)."),
    "KEY_DUPLICATE": _c(
        "Ключ повторяется", "The key is not unique", "Açar təkrarlanır",
        "Выберите уникальный ключ в «Маппинге»", "Choose a unique key in the mapping",
        "«Маппинг»də unikal açar seçin",
        "Несколько строк источника имеют одинаковый ключ. Ключ объекта должен быть уникальным (первичный ключ "
        "или набор полей)."),
    "ROW_LIMIT": _c(
        "Превышен предел строк", "Row limit exceeded", "Sətir həddi aşılıb", "Обновите DWH до выпуска с I21",
        "Update DWH to a release with I21", "DWH-ı I21 olan buraxılışa yeniləyin",
        "Старый предел 1 000 000 строк за запуск; с I21 он снят.", "ADMIN"),
    "DISK_SPACE": _c(
        "Не хватает места на диске DWH", "Not enough disk space on DWH", "DWH diskində yer çatmır",
        "Освободите место или расширьте диск сервера", "Free space or extend the server disk",
        "Yer boşaldın və ya server diskini genişləndirin",
        "Перед загрузкой размер таблицы источника × 3 сравнивается со свободным местом раздела PostgreSQL сервера.",
        "ADMIN"),
    "LOCK_TIMEOUT": _c(
        "Таблица источника заблокирована", "The source table is locked", "Mənbə cədvəli kilidlənib",
        "Повторите позже", "Run again later", "Sonra yenidən işə salın",
        "Источник не дал прочитать таблицу за 60 с: её держит другая операция (изменение структуры, долгая "
        "транзакция).", "SOURCE_OWNER"),
    "TIMEOUT": _c(
        "Загрузка шла слишком долго", "The load took too long", "Yükləmə çox uzun çəkdi",
        "Проверьте объём и скорость источника", "Check the volume and the source speed",
        "Həcmi və mənbənin sürətini yoxlayın",
        "Заявка превысила время диспетчера (по умолчанию 3600 с без признаков хода).", "ADMIN"),
    "DISPATCHER_LOST": _c(
        "Диспетчер перезапускался", "The dispatcher was restarted", "Dispetçer yenidən başladılıb",
        "Запустите снова", "Run again", "Yenidən işə salın",
        "Заявка выполнялась, когда процесс диспетчера остановился (обновление, перезагрузка); запуск закрыт."),
    "DATABASE_ERROR": _c(
        "Ошибка базы данных", "Database error", "Verilənlər bazası xətası", "Смотрите разбор по коду SQLSTATE",
        "See the analysis by SQLSTATE", "SQLSTATE koduna görə təhlilə baxın",
        "Ошибка PostgreSQL при чтении или записи; смысл — по SQLSTATE (см. ниже).", "ADMIN"),
    "INTERNAL_ERROR": _c(
        "Внутренняя ошибка движка", "Internal engine error", "Mühərrikin daxili xətası", "Сообщите администратору DWH",
        "Tell the DWH administrator", "DWH administratoruna bildirin",
        "Неожиданная ошибка программы загрузки; данных не касается.", "ADMIN"),
    "DM_KEY_NULL": _c(
        "Пустой ключ витрины", "Empty mart key", "Vitrin açarı boşdur",
        "Исправьте данные ODS или ключ витрины", "Fix the ODS data or the mart key", "ODS məlumatlarını və ya vitrin açarını düzəldin",
        "В основной таблице витрины (или в результате SQL-запроса) есть строки с пустым ключом. Прежние строки "
        "витрины остаются."),
    "DM_KEY_NOT_UNIQUE": _c(
        "Ключ витрины повторяется", "The mart key is not unique", "Vitrin açarı təkrarlanır",
        "Сделайте ключ уникальным в источнике или запросе", "Make the key unique in the source or the query",
        "Açarı mənbədə və ya sorğuda unikal edin",
        "Значения ключа повторяются: для конструктора — в таблице ODS основного источника или «того же ключа» "
        "(соединение размножило бы строки), для SQL-витрины — в результате запроса (часто JOIN к дочерней таблице "
        "без GROUP BY). Примеры значений — в диагностике."),
    "DM_LOOKUP_NOT_UNIQUE": _c(
        "Справочник витрины не уникален", "The mart lookup is not unique", "Vitrin sorğu kitabçası unikal deyil",
        "Выберите уникальную колонку справочника", "Choose a unique lookup column", "Unikal sorğu sütunu seçin",
        "Колонка справочника, по которой соединяется таблица, содержит повторы."),
    "DM_SOURCE_MISSING": _c(
        "Нет таблицы или столбца ODS для витрины", "An ODS table or column of the mart is missing",
        "Vitrin üçün ODS cədvəli və ya sütunu yoxdur", "Новая версия витрины", "A new mart version",
        "Vitrinin yeni versiyası",
        "Таблица или столбец ODS, на которые ссылается опубликованная версия витрины, удалены или переименованы."),
    "DM_SQL_COLUMNS_CHANGED": _c(
        "Запрос витрины вернул другие колонки", "The mart query returns other columns",
        "Vitrin sorğusu başqa sütunlar qaytarır", "Новая версия витрины с исправленным запросом",
        "A new mart version with the corrected query", "Düzəldilmiş sorğu ilə vitrinin yeni versiyası",
        "Колонки результата SQL-запроса больше не совпадают с полями опубликованной версии."),
    "DM_SQL_NOT_ALLOWED": _c(
        "Запрос витрины читает запрещённое", "The mart query reads forbidden data",
        "Vitrin sorğusu qadağan olunanı oxuyur", "Читать можно только таблицы ods", "Only ods tables may be read",
        "Yalnız ods cədvəlləri oxuna bilər",
        "Роль dwh_dm_sql_ro читает только схему ods и не может писать."),
    "DM_SQL_CONNECT_FAILED": _c(
        "Роль запросов витрин не подключается", "The mart query role cannot connect",
        "Vitrin sorğu rolu qoşula bilmir", "Администратору: dwh_dm_sql_setup.py --check",
        "Administrator: dwh_dm_sql_setup.py --check", "Administrator: dwh_dm_sql_setup.py --check",
        "Нет пароля или CONNECT у dwh_dm_sql_ro.", "ADMIN"),
    "DM_SQL_NOT_CONFIGURED": _c(
        "Роль запросов витрин не настроена", "The mart query role is not configured",
        "Vitrin sorğu rolu qurulmayıb", "Администратору: dwh_dm_sql_setup.py --apply",
        "Administrator: dwh_dm_sql_setup.py --apply", "Administrator: dwh_dm_sql_setup.py --apply",
        "В /etc/dwh/dwh.env нет DWH_DM_SQL_DB_PASSWORD.", "ADMIN"),
    "DM_TABLE_MISMATCH": _c(
        "Таблица витрины не совпадает с версией", "The mart table does not match its version",
        "Vitrin cədvəli versiyaya uyğun deyil", "Опубликуйте витрину заново", "Publish the mart again",
        "Vitrini yenidən dərc edin",
        "Таблица dm.<код> отличается от полей опубликованной версии."),
    "DM_DATA_ERROR": _c(
        "Данные не подходят к полям витрины", "Data do not fit the mart fields", "Məlumatlar vitrin sahələrinə uyğun deyil",
        "Проверьте типы полей витрины", "Check the mart field types", "Vitrin sahələrinin tiplərini yoxlayın",
        "Значение не приводится к типу поля витрины (длина varchar, число, дата) или нарушено ограничение."),
    "DM_TIMEOUT": _c(
        "Заполнение витрины шло слишком долго", "The mart fill took too long", "Vitrinin doldurulması çox uzun çəkdi",
        "Упростите витрину или запрос", "Simplify the mart or the query", "Vitrini və ya sorğunu sadələşdirin",
        "Запрос заполнения превысил время (DWH_DM_STATEMENT_TIMEOUT_SECONDS).", "ADMIN"),
    "DM_DEFINITION_INVALID": _c(
        "Версия витрины настроена неверно", "The mart version is invalid", "Vitrin versiyası səhv qurulub",
        "Откройте черновик и сохраните — будут показаны замечания", "Open the draft and save it to see the problems",
        "Qaralamanı açıb saxlayın — qeydlər göstəriləcək", "Определение версии нельзя превратить в SQL."),
    "DM_DATABASE_ERROR": _c(
        "Ошибка базы при заполнении витрины", "Database error while filling the mart",
        "Vitrin doldurularkən baza xətası", "Смотрите разбор по коду SQLSTATE", "See the analysis by SQLSTATE",
        "SQLSTATE koduna görə təhlilə baxın", "Ошибка PostgreSQL при заполнении витрины; смысл — по SQLSTATE.", "ADMIN"),
}

UNKNOWN = _c("Ошибка загрузки", "Load error", "Yükləmə xətası", "Смотрите разбор помощника",
             "See the assistant's analysis", "Köməkçinin təhlilinə baxın",
             "Код не описан в базе знаний: объяснять по сообщению и SQLSTATE, отметить, что нужна проверка "
             "администратором.", "ADMIN")

SQLSTATE_NOTES: dict[str, str] = {
    "22001": "значение длиннее varchar(n) поля",
    "22003": "число вне диапазона типа поля",
    "22007": "неверный формат даты/времени",
    "22008": "дата/время вне диапазона",
    "22P02": "текст не является числом / значением типа",
    "23502": "пустое значение в обязательном столбце",
    "23505": "повтор уникального ключа",
    "40P01": "взаимная блокировка, повторить запуск",
    "42501": "нет прав роли на объект",
    "42703": "столбец не существует (изменилась структура)",
    "42P01": "таблица не существует",
    "53100": "нет места на диске",
    "57014": "запрос отменён по времени или пользователем",
    "28P01": "неверный пароль учётной записи источника",
    "3D000": "база данных не существует",
}


def cause(code: str | None) -> Cause:
    return KNOWLEDGE.get((code or "").upper(), UNKNOWN)


@dataclass
class Dossier:
    kind: str                      # ERROR / MART
    code: str | None = None
    sqlstate: str | None = None
    where: list[str] = field(default_factory=list)       # source -> table -> column (identifiers only)
    facts: list[tuple[str, str]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    object_id: int | None = None
    error_id: int | None = None
    mart_code: str | None = None

    def add(self, label: str, value: Any) -> None:
        if value is None or value == "" or value == [] or value == {}:
            return
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        self.facts.append((label, text[:MAX_TEXT]))

    def text(self) -> str:
        lines = [f"- {label}: {value}" for label, value in self.facts]
        if self.unavailable:
            lines.append("- недоступно для чтения (не делать выводов): " + ", ".join(self.unavailable))
        return "\n".join(lines)


class DiagnosisError(LookupError):
    """The error / object / mart does not exist (404)."""


def _rows(cur) -> list[dict[str, Any]]:
    rows = cur.fetchall()
    if rows and not isinstance(rows[0], dict):
        names = [d.name for d in cur.description]
        return [dict(zip(names, r)) for r in rows]
    return [dict(r) for r in rows]


def _section(conn, dossier: Dossier, name: str, read: Callable[[Any], None]) -> None:
    """One part of the dossier under a savepoint: a missing right or object only marks the part unavailable."""
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                read(cur)
    except psycopg.Error:
        dossier.unavailable.append(name)


def _short(value: Any, limit: int = 300) -> Any:
    return value[:limit] if isinstance(value, str) else value


def _value(row: Any) -> Any:
    if row is None:
        return None
    value = list(row.values())[0] if isinstance(row, dict) else row[0]
    return json.loads(value) if isinstance(value, str) else value


def _object_sections(conn, d: Dossier, object_id: int, column: str | None, token_hash: str) -> None:
    system: dict[str, Any] = {}

    def obj(cur):
        # etl.v_object_load_status is readable by dwh_app (package 015); the source system id comes from the job.
        cur.execute("SELECT source_object_id, source_system_code AS source_code, source_schema, source_object, is_active, "
                    "(SELECT j.source_system_id FROM etl.job j WHERE j.source_object_id = v.source_object_id "
                    "  AND j.source_system_id IS NOT NULL LIMIT 1) AS source_system_id "
                    "FROM etl.v_object_load_status v WHERE v.source_object_id = %s", (object_id,))
        rows = _rows(cur)
        if rows:
            r = rows[0]
            system.update(r)
            d.where[:0] = [f"источник {r['source_code']}", f"таблица источника {r['source_schema']}.{r['source_object']}"]
            d.add("объект", f"№{object_id} {r['source_schema']}.{r['source_object']} (источник {r['source_code']}, "
                            f"{'включён' if r['is_active'] else 'отключён'})")
    _section(conn, d, "объект", obj)

    def history(cur):
        cur.execute("SELECT j.code, r.status, r.started_at, r.finished_at, r.rows_read, r.rows_inserted, r.rows_updated, "
                    "left(r.message, 200) AS message FROM etl.job_run r JOIN etl.job j ON j.job_id = r.job_id "
                    "WHERE j.source_object_id = %s ORDER BY r.job_run_id DESC LIMIT %s", (object_id, HISTORY_RUNS))
        runs = _rows(cur)
        if not runs:
            d.add("история запусков", "запусков ещё не было")
            return
        compact = [f"{'ODS' if r['code'].startswith('OBJECT_ODS') else 'STG'} {r['status']} "
                   f"{str(r['started_at'])[:16]} прочитано {r['rows_read']}, вставлено {r['rows_inserted']}, "
                   f"обновлено {r['rows_updated']}" for r in runs]
        d.add("последние запуски (новые сверху)", "; ".join(compact))
        failed_in_row = 0
        for r in runs:
            if r["status"] != "FAILED":
                break
            failed_in_row += 1
        d.add("неудачных запусков подряд", str(failed_in_row))
        cur.execute("SELECT max(r.finished_at) FROM etl.job_run r JOIN etl.job j ON j.job_id = r.job_id "
                    "WHERE j.source_object_id = %s AND r.status = 'SUCCESS'", (object_id,))
        last_ok = cur.fetchone()
        last_ok = list(last_ok.values())[0] if isinstance(last_ok, dict) else last_ok[0]
        d.add("последний успешный запуск", str(last_ok)[:19] if last_ok else "успешных запусков не было")
    _section(conn, d, "история запусков", history)

    def stg(cur):
        # Settings are read through the functions of the mapping screen (dwh_app has no SELECT on meta.object_config_*);
        # they check the administrator session of the user: without it the part stays "not available".
        cur.execute("SELECT app.integration_object_config_get(%s, %s) AS config", (token_hash, object_id))
        config = _value(cur.fetchone()) or {}
        current = config.get("current")
        if not current:
            d.add("маппинг STG", "опубликованной версии нет")
            return
        definition = current.get("definition") or {}
        strategy = definition.get("strategy") or {}
        d.add("маппинг STG", f"версия {current.get('version_no')}, опубликована {str(current.get('published_at'))[:16]}, "
                             f"таблица stg.{definition.get('target_table')}, стратегия "
                             f"{strategy.get('load_strategy') or 'FULL'}"
                             + (f" по {strategy['change_column']}" if strategy.get("change_column") else "")
                             + f", состояние {current.get('status')}")
        if current.get("status") != "READY":
            d.add("отличия структуры (drift)", current.get("status_details"))
        fields = definition.get("fields") or []
        keys = definition.get("key") or []
        d.add("ключ STG", ", ".join(keys) or "не задан")
        d.add("полей STG", str(len(fields)))
        wanted = (column or "").lower()
        for f in fields:
            if wanted and (f.get("target_column") == wanted or (f.get("source_column") or "").lower() == wanted):
                d.add(f"поле STG {f['target_column']}", f"из столбца источника {f.get('source_column')} (тип в "
                      f"источнике {f.get('source_type')}) → тип STG {f.get('target_type')}, приведение "
                      f"{f.get('conversion')}, запись {f.get('write_policy')}"
                      + (", ключ" if f["target_column"] in keys else ""))
    _section(conn, d, "маппинг STG", stg)

    def ods(cur):
        cur.execute("SELECT app.integration_ods_config_get(%s, %s) AS config", (token_hash, object_id))
        config = _value(cur.fetchone()) or {}
        current = config.get("current")
        if not current:
            d.add("маппинг ODS", "опубликованной версии нет")
            return
        definition = current.get("definition") or {}
        d.add("маппинг ODS", f"версия {current.get('version_no')}, опубликована {str(current.get('published_at'))[:16]}, "
                             f"таблица ods.{definition.get('target_table')}, состояние {current.get('status')}")
        if current.get("status") != "READY":
            d.add("отличия структуры ODS", current.get("status_details"))
        wanted = (column or "").lower()
        for f in definition.get("fields") or []:
            if wanted and wanted in (f.get("target_column"), f.get("stg_column")):
                d.add(f"поле ODS {f['target_column']}", f"из STG {f.get('stg_column')} ({f.get('stg_type')}) "
                      f"преобразование {f.get('transform')}" + (f" с параметром «{f['param']}»" if f.get("param") else "")
                      + f" → тип ODS {f.get('target_type')}")
    _section(conn, d, "маппинг ODS", ods)

    def connection(cur):
        if not system.get("source_system_id"):
            return
        # Only kind, state, host, port, database and the last check: never credentials.
        cur.execute("SELECT source_kind, status, connection_config ->> 'host' AS host, connection_config ->> 'port' AS port, "
                    "coalesce(connection_config ->> 'database', connection_config ->> 'dbname') AS database, "
                    "last_test_ok, last_test_at, last_test_code "
                    "FROM app.integration_source_connection_get(%s, %s)", (token_hash, system["source_system_id"]))
        rows = _rows(cur)
        if not rows:
            d.add("подключение источника", "не настроено")
            return
        c = rows[0]
        test = ("не проверялось" if c["last_test_at"] is None else
                f"последняя проверка {str(c['last_test_at'])[:16]}: {'успешно' if c['last_test_ok'] else 'ошибка'}"
                + (f" ({c['last_test_code']})" if c["last_test_code"] else ""))
        d.add("подключение источника", f"{c['source_kind']} {c['host'] or ''}:{c['port'] or ''}/{c['database'] or ''}, "
                                       f"состояние {c['status']}, {test}")
    _section(conn, d, "подключение", connection)


def error_dossier(conn, token_hash: str, error_id: int | None = None, object_id: int | None = None) -> Dossier:
    """Dossier of one error (by its number, or the last error of an object)."""
    d = Dossier("ERROR")
    found: dict[str, Any] = {}

    def error(cur):
        if error_id is not None:
            cur.execute("SELECT * FROM etl.v_integration_error WHERE error_id = %s", (error_id,))
        else:
            cur.execute("SELECT * FROM etl.v_integration_error WHERE source_object_id = %s "
                        "ORDER BY error_id DESC LIMIT 1", (object_id,))
        rows = _rows(cur)
        if rows:
            found.update(rows[0])
        if found.get("job_id"):
            cur.execute("SELECT code FROM etl.job WHERE job_id = %s", (found["job_id"],))
            job = _rows(cur)
            found["job_code"] = job[0]["code"] if job else None
    _section(conn, d, "ошибка", error)
    if not found:
        raise DiagnosisError("error not found")
    e = found
    d.error_id = int(e["error_id"])
    d.code = e.get("error_code")
    d.sqlstate = e.get("sql_state")
    d.object_id = e.get("source_object_id")
    stage = "ODS" if (e.get("job_code") or "").startswith("OBJECT_ODS") or (e.get("error_stage") or "").upper().startswith("ODS") \
        else "STG"
    d.add("ошибка", f"№{e['error_id']} {str(e['occurred_at'])[:19]}, шаг {stage} ({e.get('error_stage') or ''}), "
                    f"код {e.get('error_code')}" + (f", SQLSTATE {e['sql_state']}" if e.get("sql_state") else ""))
    d.add("сообщение", e.get("error_message"))
    d.add("подробности", e.get("error_detail"))
    if e.get("column_name"):
        d.add("поле", e["column_name"])
    d.add("правило / ожидалось", e.get("expected_value"))
    d.add("неверные значения", e.get("bad_value"))
    d.add("ключ строки", e.get("record_key") or e.get("source_pk"))
    d.add("диагностика движка (строки, значения с количеством, ключи, порция)", e.get("diagnostic_context"))
    if d.object_id:
        _object_sections(conn, d, int(d.object_id), e.get("column_name"), token_hash)
    d.where.append(f"шаг {stage}")
    if e.get("column_name"):
        d.where.append(f"столбец {e['column_name']}")
    return d


def mart_dossier(conn, mart_id: int) -> Dossier:
    """Dossier of the last failed run of a mart."""
    d = Dossier("MART")
    status: dict[str, Any] = {}

    def state(cur):
        cur.execute("SELECT * FROM etl.v_dm_mart_status WHERE mart_id = %s", (mart_id,))
        rows = _rows(cur)
        if rows:
            status.update(rows[0])
    _section(conn, d, "состояние витрины", state)
    if not status:
        raise DiagnosisError("mart not found")
    s = status
    d.mart_code = s["mart_code"]
    d.code = s.get("last_run_error_code")
    d.where = [f"витрина dm.{s['mart_code']}"]
    d.add("витрина", f"dm.{s['mart_code']} «{s.get('title')}», опубликована версия {s.get('published_version_no')}, "
                     f"таблица {'есть' if s.get('table_exists') else 'нет'}")
    d.add("последний запуск", f"{s.get('last_run_status')} {str(s.get('last_run_started_at'))[:16]}, строк "
                              f"{s.get('last_run_rows')}")
    d.add("код ошибки", s.get("last_run_error_code"))
    d.add("сообщение", s.get("last_run_error_message"))
    d.add("диагностика движка (примеры ключей, отсутствующие объекты)", s.get("last_run_diagnostic"))
    if s.get("draft_build_status") == "FAILED":
        d.add("построение черновика", s.get("draft_build_message"))
    version_id = s.get("published_version_id")

    def definition(cur):
        if not version_id:
            return
        cur.execute("SELECT to_jsonb(v) ->> 'version_kind' AS kind, to_jsonb(v) ->> 'sql_text' AS sql_text "
                    "FROM meta.dm_mart_version v WHERE version_id = %s", (version_id,))
        head = _rows(cur)
        kind = (head[0]["kind"] if head else None) or "BUILDER"
        if kind == "SQL":
            d.add("вид витрины", "готовый SQL-запрос")
            d.add("запрос витрины", _short(head[0]["sql_text"], 3000))
        else:
            d.add("вид витрины", "конструктор")
            cur.execute("SELECT alias, table_name, source_kind, key_column, ref_alias, ref_column FROM meta.dm_mart_source "
                        "WHERE version_id = %s ORDER BY position", (version_id,))
            d.add("источники витрины", [f"{r['alias']}: ods.{r['table_name']} {r['source_kind']} ключ {r['key_column']}"
                                        + (f" ← {r['ref_alias']}.{r['ref_column']}" if r["ref_alias"] else "")
                                        for r in _rows(cur)])
        cur.execute("SELECT field_name, data_type, is_key FROM meta.dm_mart_field WHERE version_id = %s ORDER BY position",
                    (version_id,))
        fields = _rows(cur)
        d.add("ключ витрины", ", ".join(f["field_name"] for f in fields if f["is_key"]))
        d.add("поля витрины", ", ".join(f"{f['field_name']} {f['data_type']}" for f in fields[:60]))
    _section(conn, d, "определение витрины", definition)
    return d


DIAG_SYSTEM = """Ты — инженер сопровождения хранилища данных DWH. Тебе дают ДОСЬЕ фактов об одной ошибке загрузки
и ЗАМЕТКИ по её коду. Поставь диагноз по существу, как опытный инженер, а не пересказ сообщения.

Правила:
- Опирайся только на факты досье. Для каждого вывода укажи, на каком факте он основан (поле, значения, история,
  маппинг, подключение). Если фактов не хватает — так и скажи и назови, что проверить. Ничего не выдумывай:
  не называй таблицы, столбцы и значения, которых нет в досье.
- Называй объекты точно: источник, таблица, столбец, шаг (STG/ODS/витрина), ключи и примеры строк из досье.
- Сравни историю: «раньше работало, сломалось после …» или «не работало ни разу».
- Шаги исправления — конкретные: какой экран DWH («Загрузки → Объекты», «Интеграции → источник → Маппинг»,
  «Администрирование → Витрины DM»), что изменить, кто это делает (пользователь DWH, владелец источника,
  администратор сервера). Без SQL-команд изменения данных.
- Отвечай на языке: {language}. Кратко и по делу, с разделами:
  **Что случилось**, **Где**, **Почему**, **Что сделать**, **Как проверить**."""

LANGUAGES = {"ru": "русский", "en": "English", "az": "Azərbaycan dili"}


def prompt(d: Dossier, language: str, question: str | None = None) -> tuple[str, str]:
    c = cause(d.code)
    notes = [f"Код {d.code or '—'}: {c.notes}", f"Кто обычно исправляет: {c.who}"]
    if d.sqlstate and d.sqlstate in SQLSTATE_NOTES:
        notes.append(f"SQLSTATE {d.sqlstate}: {SQLSTATE_NOTES[d.sqlstate]}")
    user = ("ДОСЬЕ:\n" + d.text() + "\n\nЗАМЕТКИ ПО КОДУ:\n" + "\n".join(notes)
            + (f"\n\nВОПРОС ПОЛЬЗОВАТЕЛЯ: {question.strip()[:1000]}" if question and question.strip() else ""))
    return DIAG_SYSTEM.replace("{language}", LANGUAGES.get(language, LANGUAGES["ru"])), user


def card(d: Dossier, language: str) -> dict[str, Any]:
    """What is shown at once, without the model."""
    c = cause(d.code)
    lang = language if language in ("ru", "en", "az") else "ru"
    return {"code": d.code, "sqlstate": d.sqlstate, "title": c.title[lang], "action": c.action[lang], "who": c.who,
            "where": [w for w in d.where if w], "error_id": d.error_id, "object_id": d.object_id, "mart_code": d.mart_code}
