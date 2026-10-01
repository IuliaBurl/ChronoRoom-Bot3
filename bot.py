import itertools
import logging
import os
import random
import sqlite3
import subprocess
import threading
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(config.BOT_TOKEN)

search_lock = threading.Lock()
game_lock = threading.RLock()
search_counter = itertools.count(1)

waiting_users = {}
active_connections = {}
user_effects = {}
game_sessions = {}
effects_enabled = {}

STAT_FIELDS = {'searches', 'connections', 'game_wins', 'total_time'}

EFFECT_FILTERS = {
    "🤖 Robot": "asetrate=48000*0.8,aresample=48000",
    "👹 Demon": "asetrate=48000*0.7,aresample=48000",
    "🐿️ Chipmunk": "asetrate=48000*1.5,aresample=48000"
}
DEFAULT_EFFECT_FILTER = "asetrate=48000*0.8,aresample=48000"


def init_db():
    conn = sqlite3.connect(config.DB_NAME)
    c = conn.cursor()
    c.execute(f'''CREATE TABLE IF NOT EXISTS {config.DB_TABLE_USERS}
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  searches INTEGER DEFAULT 0,
                  connections INTEGER DEFAULT 0,
                  game_wins INTEGER DEFAULT 0,
                  total_time INTEGER DEFAULT 0,
                  joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute(f'''CREATE TABLE IF NOT EXISTS {config.DB_TABLE_GAMES}
                 (game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user1_id INTEGER,
                  user2_id INTEGER,
                  words_guessed INTEGER DEFAULT 0,
                  game_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()


def get_or_create_user(user_id, username=None):
    conn = sqlite3.connect(config.DB_NAME)
    c = conn.cursor()
    c.execute(f"SELECT * FROM {config.DB_TABLE_USERS} WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute(f"INSERT INTO {config.DB_TABLE_USERS} (user_id, username) VALUES (?, ?)",
                  (user_id, username))
    elif username:
        c.execute(f"UPDATE {config.DB_TABLE_USERS} SET username = ? WHERE user_id = ?",
                  (username, user_id))
    conn.commit()
    conn.close()


def update_stats(user_id, field):
    if not config.ENABLE_STATISTICS or field not in STAT_FIELDS:
        return
    conn = sqlite3.connect(config.DB_NAME)
    c = conn.cursor()
    c.execute(f"UPDATE {config.DB_TABLE_USERS} SET {field} = {field} + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def record_game_session(user1_id, user2_id, words_guessed):
    conn = sqlite3.connect(config.DB_NAME)
    c = conn.cursor()
    c.execute(f"INSERT INTO {config.DB_TABLE_GAMES} (user1_id, user2_id, words_guessed) VALUES (?, ?, ?)",
              (user1_id, user2_id, words_guessed))
    conn.commit()
    conn.close()


def check_subscription(user_id):
    if not config.CHECK_SUBSCRIPTION:
        return True
    try:
        member = bot.get_chat_member(config.CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Subscription check error: {e}")
        return False


def start_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📢 Subscribe", url=config.CHANNEL_LINK),
        InlineKeyboardButton("✅ Check", callback_data="check_subscription")
    )
    return markup


def main_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔍 Find a partner", callback_data="search_partner")
    )
    markup.row(
        InlineKeyboardButton("🎮 Mini-game", callback_data="start_game"),
        InlineKeyboardButton("⚙️ Menu", callback_data="show_menu")
    )
    return markup


def chat_keyboard(effects_on=True, in_game=False):
    markup = InlineKeyboardMarkup()

    if in_game:
        markup.row(
            InlineKeyboardButton(f"{'🔇' if not effects_on else '🔊'} Effects", callback_data="toggle_effects_game"),
            InlineKeyboardButton("🔄 Change word", callback_data="change_word")
        )
    else:
        markup.row(
            InlineKeyboardButton("🔄 Next", callback_data="next_partner"),
            InlineKeyboardButton(f"{'🔇' if not effects_on else '🔊'} Effects", callback_data="toggle_effects_chat")
        )

    if in_game:
        markup.row(
            InlineKeyboardButton("🏁 End game", callback_data="end_game"),
            InlineKeyboardButton("👤 Username", callback_data="request_username")
        )
    else:
        markup.row(
            InlineKeyboardButton("🎮 Start game", callback_data="start_game"),
            InlineKeyboardButton("👤 Username", callback_data="request_username")
        )

    markup.row(InlineKeyboardButton("📱 Home", callback_data="go_home"))
    return markup


def guesser_keyboard(effects_on=True):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(f"{'🔇' if not effects_on else '🔊'} Effects", callback_data="toggle_effects_game"),
        InlineKeyboardButton("🏁 End game", callback_data="end_game")
    )
    markup.row(
        InlineKeyboardButton("👤 Username", callback_data="request_username")
    )
    markup.row(InlineKeyboardButton("📱 Home", callback_data="go_home"))
    return markup


def menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎭 Voice selection", callback_data="voice_selection")
    )
    markup.row(
        InlineKeyboardButton("↩️ Back", callback_data="back_to_chat")
    )
    return markup


