import unittest

from sound_of_vibe.models import Event
from sound_of_vibe.rules import Narrator, classify_command, detect_language, progress_sentences


class RulesTests(unittest.TestCase):
    def test_bilingual_progress_and_inline_technical_names(self):
        result = progress_sentences("我先查看 `app.py` 中的 API。\nI'll inspect the project.\nI'm running tests.")
        self.assertEqual([(s.language, s.action) for s in result],
                         [("zh", "read"), ("en", "read"), ("en", "test")])
        self.assertNotIn("app.py", result[0].text)

    def test_filters_code_logs_and_completed_descriptions(self):
        text = '''```python
I'm updating files.
```
~~~text
我正在修改代码。
~~~
[INFO] I'm testing now.
    I'm running tests.
+我正在修改文件。
Traceback (most recent call last):
I've finished testing.
我已经完成修改。
The project has three files.
'''
        self.assertEqual(progress_sentences(text), [])

    def test_language_ignores_paths_and_preserves_mixed_chinese(self):
        self.assertEqual(detect_language("我先检查 Java API。"), "zh")
        self.assertEqual(detect_language("Inspect `中文.py`"), "en")
        self.assertIsNone(detect_language("123 `app.py`"))

    def test_conservative_command_classification(self):
        self.assertEqual(classify_command('bash -lc "rg --files"'), "search")
        self.assertEqual(classify_command("python -m pytest -q"), "test")
        self.assertEqual(classify_command("npm run test"), "test")
        self.assertEqual(classify_command('echo "pytest"'), "command")
        self.assertEqual(classify_command("npm run test-fixture"), "command")
        self.assertEqual(classify_command("echo hello && pytest"), "command")

    def test_action_is_not_confused_by_test_file_names_or_later_nouns(self):
        result = progress_sentences("I'll inspect test.py.\nI'll review the tests.\n我先检查测试文件。\nI'm running tests.")
        self.assertEqual([item.action for item in result], ["read", "read", "read", "test"])

    def test_language_switch_dedup_and_terminal(self):
        now = [0.0]
        narrator = Narrator("检查项目", clock=lambda: now[0])
        self.assertEqual(narrator.consume(Event("kimi", "1", "read"))[0].language, "zh")
        self.assertEqual(narrator.consume(Event("kimi", "2", text="I'll inspect the code."))[0].language, "en")
        self.assertEqual(narrator.consume(Event("kimi", "3", "read")), [])
        now[0] = 31
        self.assertEqual(narrator.consume(Event("kimi", "4", "read"))[0].language, "en")
        result = narrator.consume(Event("kimi", "end", "complete", terminal=True))
        self.assertEqual(result[0].text, "Task completed")
        self.assertEqual(narrator.consume(Event("kimi", "end2", "failed", terminal=True)), [])

    def test_forced_language_and_length(self):
        narrator = Narrator("英文 English", "en")
        self.assertEqual(narrator.consume(Event("kimi", "1", text="我先查看文件。")), [])
        self.assertEqual(narrator.consume(Event("kimi", "2", "read"))[0].language, "en")
        zh = progress_sentences("我先查看" + "文件" * 80 + "。")[0]
        en = progress_sentences("I'll inspect " + "files " * 40)[0]
        self.assertLessEqual(len(zh.text), 60)
        self.assertLessEqual(len(en.text.split()), 30)


if __name__ == "__main__":
    unittest.main()
