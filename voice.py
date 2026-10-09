"""Voice effects built on FFmpeg (called without a shell)."""
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 60

EFFECT_FILTERS = {
    "🤖 Robot": "asetrate=48000*0.8,aresample=48000",
    "👹 Demon": "asetrate=48000*0.7,aresample=48000",
    "🐿️ Chipmunk": "asetrate=48000*1.5,aresample=48000",
}
DEFAULT_EFFECT_FILTER = "asetrate=48000*0.8,aresample=48000"


def build_ffmpeg_command(input_path, output_path, effect_name, max_input_seconds):
    """Builds the FFmpeg command line for one voice message.

    The input comes from an untrusted client, so:
    - "-f ogg" forces the Ogg demuxer: FFmpeg would otherwise detect the format from the
      *content* and happily parse playlists, WAV, video containers, etc. renamed to .ogg;
    - "-t" caps how much input is read, because the "duration" field of a voice message is
      filled in by the sending client and can lie;
    - "-map_metadata -1" drops tags of the source file (they would be forwarded to the partner);
    - "-nostdin" keeps FFmpeg away from the bot's stdin.
    """
    audio_filter = EFFECT_FILTERS.get(effect_name, DEFAULT_EFFECT_FILTER)
    return [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-t", str(max_input_seconds),
        "-f", "ogg",
        "-i", input_path,
        "-vn",
        "-map_metadata", "-1",
        "-af", audio_filter,
        "-c:a", "libopus",
        "-b:a", "32k",
        "-ac", "1",
        output_path,
    ]


def apply_voice_effect(input_path, output_path, effect_name, max_input_seconds):
    """Returns True if output_path now contains the processed voice."""
    try:
        if not os.path.exists(input_path):
            return False

        cmd = build_ffmpeg_command(input_path, output_path, effect_name, max_input_seconds)
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=FFMPEG_TIMEOUT, stdin=subprocess.DEVNULL)

        if result.returncode == 0 and os.path.exists(output_path):
            return True
        logger.error("FFmpeg error: %s", result.stderr)
        return False

    except Exception as e:
        logger.error("Effect error: %s", e)
        return False


def remove_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("Could not remove %s: %s", path, e)
