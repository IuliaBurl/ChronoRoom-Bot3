import os

from dotenv import load_dotenv

load_dotenv()


def _parse_chat_id(raw: str):
    """Numeric id (-100123...) becomes int, '@channelname' stays a string."""
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


BOT_TOKEN = os.getenv("BOT_TOKEN", "")

CHANNEL_ID = _parse_chat_id(os.getenv("CHANNEL_ID", ""))
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")

EFFECTS = [
    "🤖 Robot", "👹 Demon",
    "🐿️ Chipmunk"
]

GAME_WORDS = [
    "phone", "computer", "sun", "cat", "dog", "car",
    "book", "glass", "window", "door", "kettle", "fridge",
    "ball", "flower", "tree", "cloud", "rain", "snow", "fire", "water", "air", "earth", "school", "work", "rest", "music",
    "dance", "running", "jump", "laughter", "crying", "sleep", "food", "drink",
    "city", "village", "sea", "mountain", "river", "forest", "field", "sky",
    "star", "moon", "planet", "time", "money", "love", "friendship",
    "family", "health", "luck", "dream", "goal", "victory", "defeat",
    "beginning", "end", "journey", "adventure", "mystery", "riddle",
    "surprise", "holiday", "game", "sport", "movie", "song",
    "painting", "photograph", "memory", "future", "past", "present"
]

EFFECT_CHANGE_TIME = 120
SEARCH_TIMEOUT = 30
GAME_ROUND_TIME = 60

DB_NAME = "chronoroom.db"
DB_TABLE_USERS = "users"
DB_TABLE_GAMES = "games_stats"

TEMP_FOLDER = "temp_audio"
SAVE_VOICE_FILES = False

MAX_VOICE_DURATION = 20
MAX_VOICE_FILE_SIZE = 1024 * 1024  # bytes; "duration" is set by the sending client, so it cannot be trusted alone
ENABLE_STATISTICS = True
CHECK_SUBSCRIPTION = os.getenv("CHECK_SUBSCRIPTION", "1") == "1" and bool(CHANNEL_ID)
ENABLE_EFFECTS = True