def voice_selection_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🤖 Robot", callback_data="effect_robot"),
        InlineKeyboardButton("👹 Demon", callback_data="effect_demon")
    )
    markup.row(
        InlineKeyboardButton("🐿️ Chipmunk", callback_data="effect_chipmunk"),
        InlineKeyboardButton("🎲 Random", callback_data="effect_random")
    )
    markup.row(InlineKeyboardButton("↩️ Back", callback_data="show_menu"))
    return markup


def game_start_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Start game", callback_data="confirm_game_start"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")
    )
    return markup


def request_username_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Show", callback_data="show_username"),
        InlineKeyboardButton("❌ Hide", callback_data="hide_username")
    )
    return markup


def cleanup_on_start():
    """Clears all data when the bot starts"""
    waiting_users.clear()
    active_connections.clear()
    user_effects.clear()
    game_sessions.clear()
    effects_enabled.clear()
    logger.info("Previous session data cleared")


def is_guessing(user_id):
    session = game_sessions.get(user_id)
    return bool(session and session["started"] and session["guesser"] == user_id)


def is_explaining(user_id):
    session = game_sessions.get(user_id)
    return bool(session and session["started"] and session["explainer"] == user_id)


@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username

    get_or_create_user(user_id, username)
    effects_enabled.setdefault(user_id, True)

    if check_subscription(user_id):
        send_welcome(user_id)
    else:
        bot.send_message(user_id,
                         "⏳ *ChronoRoom*\n\n"
                         "Voice chat with altered voice\n\n"
                         "📢 Subscribe to the channel for access:", parse_mode="Markdown",
                         reply_markup=start_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription_callback(call):
    user_id = call.from_user.id

    if check_subscription(user_id):
        bot.answer_callback_query(call.id, "✅ Access granted")
        bot.edit_message_text("⏳ *ChronoRoom*",
                              call.message.chat.id,
                              call.message.message_id,
                              parse_mode="Markdown",
                              reply_markup=main_keyboard())
    else:
        bot.answer_callback_query(call.id, "❌ You are not subscribed", show_alert=True)


def send_welcome(user_id):
    welcome_text = (
        "⏳ *ChronoRoom*\n\n"
        "Voice chat with altered voice\n\n"
        "• Random partner\n"
        "• Voice changes automatically\n"
        "• Mini-game “Guess the Word”\n"
        "• Complete anonymity"
    )

    bot.send_message(user_id, welcome_text,
                     parse_mode="Markdown",
                     reply_markup=main_keyboard())


def show_partner_found(user_id, chat_id, message_id):
    success_text = "✅ *Partner found*\n\n🎤 Send voice messages"
    try:
        bot.edit_message_text(success_text,
                              chat_id,
                              message_id,
                              parse_mode="Markdown",
                              reply_markup=chat_keyboard())
    except Exception:
        bot.send_message(user_id, success_text,
                         parse_mode="Markdown",
                         reply_markup=chat_keyboard())


def begin_search(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if user_id in active_connections:
        bot.answer_callback_query(call.id, "❌ End the current dialogue first")
        return

    with search_lock:
        if user_id in waiting_users:
            bot.answer_callback_query(call.id, "🔍 Already searching...")
            return

    bot.answer_callback_query(call.id, "🔍 Starting search...")
    update_stats(user_id, 'searches')

    try:
        bot.edit_message_text(f"🔮 *Searching for a partner...*\n⏱ {config.SEARCH_TIMEOUT} seconds",
                              chat_id, message_id,
                              parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not edit search message: {e}")

    partner = None
    token = next(search_counter)

    with search_lock:
        if user_id in active_connections or user_id in waiting_users:
            return
        for candidate_id in list(waiting_users):
            if candidate_id != user_id and candidate_id not in active_connections:
                partner = (candidate_id, waiting_users.pop(candidate_id))
                break
            waiting_users.pop(candidate_id, None)

        if partner:
            partner_id = partner[0]
            active_connections[user_id] = partner_id
            active_connections[partner_id] = user_id
            for uid in (user_id, partner_id):
                user_effects[uid] = random.choice(config.EFFECTS)
                effects_enabled[uid] = True
        else:
            waiting_users[user_id] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "token": token
            }

    if partner:
        partner_id, partner_data = partner
        show_partner_found(user_id, chat_id, message_id)
        show_partner_found(partner_id, partner_data["chat_id"], partner_data["message_id"])
        update_stats(user_id, 'connections')
        update_stats(partner_id, 'connections')
        logger.info(f"Connection: {user_id} <-> {partner_id}")
    else:
        timer = threading.Timer(config.SEARCH_TIMEOUT, search_timeout, args=(user_id, token))
        timer.daemon = True
        timer.start()


def search_timeout(user_id, token):
    with search_lock:
        data = waiting_users.get(user_id)
        if not data or data["token"] != token:
            return
        del waiting_users[user_id]

    try:
        bot.edit_message_text("❌ *Partner not found*\nTry again later",
                              data["chat_id"],
                              data["message_id"],
                              parse_mode="Markdown",
                              reply_markup=main_keyboard())
    except Exception:
        bot.send_message(user_id, "❌ *Partner not found*\nTry again later",
                         parse_mode="Markdown",
                         reply_markup=main_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "search_partner")
def search_partner_callback(call):
    begin_search(call)


@bot.callback_query_handler(func=lambda call: call.data == "start_game")
def start_game_callback(call):
    user_id = call.from_user.id

    if user_id not in active_connections:
        bot.answer_callback_query(call.id, "❌ Find a partner first")
        return

    partner_id = active_connections[user_id]

    with game_lock:
        if user_id in game_sessions:
            bot.answer_callback_query(call.id, "❌ A game is already in progress")
            return

        explainer = random.choice([user_id, partner_id])
        guesser = user_id if explainer == partner_id else partner_id

        session = {
            "players": (user_id, partner_id),
            "word": random.choice(config.GAME_WORDS),
            "explainer": explainer,
            "guesser": guesser,
            "effects_on": True,
            "score": {user_id: 0, partner_id: 0},
            "confirmed": set(),
            "prompts": {},
            "started": False,
            "round_id": 1
        }
        game_sessions[user_id] = session
        game_sessions[partner_id] = session

    game_rules = (
        "🎮 *Mini-game “Guess the Word”*\n\n"
        "*How to play:*\n"
        "• One explains the word by voice (without saying it)\n"
        "• The other guesses by typing text\n"
        "• After guessing - switch roles\n"
        f"• {config.GAME_ROUND_TIME} seconds per explanation\n\n"
        "*Tips:*\n"
        "• Describe the object/phenomenon\n"
        "• Use associations"
    )

    for uid in [user_id, partner_id]:
        role = "explainer" if uid == explainer else "guesser"
        bot.send_message(uid,
                         f"{game_rules}\n\n"
                         f"🎯 *Your role:* {role}\n"
                         f"⏱ *Round time:* {config.GAME_ROUND_TIME} seconds\n\n"
                         f"Start the game?",
                         parse_mode="Markdown",
                         reply_markup=game_start_keyboard())

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "confirm_game_start")
def confirm_game_start_callback(call):
    user_id = call.from_user.id

    with game_lock:
        session = game_sessions.get(user_id)
        if not session:
            bot.answer_callback_query(call.id, "❌ Game session not found")
            return
        if session["started"]:
            bot.answer_callback_query(call.id, "ℹ️ The game has already started")
            return

        session["confirmed"].add(user_id)
        session["prompts"][user_id] = (call.message.chat.id, call.message.message_id)
        both_confirmed = len(session["confirmed"]) == 2
        if both_confirmed:
            session["started"] = True
        prompts = dict(session["prompts"])

    if both_confirmed:
        bot.answer_callback_query(call.id, "✅ Game starting!")
        for chat_id, message_id in prompts.values():
            try:
                bot.edit_message_text("🎮 *Game starting!*",
                                      chat_id,
                                      message_id,
                                      parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Could not edit game message: {e}")
        start_game_round(session)
    else:
        bot.answer_callback_query(call.id, "✅ Waiting for partner's consent")
        bot.edit_message_text("⏳ Waiting for partner's consent...",
                              call.message.chat.id,
                              call.message.message_id)


def start_game_round(session):
    word = session["word"]
    explainer = session["explainer"]
    guesser = session["guesser"]
    effects_on = session["effects_on"]
    round_id = session["round_id"]

    bot.send_message(explainer,
                     f"🎯 *Your word:* _{word}_\n\n"
                     f"Explain it with a voice message!\n"
                     f"⏱ You have {config.GAME_ROUND_TIME} seconds",
                     parse_mode="Markdown",
                     reply_markup=chat_keyboard(effects_on=effects_on, in_game=True))

    bot.send_message(guesser,
                     f"🎯 *Guess the word!*\n\n"
                     f"Your partner explains the word by voice\n"
                     f"Write your answer as a text message\n"
                     f"⏱ You have {config.GAME_ROUND_TIME} seconds",
                     parse_mode="Markdown",
                     reply_markup=guesser_keyboard(effects_on=effects_on))

    timer = threading.Timer(config.GAME_ROUND_TIME, game_round_timeout, args=(session, round_id))
    timer.daemon = True
    timer.start()


def advance_round(session, expected_round=None, new_explainer=None):
    """Switches roles, picks a new word and returns the previous word; None if the round is outdated"""
    with game_lock:
        if game_sessions.get(session["players"][0]) is not session:
            return None
        if expected_round is not None and session["round_id"] != expected_round:
            return None

        old_word = session["word"]
        if new_explainer is None:
            new_explainer = session["guesser"]
        player1, player2 = session["players"]
        new_guesser = player2 if new_explainer == player1 else player1

        session["explainer"] = new_explainer
        session["guesser"] = new_guesser
        session["word"] = random.choice(config.GAME_WORDS)
        session["round_id"] += 1
        return old_word


def game_round_timeout(session, round_id):
    old_word = advance_round(session, expected_round=round_id)
    if old_word is None:
        return

    for uid in session["players"]:
        try:
            bot.send_message(uid,
                             f"⏱ *Time is up!*\n\nThe word was: _{old_word}_",
                             parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not send timeout message to {uid}: {e}")

    start_game_round(session)


@bot.callback_query_handler(func=lambda call: call.data == "cancel_game")
def cancel_game_callback(call):
    user_id = call.from_user.id

    with game_lock:
        session = game_sessions.get(user_id)
        if not session or session["started"]:
            bot.answer_callback_query(call.id, "❌ Game session not found")
            return
        for uid in session["players"]:
            game_sessions.pop(uid, None)
        partner_id = session["players"][1] if session["players"][0] == user_id else session["players"][0]

    bot.answer_callback_query(call.id, "❌ Game cancelled")
    bot.edit_message_text("❌ Game cancelled",
                          call.message.chat.id,
                          call.message.message_id,
                          reply_markup=chat_keyboard(in_game=False))

    if partner_id in active_connections:
        bot.send_message(partner_id, "❌ Partner cancelled the game",
                         reply_markup=chat_keyboard(in_game=False))


@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id

    if is_guessing(user_id):
        session = game_sessions[user_id]
        guessed_word = message.text.lower().strip()
        correct_word = session["word"].lower().strip()

        if guessed_word == correct_word:
            round_id = session["round_id"]
            old_word = advance_round(session, expected_round=round_id, new_explainer=user_id)
            if old_word is None:
                return

            partner_id = session["players"][1] if session["players"][0] == user_id else session["players"][0]
            session["score"][user_id] += 1

            score_text = f"🏆 *Score:* {session['score'][user_id]} - {session['score'][partner_id]}"

            bot.send_message(user_id,
                             f"✅ *Correct!*\n\n"
                             f"{score_text}\n\n"
                             f"Now you explain a new word",
                             parse_mode="Markdown")

            bot.send_message(partner_id,
                             f"🎯 *Partner guessed it!*\n\n"
                             f"The word was: _{old_word}_\n"
                             f"🏆 *Score:* {session['score'][partner_id]} - {session['score'][user_id]}\n\n"
                             f"Now you guess",
                             parse_mode="Markdown")

            start_game_round(session)
        else:
            bot.reply_to(message, "❌ Wrong, try again")

    elif user_id in active_connections:
        if is_explaining(user_id):
            bot.reply_to(message, "⚠️ *It's your turn to explain!*\n\n"
                                  "Send a voice message to explain the word.",
                         parse_mode="Markdown")
        else:
            bot.reply_to(message, "⚠️ *Voice messages only!*\n\n"
                                  "Send a voice message or use the buttons below.",
                         parse_mode="Markdown")
    else:
        bot.send_message(user_id, "🎤 *ChronoRoom - voice chat*",
                         parse_mode="Markdown",
                         reply_markup=main_keyboard())


@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'sticker'])
def handle_other_messages(message):
    user_id = message.from_user.id

    if user_id in active_connections:
        if is_guessing(user_id):
            bot.reply_to(message, "⚠️ *You need to guess the word now!*\n\n"
                                  "Write a text message with your answer.",
                         parse_mode="Markdown")
        else:
            bot.reply_to(message, "⚠️ *Voice messages only!*\n\n"
                                  "Use the microphone to record a voice message.",
                         parse_mode="Markdown")


def apply_voice_effect(input_path, output_path, effect_name):
    try:
        if not os.path.exists(input_path):
            return False

        audio_filter = EFFECT_FILTERS.get(effect_name, DEFAULT_EFFECT_FILTER)

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-af", audio_filter,
            "-c:a", "libopus",
            "-b:a", "32k",
            "-ac", "1",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode == 0 and os.path.exists(output_path):
            return True
        else:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Effect error: {e}")
        return False


def remove_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning(f"Could not remove {path}: {e}")


@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id

    if user_id not in active_connections:
        bot.send_message(user_id, "❌ Find a partner first",
                         reply_markup=main_keyboard())
        return

    partner_id = active_connections[user_id]
    effects_on = effects_enabled.get(user_id, True)

    if is_guessing(user_id):
        bot.reply_to(message, "⚠️ *It's your turn to guess!*\n\n"
                              "Write a text message with your answer.",
                     parse_mode="Markdown")
        return

    if message.voice.duration > config.MAX_VOICE_DURATION:
        bot.reply_to(message, f"⚠️ Voice message is too long (max {config.MAX_VOICE_DURATION} seconds)")
        return

    original_path = f"{config.TEMP_FOLDER}/{user_id}_{message.message_id}_original.ogg"
    processed_path = f"{config.TEMP_FOLDER}/{user_id}_{message.message_id}_processed.ogg"

    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with open(original_path, 'wb') as f:
            f.write(downloaded_file)

        processing_msg = bot.send_message(user_id, "🔄 *Processing voice...*",
                                          parse_mode="Markdown")

        sent_original = True
        effect = user_effects.get(user_id)

        if config.ENABLE_EFFECTS and effects_on and effect:
            if apply_voice_effect(original_path, processed_path, effect):
                with open(processed_path, 'rb') as f:
                    bot.send_voice(partner_id, f)
                sent_original = False

        if sent_original:
            with open(original_path, 'rb') as f:
                bot.send_voice(partner_id, f)

        status = "✅ *Voice message sent*"
        if sent_original and config.ENABLE_EFFECTS and effects_on and effect:
            status += " (original)"
        bot.edit_message_text(status,
                              user_id, processing_msg.message_id,
                              parse_mode="Markdown")

    except Exception as e:
        bot.send_message(user_id, "❌ Failed to send the voice message. Try again.")
        logger.error(f"Voice error: {e}")

    finally:
        if not config.SAVE_VOICE_FILES:
            remove_file(original_path)
        remove_file(processed_path)


def leave_dialogue(user_id, partner_id):
    if user_id in game_sessions:
        end_game_for_user(user_id)

    bot.send_message(partner_id, "⚠️ Partner left the dialogue",
                     reply_markup=main_keyboard())

    cleanup_connection(user_id, partner_id)


@bot.callback_query_handler(func=lambda call: call.data == "next_partner")
def next_partner_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]
        leave_dialogue(user_id, partner_id)
        begin_search(call)
    else:
        bot.answer_callback_query(call.id, "❌ No active dialogue")


