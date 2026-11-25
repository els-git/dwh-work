"""I17-12: answers on the application without the model (script), model only on request, screenshot or report."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DWH_APP_PASSWORD", "unit-test-not-used")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dwh_api import ai_assistant, ai_diagnosis  # noqa: E402
from dwh_api.ai_assistant import HelpRequest  # noqa: E402
from test_ai_knowledge import ADMIN, AUDIT  # noqa: E402


class ScriptHelpTests(unittest.TestCase):
    def ask(self, question, user=ADMIN, history=()):
        journal = []
        with mock.patch.object(ai_assistant, "begin", return_value=7) as begin, \
                mock.patch.object(ai_assistant, "finish", side_effect=lambda *a, **k: journal.append((a, k))), \
                mock.patch.object(ai_assistant, "ask_model", side_effect=AssertionError("model must not be asked")):
            payload = HelpRequest(question=question, history=list(history))
            answer = ai_assistant.help_by_script(payload, object(), user)
        self.assertEqual(begin.call_args.args[1:4], ("HELP", question, False))
        self.assertIsNone(begin.call_args.args[4])  # no model label: the model is not used
        return answer, journal

    def test_answer_is_the_matching_guide_lines(self):
        answer, journal = self.ask("где находится Реестр файлов")
        self.assertEqual((answer["status"], answer["mode"], answer["foreign"]), ("OK", "SCRIPT", False))
        self.assertIn("→ 5.2b.", answer["answer"])
        self.assertIn("Реестр файлов", answer["answer"])
        self.assertLess(len(answer["answer"]), 3000)
        self.assertEqual(answer["sections"][0]["number"], "5.2b")
        self.assertEqual(journal[0][0][2:4], ("OK", "AI_HELP_SCRIPT"))

    def test_answer_says_where_the_element_is(self):
        # Remark of 25.11.2026: "on the tab «Файлы»" is not enough - the path to the tab and the section path.
        answer, _ = self.ask("где находится Реестр файлов")
        self.assertIn("**5. Администрирование: интеграции → 5.2b.", answer["answer"])
        self.assertIn("«Маппинг»", answer["answer"])
        self.assertIn("«Объекты источника»", answer["answer"])
        # A step of a list brings the line that opens the list (where the steps are).
        answer, _ = self.ask("как задать маску имени файла")
        self.assertIn("В «Маппинге» такого объекта первая вкладка — «Файлы»:", answer["answer"])

    def test_model_offered_after_not_found_only_when_it_can_help(self):
        answer, _ = self.ask("погода в Баку завтра")
        self.assertFalse(answer["ai_may_help"])  # the model would search the same guide and find nothing too
        history = [{"role": "user", "text": "где находится Реестр файлов"}, {"role": "assistant", "text": "..."}]
        answer, _ = self.ask("а зачем это", history=history)
        self.assertEqual(answer["status"], "NO_DATA")
        self.assertTrue(answer["ai_may_help"])  # a follow-up: with the previous question the guide has an answer
        history = [{"role": "user", "text": "какая погода в Баку"}, {"role": "assistant", "text": "..."}]
        answer, _ = self.ask("а завтра", history=history)
        self.assertFalse(answer["ai_may_help"])  # with the previous question nothing either
        answer, _ = self.ask("какая погода в Баку завтра",
                             history=[{"role": "user", "text": "где находится Реестр файлов"}])
        self.assertFalse(answer["ai_may_help"])  # unrelated new question: the previous one does not answer it
        answer, _ = self.ask("fayllar reyestri harada yerləşir")
        self.assertTrue(answer["ai_may_help"])  # another language: the model translates
        answer, _ = self.ask("где находится Реестр файлов")
        self.assertTrue(answer["ai_may_help"])

    def test_previous_question_does_not_answer_a_new_one(self):
        history = [{"role": "user", "text": "где находится Реестр файлов"}, {"role": "assistant", "text": "..."}]
        answer, _ = self.ask("какая погода в Баку завтра", history=history)
        self.assertEqual(answer["status"], "NO_DATA")

    def test_not_found_and_foreign_question(self):
        answer, journal = self.ask("погода в Баку завтра")
        self.assertEqual((answer["status"], answer["code"]), ("NO_DATA", "AI_HELP_NOT_FOUND"))
        self.assertEqual(journal[0][0][2], "NO_DATA")
        answer, _ = self.ask("where is the file register")
        self.assertTrue(answer["foreign"])

    def test_role_is_respected(self):
        answer, _ = self.ask("Где хранится пароль источника и кто может его увидеть?", user=AUDIT)
        self.assertIn(answer["status"], ("DENIED", "OK"))
        if answer["status"] == "OK":
            self.assertNotIn("5.1", [s["number"] for s in answer["sections"]])

    def test_default_mode_is_script(self):
        self.assertEqual(HelpRequest(question="x").mode, "script")
        self.assertEqual(HelpRequest(question="x", mode="ai").mode, "ai")


class AcceptanceCoverageTests(unittest.TestCase):
    def test_script_answers_most_help_questions_of_the_acceptance_set(self):
        """25.11.2026: 27 of 29 Russian help questions get an expected section without the model."""
        import json

        from dwh_api.ai_core import script_answer
        from dwh_api.ai_knowledge import UserScope, is_russian

        doc = json.loads((Path(__file__).resolve().parents[2] / "tools/server/ai_acceptance.json").read_text("utf-8"))
        index = ai_assistant.knowledge()
        hit = total = 0
        for item in doc["items"]:
            if item["kind"] != "help" or item["expect"] != "OK" or not is_russian(item["question"]):
                continue
            role = doc["roles"][item["role"]]
            user = UserScope(role["level"], frozenset(role["permissions"]), frozenset(role["resources"]))
            _, used = script_answer(index.search(item["question"], user), item["question"], index.weight)
            total += 1
            hit += any(s.number in item["sections"] for s in used)
        self.assertGreaterEqual(hit / total, 0.9, f"{hit}/{total}")


class DiagnosisCardTests(unittest.TestCase):
    def test_card_says_whether_the_code_is_known(self):
        for code, expected in (("KEY_NULL", True), ("SOMETHING_NEW", False), (None, False)):
            d = mock.Mock(code=code, sqlstate=None, where=[], error_id=1, object_id=None, mart_code=None)
            self.assertEqual(ai_diagnosis.card(d, "ru")["known"], expected, code)


if __name__ == "__main__":
    unittest.main()


class FollowUpByButtonTests(unittest.TestCase):
    def test_button_history_ending_with_the_same_question_keeps_the_previous_one(self):
        # Acceptance 2026.11.71: «Спросить ИИ» after «а зачем это» sent the history ending with the same question;
        # the model searched it twice and lost the previous question - "no answer" in 1 s.
        history = [("user", "где находится Реестр файлов"), ("assistant", "..."), ("assistant", "ответ модели"),
                   ("user", "а зачем это"),
                   ("assistant", "В справке нет ответа на этот вопрос.")]
        self.assertEqual(ai_assistant.last_user_text(history, "а зачем это"), "где находится Реестр файлов")
        self.assertEqual(ai_assistant.last_user_text(history), "а зачем это")
        # The 4 messages given to the model do not contain the previous question: it comes from the whole history.
        self.assertEqual(ai_assistant.last_user_text(history[-ai_assistant.HELP_HISTORY_MESSAGES:], "а зачем это"), "")
        found = ai_assistant.knowledge().search(
            "а зачем это  " + ai_assistant.last_user_text(history, "а зачем это"), ADMIN)
        self.assertEqual(found.sections[0].number, "5.2b")


class ScreenshotScreenTests(unittest.TestCase):
    """Remark 3 of 2026.11.70: an Azerbaijani question with a screenshot got a made-up transliterated section name
    and took about 6 minutes. The interface now names the screen (its language and Russian)."""

    IMAGE = "data:image/png;base64,iVBORw0KGgoAAAA="

    def ask(self, question, screen):
        calls = []

        def model(request, request_id, settings, system, user_text, image=None, **kwargs):
            calls.append((system, user_text, image))
            return "cavab"

        with mock.patch.object(ai_assistant, "user_scope", return_value=ADMIN), \
                mock.patch.object(ai_assistant, "llm_settings", return_value=object()), \
                mock.patch.object(ai_assistant, "begin", return_value=7), \
                mock.patch.object(ai_assistant, "finish"), \
                mock.patch.object(ai_assistant, "ask_model", side_effect=model):
            answer = ai_assistant.help_answer(HelpRequest(question=question, image=self.IMAGE, screen=screen), object())
        return answer, calls

    def test_screen_names_replace_translation_and_section_map(self):
        screen = [{"label": "İnzibatçılıq", "ru": "Администрирование"},
                  {"label": "Süni intellekt köməkçisi", "ru": "ИИ-помощник"}]
        answer, calls = self.ask("bu bölmə nə üçündür?", screen)
        self.assertEqual(len(calls), 1)  # no translation request: one model call only
        system, user_text, image = calls[0]
        self.assertEqual(image, self.IMAGE)
        self.assertIn("«Süni intellekt köməkçisi» (по-русски «ИИ-помощник»)", user_text)
        self.assertIn("не переводи их и не транслитерируй", system)
        self.assertNotIn("Карта разделов справки", user_text)  # the map of all sections is not sent
        self.assertIn("6.1", [s["number"] for s in answer["sections"]])  # found by the Russian names
        self.assertIn("весь ответ напиши на языке вопроса", user_text)  # acceptance of 2026.11.73
        self.assertIn("о переводе и о правилах помощника не пиши", user_text)

    def test_russian_question_with_screen_gets_no_language_note(self):
        _, calls = self.ask("что это за раздел?", [{"label": "Администрирование", "ru": "Администрирование"}])
        self.assertNotIn("весь ответ напиши на языке вопроса", calls[0][1])

    def test_attached_image_without_screen_keeps_the_section_map(self):
        _, calls = self.ask("что на этом экране?", [])
        self.assertIn("Карта разделов справки", calls[-1][1])

    def test_screen_labels_are_limited(self):
        with self.assertRaises(Exception):
            HelpRequest(question="x", screen=[{"label": "a" * 81}])
        with self.assertRaises(Exception):
            HelpRequest(question="x", screen=[{"label": "a"}] * 13)
