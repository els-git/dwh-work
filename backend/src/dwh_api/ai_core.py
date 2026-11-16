"""Shared logic of the AI assistant (I17): prompts, the description of reporting data for the model and the
checked execution of a report query. Used by the API (ai_assistant.py) and by the acceptance run of step 7
(tools/server/dwh_ai_acceptance.py), so that the acceptance measures exactly what the application does.
No FastAPI here; callers pass database cursors.
"""
from __future__ import annotations

from psycopg import sql as pgsql

from dwh_api.ai_knowledge import SearchResult
from dwh_api.ai_sql_guard import AllowedObject, allowed_closure, check_plan, limited

DENIED_TEXT = "Текущей роли эта информация недоступна."
NO_HELP_TEXT = "В справке нет ответа на этот вопрос."
MAX_ROWS = 1000

HELP_SYSTEM = (
    "Ты помощник по приложению DWH. Отвечай на языке вопроса, только по приведённым разделам справки "
    "и по снимку экрана, если он приложен. Отвечай по существу, но полно: шаги по порядку, где это находится "
    "(раздел, вкладка, кнопка — так, как в справке), что важно учесть. Если ответа в справке нет, так и скажи. "
    "Никогда не выдумывай пароли, адреса и данные. Оформление: обычный текст, можно **жирный** и списки; "
    "без LaTeX и формул, стрелку пиши символом →.")
IMAGE_NOTE = (
    "\nК вопросу приложен снимок экрана приложения DWH. Определи по снимку и карте разделов, какой это экран. "
    "Затем расскажи развёрнуто: что на нём показано (заголовок, показатели, вкладки, таблицы, кнопки — их смысл), "
    "что пользователь может здесь сделать (действия, фильтры, выгрузка), куда можно перейти дальше и в каком "
    "разделе справки об этом подробнее. Цифры со снимка не пересказывай, если о них не спрашивают.")
REPORT_SYSTEM = (
    "Ты пишешь SQL для PostgreSQL 17 по описанию отчёта. Можно использовать только объекты и колонки из описания "
    "ниже, имена объектов всегда со схемой (dm.имя), только один SELECT, без комментариев. "
    "Значения справочников бери только из перечисленных. "
    "Колонки пиши без префикса; если даёшь объекту псевдоним, обращайся к колонкам только через него. "
    "Слова «по <признаку>» (по продуктам, по каналам, по филиалам) означают группировку GROUP BY по колонке "
    "этого признака, и она выводится в результате. "
    "Верни один запрос в блоке ```sql```, без пояснений. "
    "Если для отчёта нужны данные из списка недоступных роли, верни ```sql\n-- DENIED\n```. "
    "Если нужных данных нет в описании, верни ```sql\n-- NO_DATA\n```.")


def help_prompt(found: SearchResult, question: str, screen_map: str | None = None) -> tuple[str, str]:
    """System and user messages for a help question; only the sections the user may read are included.
    With a screenshot, screen_map lists every section the user may read (title and its start) so that the
    model can recognise the screen even when the question has few words."""
    context = "\n---\n".join(s.text for s in found.sections) or "(подходящих разделов справки нет)"
    note = ("\nРазделы о вопросе недоступны роли пользователя: о них отвечай «" + DENIED_TEXT + "»."
            if found.denied_titles else "")
    if screen_map is None:
        return HELP_SYSTEM + note, f"Справка:\n{context}\n\nВопрос: {question}"
    return (HELP_SYSTEM + IMAGE_NOTE + note,
            f"Карта разделов справки:\n{screen_map}\n\nПодробно:\n{context}\n\nВопрос: {question}")


def repair_message(question: str, sql: str, error: str) -> str:
    """Second request after a PostgreSQL error: the model sees its query and the error (no data)."""
    return (f"{question}\n\nПредыдущий запрос не выполнился.\n```sql\n{sql}\n```\n"
            f"Ошибка PostgreSQL: {error}\nИсправь запрос по тем же правилам.")


def report_system(description: str) -> str:
    return REPORT_SYSTEM + "\n\nОписание данных:\n" + description


