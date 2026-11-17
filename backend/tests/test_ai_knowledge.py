"""I17 step 5 / I17-08: the guide index gives the model only the sections the user's role may read."""
from __future__ import annotations

import unittest

from dwh_api.ai_knowledge import Access, GuideError, KnowledgeIndex, UserScope, parse_guide

VIEW = {"NAV_INTEGRATION_VIEW", "INTEGRATION_VIEW", "NAV_ETL_VIEW", "ETL_VIEW", "NAV_DQ_VIEW", "DQ_VIEW",
        "NAV_REPORTING_VIEW", "REPORT_VIEW", "AI_ASSISTANT_USE", "AI_REPORT_RUN"}
ADMIN = UserScope("SUPERADMIN", frozenset(VIEW | {"ADMIN_INTEGRATION_MANAGE", "ADMIN_USER_MANAGE",
                                                  "ADMIN_ROLE_MANAGE", "ADMIN_ACCESS_MANAGE", "ADMIN_MODULE_MANAGE",
                                                  "AI_ADMIN_MANAGE"}), frozenset({"MODULE:CDP"}))
# "Audit": sees loads, runs and quality, no integration settings, no reporting.
AUDIT = UserScope("USER", frozenset({"NAV_INTEGRATION_VIEW", "INTEGRATION_VIEW", "NAV_ETL_VIEW", "ETL_VIEW",
                                     "NAV_DQ_VIEW", "DQ_VIEW", "AI_ASSISTANT_USE"}), frozenset({"MODULE:CDP"}))
NOBODY = UserScope("USER", frozenset({"AI_ASSISTANT_USE"}))


class AccessTests(unittest.TestCase):
    def test_parse_and_allow(self):
        rule = Access.parse("resource=MODULE:CDP; level=ADMIN; permissions=ADMIN_INTEGRATION_MANAGE")
        self.assertTrue(rule.allows(ADMIN))
        self.assertFalse(rule.allows(AUDIT))
        self.assertTrue(Access.parse("all").allows(NOBODY))
        self.assertFalse(Access.parse("level=SUPERADMIN").allows(UserScope("ADMIN", frozenset())))
        self.assertTrue(Access.parse("level=ADMIN").allows(UserScope("ADMIN", frozenset())))
        for bad in ("level=ROOT", "permissions=", "who=me", "permissions"):
            with self.assertRaises(GuideError):
                Access.parse(bad)

    def test_guide_without_marker_is_refused(self):
        with self.assertRaises(GuideError):
            parse_guide("# T\n\n## 1. Section\ntext\n")

    def test_subsection_inherits(self):
        sections = parse_guide("## 1. A\n<!-- access: permissions=ETL_VIEW -->\nx\n### 1.1. B\ny\n"
                               "### 1.2. C\n<!-- access: all -->\nz\n")
        self.assertEqual([s.number for s in sections], ["1", "1.1", "1.2"])
        self.assertEqual(sections[1].access.permissions, frozenset({"ETL_VIEW"}))
        self.assertEqual(sections[2].access, Access())


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = KnowledgeIndex.from_file()

    def numbers(self, question, user):
        return [s.number for s in self.index.search(question, user).sections]

    def test_probe_questions_find_their_section(self):
        expected = {
            "Как запустить загрузку объекта вручную?": "2.2",
            "Что значит кнопка «Заново полностью» и чем она отличается от «Запустить»?": "2.2",
            "Где посмотреть ошибки последней загрузки?": "2.5",
            "Как проверить подключение к источнику?": "5.1",
            "Где сохранённые отчёты и как сохранить отчёт помощника?": "3.4",
            "Что такое INCREMENTAL загрузка и как задать перекрытие?": "2.3",
            "Как сделать копию базы и проверить, что она восстанавливается?": "7.2",
            "Как установить обновление DWH на сервере и откатить его?": "7.1",
            "Что делать, если загрузка упала с ошибкой SOURCE_NO_RESPONSE?": "2.6",
            "Как заменить пароль источника и где он хранится?": "5.1",
        }
        for question, number in expected.items():
            self.assertIn(number, self.numbers(question, ADMIN), question)

    def test_audit_role_gets_no_closed_section(self):
        for question in ("Как заменить пароль источника и где он хранится?",
                         "Как проверить подключение к источнику?",
                         "Как установить обновление DWH на сервере и откатить его?"):
            result = self.index.search(question, AUDIT)
            for section in result.sections:
                self.assertTrue(section.access.allows(AUDIT), (question, section.number))
            self.assertTrue(result.only_denied, question)

    def test_audit_role_still_gets_open_sections(self):
        self.assertIn("2.6", self.numbers("Что делать, если загрузка упала с ошибкой SOURCE_NO_RESPONSE?", AUDIT))
        self.assertIn("2.4", self.numbers("Где история запусков и пакетов?", AUDIT))

    def test_section_zero_is_never_returned(self):
        self.assertNotIn("0", self.numbers("разметка доступа помощника access", ADMIN))

    def test_unrelated_question(self):
        result = self.index.search("погода в Баку", ADMIN)
        self.assertEqual(result.sections, [])
        self.assertFalse(result.only_denied)