@bot.callback_query_handler(func=lambda call: call.data == "toggle_effects_chat")
def toggle_effects_chat_callback(call):
    user_id = call.from_user.id

    if user_id in effects_enabled:
        effects_enabled[user_id] = not effects_enabled[user_id]

        status = "enabled" if effects_enabled[user_id] else "disabled"
        bot.answer_callback_query(call.id, f"🎭 Effects {status}")

        in_game = user_id in game_sessions

        bot.edit_message_reply_markup(call.message.chat.id,
                                      call.message.message_id,
                                      reply_markup=chat_keyboard(
                                          effects_on=effects_enabled[user_id],
                                          in_game=in_game))
    else:
        bot.answer_callback_query(call.id, "❌ Toggle error")


@bot.callback_query_handler(func=lambda call: call.data == "toggle_effects_game")
def toggle_effects_game_callback(call):
    user_id = call.from_user.id

    session = game_sessions.get(user_id)
    if session:
        session["effects_on"] = not session["effects_on"]
        for uid in session["players"]:
            effects_enabled[uid] = session["effects_on"]

        status = "enabled" if session["effects_on"] else "disabled"
        bot.answer_callback_query(call.id, f"🎭 Effects {status}")

        if session["explainer"] == user_id:
            keyboard = chat_keyboard(effects_on=session["effects_on"], in_game=True)
        else:
            keyboard = guesser_keyboard(effects_on=session["effects_on"])

        bot.edit_message_reply_markup(call.message.chat.id,
                                      call.message.message_id,
                                      reply_markup=keyboard)
    else:
        bot.answer_callback_query(call.id, "❌ Not in game")


