"""Language resolution and the message catalogue.

Every test here pins its language explicitly or fakes the environment: the
machine running the suite has its own locale, and a test that silently agreed
with it would pass on this laptop and fail in CI.
"""
import os
import unittest
import unittest.mock as mock

from codex_reset_watch import i18n


class SystemLanguageTests(unittest.TestCase):
    def _with_locale(self, value):
        env = {k: v for k, v in os.environ.items()
               if k not in ("LC_ALL", "LC_MESSAGES", "LANG", "CRW_LANG")}
        if value is not None:
            env["LANG"] = value
        return mock.patch.dict(os.environ, env, clear=True)

    def test_english_locale_resolves_to_en(self):
        with self._with_locale("en_US.UTF-8"):
            self.assertEqual(i18n.system_language(), "en")

    def test_traditional_chinese_locale_resolves_to_zh_tw(self):
        with self._with_locale("zh_TW.UTF-8"):
            self.assertEqual(i18n.system_language(), "zh-TW")

    def test_hong_kong_locale_resolves_to_zh_tw(self):
        with self._with_locale("zh_HK.UTF-8"):
            self.assertEqual(i18n.system_language(), "zh-TW")

    def test_simplified_chinese_does_not_resolve_to_traditional(self):
        with self._with_locale("zh_CN.UTF-8"):
            self.assertEqual(i18n.system_language(), i18n.FALLBACK)

    def test_unknown_locale_falls_back(self):
        with self._with_locale("xx_YY.UTF-8"):
            self.assertEqual(i18n.system_language(), i18n.FALLBACK)

    def test_c_locale_falls_back(self):
        with self._with_locale("C"):
            self.assertEqual(i18n.system_language(), i18n.FALLBACK)


class ResolveLanguageTests(unittest.TestCase):
    def test_explicit_code_is_kept(self):
        self.assertEqual(i18n.resolve_language("zh-TW"), "zh-TW")
        self.assertEqual(i18n.resolve_language("en"), "en")

    def test_auto_resolves_to_system_language(self):
        with mock.patch.object(i18n, "system_language", return_value="zh-TW"):
            self.assertEqual(i18n.resolve_language("auto"), "zh-TW")

    def test_unknown_code_resolves_to_system_language(self):
        with mock.patch.object(i18n, "system_language", return_value="en"):
            self.assertEqual(i18n.resolve_language("klingon"), "en")

    def test_none_resolves_to_system_language(self):
        with mock.patch.object(i18n, "system_language", return_value="en"):
            self.assertEqual(i18n.resolve_language(None), "en")


class EnvOverrideTests(unittest.TestCase):
    def test_crw_lang_env_wins_over_configured_value(self):
        with mock.patch.dict(os.environ, {"CRW_LANG": "zh-TW"}):
            self.assertEqual(i18n.current_language({"language": "en"}), "zh-TW")

    def test_configured_value_is_used_without_the_env_override(self):
        env = {k: v for k, v in os.environ.items() if k != "CRW_LANG"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(i18n.current_language({"language": "zh-TW"}), "zh-TW")

    def test_junk_env_value_is_ignored_in_favour_of_the_config(self):
        with mock.patch.dict(os.environ, {"CRW_LANG": "nonsense"}):
            self.assertEqual(i18n.current_language({"language": "zh-TW"}), "zh-TW")


class TranslateTests(unittest.TestCase):
    def test_returns_the_requested_language(self):
        self.assertEqual(i18n.t("menu.quit", lang="en"), "quit")
        self.assertEqual(i18n.t("menu.quit", lang="zh-TW"), "離開")

    def test_unknown_id_returns_the_supplied_default(self):
        self.assertEqual(i18n.t("no.such.id", lang="en", default="fallback"), "fallback")

    def test_unknown_id_without_default_returns_the_id(self):
        self.assertEqual(i18n.t("no.such.id", lang="en"), "no.such.id")

    def test_missing_translation_falls_back_to_english(self):
        with mock.patch.dict(i18n.MESSAGES, {"partial.id": {"en": "only english"}}):
            self.assertEqual(i18n.t("partial.id", lang="zh-TW"), "only english")

    def test_placeholders_are_substituted(self):
        with mock.patch.dict(i18n.MESSAGES, {"greet.id": {"en": "hello {name}"}}):
            self.assertEqual(i18n.t("greet.id", lang="en", name="world"), "hello world")

    def test_a_missing_placeholder_never_raises(self):
        with mock.patch.dict(i18n.MESSAGES, {"greet.id": {"en": "hello {name}"}}):
            self.assertIn("hello", i18n.t("greet.id", lang="en"))


class CatalogueCompletenessTests(unittest.TestCase):
    def test_every_message_has_every_language(self):
        missing = [
            f"{msg_id}:{lang}"
            for msg_id, translations in i18n.MESSAGES.items()
            for lang in i18n.LANGUAGE_CODES
            if lang not in translations
        ]
        self.assertEqual(missing, [])

    def test_language_codes_match_the_language_table(self):
        self.assertEqual(i18n.LANGUAGE_CODES, tuple(l.code for l in i18n.LANGUAGES))

    def test_auto_is_not_a_selectable_language_code(self):
        self.assertNotIn(i18n.AUTO, i18n.LANGUAGE_CODES)

    def test_every_language_names_itself_in_its_own_script(self):
        labels = i18n.language_labels()
        self.assertEqual(labels["en"], "English")
        self.assertEqual(labels["zh-TW"], "繁體中文")


if __name__ == "__main__":
    unittest.main()
