import os
import shutil
import subprocess
import tempfile
import unittest

import voice


def ffmpeg_opus_available():
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "libopus" in out


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


class CommandTests(unittest.TestCase):
    def cmd(self, effect="👹 Demon"):
        return voice.build_ffmpeg_command("in.ogg", "out.ogg", effect, 21)

    def test_input_options_come_before_input(self):
        cmd = self.cmd()
        i = cmd.index("-i")
        self.assertEqual(cmd[cmd.index("-f") + 1], "ogg")
        self.assertLess(cmd.index("-f"), i, "forced demuxer must be an INPUT option")
        self.assertLess(cmd.index("-t"), i, "input duration cap must be an INPUT option")
        self.assertEqual(cmd[cmd.index("-t") + 1], "21")
        self.assertEqual(cmd[i + 1], "in.ogg")
        self.assertEqual(cmd[-1], "out.ogg")

    def test_hardening_flags(self):
        cmd = self.cmd()
        self.assertIn("-nostdin", cmd)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")
        self.assertIn("-vn", cmd)

    def test_no_shell_string(self):
        self.assertIsInstance(self.cmd(), list)

    def test_effect_filters(self):
        self.assertIn("48000*0.7", self.cmd("👹 Demon")[self.cmd().index("-af") + 1])
        self.assertIn("48000*1.5", self.cmd("🐿️ Chipmunk")[self.cmd().index("-af") + 1])
        self.assertEqual(self.cmd("unknown")[self.cmd().index("-af") + 1], voice.DEFAULT_EFFECT_FILTER)

    def test_missing_input_returns_false(self):
        self.assertFalse(voice.apply_voice_effect("/nonexistent.ogg", "/tmp/x.ogg", "👹 Demon", 21))

    def test_remove_file_is_quiet(self):
        voice.remove_file("/nonexistent/file.ogg")


@unittest.skipUnless(ffmpeg_opus_available(), "ffmpeg with libopus is required")
class FfmpegIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def make_voice(self, name, seconds):
        out = self.path(name)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=300:duration={seconds}", "-ar", "48000", "-ac", "1",
                        "-c:a", "libopus", out], check=True)
        return out

    def test_effect_is_applied(self):
        src = self.make_voice("voice.ogg", 3)
        out = self.path("out.ogg")
        self.assertTrue(voice.apply_voice_effect(src, out, "👹 Demon", 21))
        self.assertAlmostEqual(duration(out), 3 / 0.7, delta=0.3)       # lower pitch = longer

    def test_non_ogg_input_is_rejected(self):
        wav = self.path("fake.wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", wav], check=True)
        disguised = self.path("disguised.ogg")
        shutil.copy(wav, disguised)
        with self.assertLogs("voice", level="ERROR"):
            self.assertFalse(voice.apply_voice_effect(disguised, self.path("o.ogg"), "🤖 Robot", 21))

    def test_garbage_input_is_rejected(self):
        junk = self.path("junk.ogg")
        with open(junk, "wb") as f:
            f.write(b"#EXTM3U\n#EXTINF:5,\nfile:///etc/hostname\n")
        with self.assertLogs("voice", level="ERROR"):
            self.assertFalse(voice.apply_voice_effect(junk, self.path("o.ogg"), "🤖 Robot", 21))

    def test_input_length_is_capped_regardless_of_claimed_duration(self):
        src = self.make_voice("long.ogg", 60)
        out = self.path("out.ogg")
        self.assertTrue(voice.apply_voice_effect(src, out, "🤖 Robot", 21))
        self.assertLess(duration(out), 21 / 0.8 + 0.5)                   # ~26 s at most, not 75 s

    def test_source_tags_are_not_forwarded(self):
        src = self.make_voice("voice.ogg", 2)
        tagged = self.path("tagged.ogg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-metadata", "artist=SECRET",
                        "-c", "copy", tagged], check=True)
        out = self.path("out.ogg")
        self.assertTrue(voice.apply_voice_effect(tagged, out, "🐿️ Chipmunk", 21))
        tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags",
                               "-of", "default=nw=1", out], capture_output=True, text=True).stdout
        self.assertNotIn("SECRET", tags)


if __name__ == "__main__":
    unittest.main()