@bot.callback_query_handler(func=lambda call: call.data == "change_word")
def change_word_callback(call):
    user_id = call.from_user.id

    session = game_sessions.get(user_id)
    if session and session["started"]:
        if session["explainer"] != user_id:
            bot.answer_callback_query(call.id, "❌ It's not your turn to explain")
            return

        new_word = random.choice(config.GAME_WORDS)
        session["word"] = new_word

        bot.answer_callback_query(call.id, "🔄 Word changed")
        bot.send_message(user_id,
                         f"🔄 *New word:* _{new_word}_\n\n"
                         f"Explain it with a voice message!",
                         parse_mode="Markdown")
    else:
        bot.answer_callback_query(call.id, "❌ Not in game")


@bot.callback_query_handler(func=lambda call: call.data == "end_game")
def end_game_callback(call):
    user_id = call.from_user.id

    if user_id in game_sessions:
        end_game_for_user(user_id)
        bot.answer_callback_query(call.id, "🏁 Game ended")
        bot.edit_message_text("🏁 *Game ended*",
                              call.message.chat.id,
                              call.message.message_id,
                              parse_mode="Markdown",
                              reply_markup=chat_keyboard(in_game=False))
    else:
        bot.answer_callback_query(call.id, "❌ Not in game")


def end_game_for_user(user_id):
    with game_lock:
        session = game_sessions.get(user_id)
        if not session:
            return
        player1, player2 = session["players"]
        partner_id = player2 if user_id == player1 else player1
        game_sessions.pop(player1, None)
        game_sessions.pop(player2, None)

    if config.ENABLE_STATISTICS and session["started"]:
        words_guessed = sum(session["score"].values())
        record_game_session(player1, player2, words_guessed)

        conn = sqlite3.connect(config.DB_NAME)
        c = conn.cursor()
        for uid, score in session["score"].items():
            if score > 0:
                c.execute(f"UPDATE {config.DB_TABLE_USERS} SET game_wins = game_wins + 1 WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()

    if partner_id in active_connections:
        bot.send_message(partner_id, "🏁 *Game ended*",
                         parse_mode="Markdown",
                         reply_markup=chat_keyboard(in_game=False))


@bot.callback_query_handler(func=lambda call: call.data.startswith("effect_"))
def effect_selection_callback(call):
    user_id = call.from_user.id

    effect_map = {
        "effect_robot": "🤖 Robot",
        "effect_demon": "👹 Demon",
        "effect_chipmunk": "🐿️ Chipmunk"
    }

    if call.data == "effect_random":
        selected_effect = random.choice(config.EFFECTS)
    elif call.data in effect_map:
        selected_effect = effect_map[call.data]
    else:
        bot.answer_callback_query(call.id, "❌ Unknown effect")
        return

    user_effects[user_id] = selected_effect

    bot.answer_callback_query(call.id, f"✅ Set: {selected_effect}")
    bot.edit_message_text(f"🎭 *Effect set:* {selected_effect}",
                          call.message.chat.id,
                          call.message.message_id,
                          parse_mode="Markdown", reply_markup=voice_selection_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "voice_selection")
def voice_selection_menu_callback(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("🎭 *Voice effect selection*\n\n"
                          "Choose an effect for your voice:",
                          call.message.chat.id,
                          call.message.message_id,
                          parse_mode="Markdown",
                          reply_markup=voice_selection_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "request_username")
def request_username_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]

        bot.send_message(partner_id,
                         "🎭 *Username request*\nShow your username to the partner?",
                         parse_mode="Markdown",
                         reply_markup=request_username_keyboard())

        bot.answer_callback_query(call.id, "📨 Request sent")
    else:
        bot.answer_callback_query(call.id, "❌ No active dialogue")


@bot.callback_query_handler(func=lambda call: call.data == "show_username")
def show_username_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]
        username = call.from_user.username

        if username:
            bot.send_message(partner_id, f"👤 @{username}")
            bot.answer_callback_query(call.id, "✅ Sent")
        else:
            bot.answer_callback_query(call.id, "❌ You don't have a username")
    else:
        bot.answer_callback_query(call.id, "❌ Dialogue ended")