def describe_report_data(meta_cur, values_cur, allowed: list[AllowedObject], described: dict[int, dict],
                         denied: list[str]) -> tuple[str, set[str]]:
    """Description of the allowed objects for the model (columns, reference values, terms, examples, titles of
    the denied objects) and the names of all relations of schema dm (for the text check).
    meta_cur reads the catalog and meta.ai_* (dwh_app); values_cur reads reference values (dwh_ai_ro in the API)."""
    meta_cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                     "WHERE n.nspname = 'dm' AND c.relkind IN ('r', 'v', 'm', 'p', 'f')")
    dm_relations = {r["relname"] for r in meta_cur.fetchall()}
    meta_cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                     "WHERE table_schema = 'dm' AND table_name = ANY(%s) ORDER BY table_name, ordinal_position",
                     ([o.object_name for o in allowed],))
    columns: dict[str, list[str]] = {}
    for r in meta_cur.fetchall():
        columns.setdefault(r["table_name"], []).append(f"{r['column_name']} {r['data_type']}")
    ids = [o.object_id for o in allowed]
    meta_cur.execute("SELECT object_id, column_name, max_values FROM meta.ai_value_column "
                     "WHERE is_active AND object_id = ANY(%s) ORDER BY value_column_id", (ids,))
    value_columns = meta_cur.fetchall()
    meta_cur.execute("SELECT term, meaning FROM meta.ai_glossary WHERE is_active "
                     "AND (object_id IS NULL OR object_id = ANY(%s)) ORDER BY term_id", (ids,))
    glossary = meta_cur.fetchall()
    meta_cur.execute("SELECT question, sql_text FROM meta.ai_example WHERE is_active AND object_id = ANY(%s) "
                     "ORDER BY example_id LIMIT 20", (ids,))
    examples = meta_cur.fetchall()
    values: dict[tuple[int, str], list[str]] = {}
    by_id = {o.object_id: o for o in allowed}
    for vc in value_columns:
        obj = by_id[int(vc["object_id"])]
        values_cur.execute(pgsql.SQL("SELECT DISTINCT {c}::text AS v FROM {s}.{t} WHERE {c} IS NOT NULL "
                                     "ORDER BY 1 LIMIT %s").format(
            c=pgsql.Identifier(vc["column_name"]), s=pgsql.Identifier(obj.schema_name),
            t=pgsql.Identifier(obj.object_name)), (int(vc["max_values"]),))
        values[(obj.object_id, vc["column_name"])] = [r["v"] for r in values_cur.fetchall()]
    lines = []
    for obj in allowed:
        info = described[obj.object_id]
        lines.append(f"Объект dm.{obj.object_name} — {info['title']}. {info['description'] or ''}".strip())
        lines.append("  Колонки: " + ", ".join(columns.get(obj.object_name, [])))
        for (object_id, column), items in values.items():
            if object_id == obj.object_id:
                lines.append(f"  Значения {column}: " + "; ".join(items))
    if glossary:
        lines.append("Термины: " + "; ".join(f"{g['term']} — {g['meaning']}" for g in glossary))
    for e in examples:
        lines.append(f"Пример: {e['question']}\n```sql\n{e['sql_text']}\n```")
    if denied:
        lines.append("Недоступны роли пользователя: " + "; ".join(denied))
    return "\n".join(lines), dm_relations


def run_checked(cur, statement: str, allowed: list[AllowedObject],
                max_rows: int = MAX_ROWS) -> tuple[list[str], list[dict], bool]:
    """Plan check (EXPLAIN VERBOSE: only allowed objects and their base tables), then at most max_rows rows."""
    cur.execute("EXPLAIN (FORMAT JSON, VERBOSE) " + statement)
    plan = cur.fetchone()
    plan_value = list(plan.values())[0] if isinstance(plan, dict) else plan[0]
    check_plan(plan_value, allowed_closure(cur, allowed))
    cur.execute(limited(statement, max_rows))
    columns = [d.name for d in cur.description or []]
    rows = cur.fetchall()
    return columns, [dict(r) for r in rows[:max_rows]], len(rows) > max_rows
