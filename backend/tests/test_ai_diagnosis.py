"""I17-10: knowledge base of causes, card, prompt and dialogue memory of the AI assistant."""
import re
import unittest
from pathlib import Path

from dwh_api import ai_diagnosis as diag
from dwh_api.ai_assistant import HISTORY_MESSAGE_CHARS, HISTORY_MESSAGES, HistoryItem, dialogue, last_user_text

ROOT = Path(__file__).resolve().parents[2]


class KnowledgeTest(unittest.TestCase):
    def test_every_entry_has_three_languages(self):
        for code, cause in diag.KNOWLEDGE.items():
            for lang in ("ru", "en", "az"):
                self.assertTrue(cause.title[lang] and cause.action[lang], (code, lang))
            self.assertIn(cause.who, ("USER", "SOURCE_OWNER", "ADMIN"), code)
            self.assertGreater(len(cause.notes), 20, code)

    def test_engine_error_codes_are_described(self):
        """Codes the load and mart engines finish runs with have an entry (a new code needs a description)."""
        text = (ROOT / "etl/src/run_object.py").read_text(encoding="utf-8") + \
            (ROOT / "etl/src/dm_mart_worker.py").read_text(encoding="utf-8")
        used = set(re.findall(r'"((?:SOURCE|KEY|DM|TYPE|SCHEMA|TARGET|DISK|LOCK)_[A-Z_]+)"', text))
        engine = {c for c in used if c in {
            "SOURCE_NO_RESPONSE", "SOURCE_CONNECT_FAILED", "SOURCE_NOT_READ_ONLY", "TYPE_CONVERSION", "KEY_NULL",
            "KEY_DUPLICATE", "SCHEMA_DRIFT", "TARGET_DRIFT", "DISK_SPACE", "LOCK_TIMEOUT", "DM_KEY_NULL",
            "DM_KEY_NOT_UNIQUE", "DM_LOOKUP_NOT_UNIQUE", "DM_SOURCE_MISSING", "DM_SQL_COLUMNS_CHANGED"}}
        self.assertTrue(engine)
        self.assertEqual(sorted(engine - set(diag.KNOWLEDGE)), [])

    def test_unknown_code_falls_back(self):
        self.assertIs(diag.cause("NO_SUCH_CODE"), diag.UNKNOWN)
        self.assertIs(diag.cause(None), diag.UNKNOWN)
        self.assertIs(diag.cause("type_conversion"), diag.KNOWLEDGE["TYPE_CONVERSION"])


class CardAndPromptTest(unittest.TestCase):
    def dossier(self):
        d = diag.Dossier("ERROR", code="TYPE_CONVERSION", sqlstate="22007", error_id=5, object_id=7)
        d.where = ["источник CRM", "таблица источника crm.clients", "шаг ODS", "столбец birth_date"]
        d.add("поле", "birth_date")
        d.add("неверные значения", "birthD")
        d.add("пусто", None)
        d.unavailable.append("подключение")
        return d

    def test_card_in_the_language_without_model(self):
        card = diag.card(self.dossier(), "az")
        self.assertEqual(card["title"], diag.KNOWLEDGE["TYPE_CONVERSION"].title["az"])
        self.assertIn("столбец birth_date", card["where"])
        self.assertEqual(diag.card(self.dossier(), "xx")["title"], diag.KNOWLEDGE["TYPE_CONVERSION"].title["ru"])

    def test_prompt_contains_facts_notes_and_unavailable_parts(self):
        system, user = diag.prompt(self.dossier(), "en", "why now?")
        self.assertIn("English", system)
        self.assertIn("**Что сделать**", system)
        self.assertIn("- поле: birth_date", user)
        self.assertNotIn("пусто", user)
        self.assertIn("SQLSTATE 22007", user)
        self.assertIn("не делать выводов): подключение", user)
        self.assertIn("ВОПРОС ПОЛЬЗОВАТЕЛЯ: why now?", user)

    def test_long_facts_are_cut(self):
        d = diag.Dossier("ERROR")
        d.add("x", "a" * 5000)
        self.assertEqual(len(d.facts[0][1]), diag.MAX_TEXT)


class DialogueTest(unittest.TestCase):
    def test_last_messages_cut_and_ordered(self):
        items = [HistoryItem(role="user" if i % 2 == 0 else "assistant", text=f"m{i} " + "x" * 5000) for i in range(15)]
        kept = dialogue(items)
        self.assertLessEqual(len(kept), HISTORY_MESSAGES)
        self.assertTrue(all(len(text) <= HISTORY_MESSAGE_CHARS for _, text in kept))
        self.assertTrue(kept[-1][1].startswith("m14"))
        self.assertEqual(last_user_text(kept)[:3], "m14")

    def test_empty_messages_dropped(self):
        self.assertEqual(dialogue([HistoryItem(role="user", text="  "), HistoryItem(role="assistant", text="ok")]),
                         [("assistant", "ok")])


if __name__ == "__main__":
    unittest.main()