@bot.callback_query_handler(func=lambda call: call.data == "hide_username")
def hide_username_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]
        bot.send_message(partner_id, "❌ Partner kept anonymity")
        bot.answer_callback_query(call.id, "✅ Anonymity preserved")
    else:
        bot.answer_callback_query(call.id, "❌ Dialogue ended")


@bot.callback_query_handler(func=lambda call: call.data == "go_home")
def go_home_callback(call):
    user_id = call.from_user.id

    with search_lock:
        waiting_users.pop(user_id, None)

    if user_id in active_connections:
        partner_id = active_connections[user_id]
        leave_dialogue(user_id, partner_id)

    bot.answer_callback_query(call.id, "🏠 Main menu")
    bot.edit_message_text("⏳ *ChronoRoom*",
                          call.message.chat.id,
                          call.message.message_id,
                          parse_mode="Markdown",
                          reply_markup=main_keyboard())


def cleanup_connection(user_id, partner_id):
    with search_lock:
        active_connections.pop(partner_id, None)
        active_connections.pop(user_id, None)

    for uid in (user_id, partner_id):
        user_effects.pop(uid, None)
        effects_enabled.pop(uid, None)
        game_sessions.pop(uid, None)


@bot.callback_query_handler(func=lambda call: call.data == "show_menu")
def show_menu_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.from_user.id, "⚙️ *ChronoRoom Menu*",
                     parse_mode="Markdown",
                     reply_markup=menu_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "back_to_chat")
