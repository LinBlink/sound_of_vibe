import json
import unittest

from sound_of_vibe.stream import JsonlDecoder


class StreamTests(unittest.TestCase):
    def test_bytewise_utf8_and_final_line_without_newline(self):
        expected = {"role": "assistant", "content": "我先查看文件。"}
        wire = (json.dumps(expected, ensure_ascii=False) + "\r\n").encode()
        decoder = JsonlDecoder()
        messages = []
        for byte in wire:
            messages.extend(decoder.feed(bytes([byte])))
        self.assertEqual(messages, [expected])
        self.assertEqual(decoder.feed(b'{"role":"meta"}'), [])
        self.assertEqual(decoder.finish(), [{"role": "meta"}])

    def test_bad_lines_do_not_poison_following_events(self):
        decoder = JsonlDecoder()
        messages = decoder.feed(b'not json\n[]\n\xff\n\n{"ok":true}\n')
        self.assertEqual(messages, [{"ok": True}])
        self.assertEqual(decoder.invalid, 3)

    def test_oversized_line_is_bounded_and_recovers(self):
        decoder = JsonlDecoder(max_line_bytes=20)
        self.assertEqual(decoder.feed(b"x" * 100), [])
        self.assertLessEqual(len(decoder.buffer), 20)
        self.assertEqual(decoder.feed(b'y\n{"ok":true}\n'), [{"ok": True}])
        self.assertEqual(decoder.invalid, 1)


if __name__ == "__main__":
    unittest.main()
