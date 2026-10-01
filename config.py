import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
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
ENABLE_STATISTICS = True
CHECK_SUBSCRIPTION = os.getenv("CHECK_SUBSCRIPTION", "1") == "1" and CHANNEL_ID != 0
ENABLE_EFFECTS = True

ADMIN_IDS = []