def back_to_chat_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        in_game = is_guessing(user_id) or is_explaining(user_id)
        effects_on = effects_enabled.get(user_id, True)

        if in_game:
            if is_explaining(user_id):
                text = "🎮 *Game continues*\n\nYou explain the word by voice"
                keyboard = chat_keyboard(effects_on=effects_on, in_game=True)
            else:
                text = "🎮 *Game continues*\n\nYou guess the word by text"
                keyboard = guesser_keyboard(effects_on=effects_on)
        else:
            text = "💬 *Dialogue active*\n\nSend voice messages"
            keyboard = chat_keyboard(effects_on=effects_on, in_game=False)

        bot.answer_callback_query(call.id, "💬 Returning to chat")
        bot.edit_message_text(text, call.message.chat.id,
                              call.message.message_id,
                              parse_mode="Markdown",
                              reply_markup=keyboard)
    else:
        bot.answer_callback_query(call.id, "🏠 Returning to menu")
        bot.edit_message_text("⏳ *ChronoRoom*",
                              call.message.chat.id,
                              call.message.message_id,
                              parse_mode="Markdown",
                              reply_markup=main_keyboard())


def auto_change_effects():
    while True:
        time.sleep(config.EFFECT_CHANGE_TIME)
        for user_id in list(user_effects.keys()):
            if user_id in active_connections and effects_enabled.get(user_id, True):
                user_effects[user_id] = random.choice(config.EFFECTS)


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set")

    init_db()
    cleanup_on_start()

    logger.info("ChronoRoom started")
    logger.info(f"Effects: {len(config.EFFECTS)}")
    logger.info(f"Words for game: {len(config.GAME_WORDS)}")
    logger.info(f"Channel: {config.CHANNEL_LINK}")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        logger.info("FFmpeg installed")
    except FileNotFoundError:
        logger.warning("FFmpeg not found")

    os.makedirs(config.TEMP_FOLDER, exist_ok=True)

    for filename in os.listdir(config.TEMP_FOLDER):
        file_path = os.path.join(config.TEMP_FOLDER, filename)
        if os.path.isfile(file_path):
            remove_file(file_path)

    threading.Thread(target=auto_change_effects, daemon=True).start()

    logger.info("Bot is running...")
    bot.infinity_polling(interval=0)


if __name__ == "__main__":
    main()