class ScreenshotPromptTests(unittest.TestCase):
    """Step 9: with a screenshot the model gets a map of every section the role may read."""

    GUIDE = ("## 1. Загрузки\n<!-- access: permissions=ETL_VIEW -->\nВкладки Объекты и Запуски.\n"
             "## 2. Администрирование\n<!-- access: level=ADMIN -->\nПользователи и роли.\n")

    def test_screen_map_has_only_allowed_sections(self):
        index = KnowledgeIndex(parse_guide(self.GUIDE))
        text = index.screen_map(AUDIT)
        self.assertIn("Загрузки", text)
        self.assertIn("Вкладки Объекты", text)
        self.assertNotIn("Администрирование", text)
        self.assertNotIn("Пользователи", text)

    def test_help_prompt_with_and_without_map(self):
        from dwh_api.ai_core import IMAGE_NOTE, help_prompt, repair_message
        index = KnowledgeIndex(parse_guide(self.GUIDE))
        found = index.search("вкладки объекты", AUDIT)
        system, text = help_prompt(found, "что тут?")
        self.assertNotIn(IMAGE_NOTE, system)
        self.assertNotIn("Карта разделов", text)
        system, text = help_prompt(found, "что тут?", index.screen_map(AUDIT))
        self.assertIn(IMAGE_NOTE, system)
        self.assertIn("Карта разделов справки", text)
        self.assertNotIn("Пользователи", text)
        repair = repair_message("итог", "SELECT dm.x FROM dm.v_a a", 'missing FROM-clause entry for table "dm"')
        self.assertIn("missing FROM-clause", repair)
        self.assertIn("SELECT dm.x", repair)


class ClosedNearbyTests(unittest.TestCase):
    """A subject described partly in an open and partly in a closed section: the audit role is told the rest is
    closed for its role instead of "there is nothing in the guide"; neither the text nor the title is given."""

    GUIDE = ("## 1. Загрузки\n<!-- access: permissions=ETL_VIEW -->\n### 1.1. Вкладка витрины\n"
             "Список витрин и их запусков; добавить витрину сюда нельзя.\n"
             "## 2. Администрирование\n<!-- access: level=ADMIN -->\n### 2.1. Конструктор витрины\n"
             "Как добавить витрину: секретный порядок шагов.\n")

    def test_closed_nearby_note(self):
        from dwh_api.ai_core import help_prompt
        index = KnowledgeIndex(parse_guide(self.GUIDE))
        found = index.search("как добавить витрину", AUDIT)
        self.assertEqual([s.number for s in found.sections], ["1.1"])
        self.assertEqual(found.closed_nearby, 1)
        self.assertEqual(found.denied_titles, [])
        system, text = help_prompt(found, "как добавить витрину")
        self.assertIn("приложение само сообщит", system)
        self.assertIn("Не пиши, что в справке", system)
        self.assertNotIn("Конструктор", system + text)
        self.assertNotIn("секретный", system + text)
        admin = index.search("как добавить витрину", ADMIN)
        self.assertEqual(admin.closed_nearby, 0)
        self.assertNotIn("приложение само сообщит", help_prompt(admin, "как добавить витрину")[0])

    def test_real_guide_marts_question(self):
        index = KnowledgeIndex.from_file()
        audit = index.search("Как добавить витрину?", AUDIT)
        self.assertTrue(audit.sections)
        self.assertGreater(audit.closed_nearby, 0)
        self.assertTrue(all(s.access.allows(AUDIT) for s in audit.sections))
        self.assertIn("5.6", [s.number for s in index.search("Как добавить витрину?", ADMIN).sections])


if __name__ == "__main__":
    unittest.main()
