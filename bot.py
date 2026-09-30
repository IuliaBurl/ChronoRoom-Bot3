import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
import time
import random
import os
import subprocess
from threading import Thread
import queue
import sqlite3

bot = telebot.TeleBot(config.BOT_TOKEN)

search_queue = queue.Queue()
active_connections = {}
user_effects = {}
game_sessions = {}
effects_enabled = {}

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

init_db()

def get_or_create_user(user_id, username=None):
    conn = sqlite3.connect(config.DB_NAME)
    c = conn.cursor()
    c.execute(f"SELECT * FROM {config.DB_TABLE_USERS} WHERE user_id = ?", (user_id,))
    user = c.fetchone()

    if not user:
        c.execute(f"INSERT INTO {config.DB_TABLE_USERS} (user_id, username) VALUES (?, ?)",
                  (user_id, username))
        conn.commit()
    conn.close()

def update_stats(user_id, field):
    if not config.ENABLE_STATISTICS:
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

def check_subscription(user_id):                                                     if not config.CHECK_SUBSCRIPTION:
        return True
    try:
        member = bot.get_chat_member(config.CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
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
        )                                                                            else:
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
    global search_queue, active_connections, user_effects, game_sessions, effects_enabled

    while not search_queue.empty():
        try:
            search_queue.get_nowait()
        except:
            break

    active_connections.clear()
    user_effects.clear()
    game_sessions.clear()
    effects_enabled.clear()

    print("✅ Previous session data cleared")

cleanup_on_start()

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username
                                                                                     get_or_create_user(user_id, username)
    effects_enabled[user_id] = True

    if check_subscription(user_id):
        send_welcome(user_id)
    else:
        bot.send_message(user_id,
                         "⏳ *ChronoRoom*\n\n"
                         "Voice chat with altered voice\n\n"
                         "📢 Subscribe to the channel for access:",                                       parse_mode="Markdown",
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

@bot.callback_query_handler(func=lambda call: call.data == "search_partner")
def search_partner_callback(call):
    user_id = call.from_user.id
                                                                                     if user_id in active_connections:
        bot.answer_callback_query(call.id, "❌ End the current dialogue first")
        return

    search_id = f"{user_id}_{int(time.time())}"

    bot.answer_callback_query(call.id, "🔍 Starting search...")
    bot.edit_message_text("🔮 *Searching for a partner...*\n⏱ 30 seconds",
                         call.message.chat.id,                                                            call.message.message_id,
                         parse_mode="Markdown")

    search_queue.put((user_id, call.message.chat.id, call.message.message_id))

    Thread(target=find_partner, args=(user_id, call.message.chat.id, call.message.message_id, search_id), daemon=True).start()

def find_partner(user_id, chat_id, message_id, search_id):
    """Partner search - fixed version"""
    timeout = time.time() + config.SEARCH_TIMEOUT

    while time.time() < timeout:
        try:
            while not search_queue.empty():
                try:
                    potential_data = search_queue.get_nowait()
                    potential_user_id = potential_data[0]
                    potential_chat_id = potential_data[1]
                    potential_message_id = potential_data[2]

                    if (potential_user_id != user_id and
                        potential_user_id not in active_connections and
                        user_id not in active_connections):

                        active_connections[user_id] = potential_user_id
                        active_connections[potential_user_id] = user_id

                        user_effects[user_id] = random.choice(config.EFFECTS)
                        user_effects[potential_user_id] = random.choice(config.EFFECTS)

                        effects_enabled[user_id] = True
                        effects_enabled[potential_user_id] = True

                        success_text = "✅ *Partner found*\n\n🎤 Send voice messages"

                        try:
                            bot.edit_message_text(success_text,
                                                chat_id,
                                                message_id,
                                                parse_mode="Markdown",
                                                reply_markup=chat_keyboard())
                        except:
                            bot.send_message(user_id, success_text,
                                           parse_mode="Markdown",
                                           reply_markup=chat_keyboard())

                        try:
                            bot.edit_message_text(success_text,
                                                potential_chat_id,
                                                potential_message_id,
                                                parse_mode="Markdown",
                                                reply_markup=chat_keyboard())
                        except:
                            bot.send_message(potential_user_id, success_text,
                                           parse_mode="Markdown",
                                           reply_markup=chat_keyboard())

                        update_stats(user_id, 'connections')
                        update_stats(potential_user_id, 'connections')

                        print(f"✅ Connection: {user_id} <-> {potential_user_id}")
                        return

                    else:
                        # Doesn't match, put it back
                        search_queue.put(potential_data)
                        break

                except queue.Empty:
                    break

            time.sleep(0.5)

        except Exception as e:
            print(f"Search error: {e}")
            break

    try:
        bot.edit_message_text("❌ *Partner not found*\nTry again later",
                             chat_id,
                             message_id,
                             parse_mode="Markdown",
                             reply_markup=main_keyboard())
    except:
        bot.send_message(user_id, "❌ *Partner not found*\nTry again later",
                        parse_mode="Markdown",
                        reply_markup=main_keyboard())

    cleanup_queue(user_id)

def cleanup_queue(user_id):
    """Removes a user from the search queue"""
    temp_queue = queue.Queue()
    found = False

    while not search_queue.empty():
        try:
            data = search_queue.get_nowait()
            if data[0] != user_id:
                temp_queue.put(data)
            else:
                found = True
        except:
            break

    while not temp_queue.empty():
        try:
            search_queue.put(temp_queue.get_nowait())
        except:
            break

    return found

@bot.callback_query_handler(func=lambda call: call.data == "start_game")
def start_game_callback(call):                                                       user_id = call.from_user.id

    if user_id not in active_connections:
        bot.answer_callback_query(call.id, "❌ Find a partner first")
        return

    partner_id = active_connections[user_id]
                                                                                     # Send game invitation
    game_rules = (
        "🎮 *Mini-game “Guess the Word”*\n\n"
        "*How to play:*\n"
        "• One explains the word by voice (without saying it)\n"
        "• The other guesses by typing text\n"
        "• After guessing - switch roles\n"
        "• 60 seconds per explanation\n\n"
        "*Tips:*\n"
        "• Describe the object/phenomenon\n"
        "• Use associations"
    )

    explainer = random.choice([user_id, partner_id])
    guesser = user_id if explainer == partner_id else partner_id
    word = random.choice(config.GAME_WORDS)                                      
    game_sessions[user_id] = {
        "partner": partner_id,
        "word": word,
        "explainer": explainer,
        "guesser": guesser,
        "effects_on": True,
        "score": {user_id: 0, partner_id: 0},
        "confirmed": False
    }
    game_sessions[partner_id] = {
        "partner": user_id,
        "word": word,
        "explainer": explainer,
        "guesser": guesser,                                                              "effects_on": True,
        "score": {user_id: 0, partner_id: 0},
        "confirmed": False
    }

    for uid in [user_id, partner_id]:
        role = "explainer" if uid == explainer else "guesser"

        bot.send_message(uid,
                        f"{game_rules}\n\n"
                        f"🎯 *Your role:* {role}\n"
                        f"⏱ *Round time:* 60 seconds\n\n"
                        f"Start the game?",
                        parse_mode="Markdown",
                        reply_markup=game_start_keyboard())

    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_game_start")
def confirm_game_start_callback(call):
    user_id = call.from_user.id

    if user_id not in game_sessions:
        bot.answer_callback_query(call.id, "❌ Game session not found")
        return

    session = game_sessions[user_id]                                                 partner_id = session["partner"]

    session["confirmed"] = True

    if game_sessions[partner_id].get("confirmed", False):
        bot.answer_callback_query(call.id, "✅ Game starting!")
        bot.edit_message_text("🎮 *Game starting!*",
                            call.message.chat.id,
                            call.message.message_id,
                            parse_mode="Markdown")
        start_game_round(user_id, partner_id)
    else:
        bot.answer_callback_query(call.id, "✅ Waiting for partner's consent")
        bot.edit_message_text("⏳ Waiting for partner's consent...",
                            call.message.chat.id,
                            call.message.message_id)

def start_game_round(user_id, partner_id):
    session_u = game_sessions[user_id]

    word = session_u["word"]
    explainer = session_u["explainer"]
    guesser = session_u["guesser"]
    effects_on = session_u["effects_on"]

    # Send word to explainer
    bot.send_message(explainer,
                    f"🎯 *Your word:* _{word}_\n\n"
                    f"Explain it with a voice message!\n"
                    f"⏱ You have 60 seconds",
                    parse_mode="Markdown",
                    reply_markup=chat_keyboard(effects_on=effects_on, in_game=True))

    bot.send_message(guesser,
                    f"🎯 *Guess the word!*\n\n"
                    f"Your partner explains the word by voice\n"
                    f"Write your answer as a text message\n"
                    f"⏱ You have 60 seconds",
                    parse_mode="Markdown",
                    reply_markup=guesser_keyboard(effects_on=effects_on))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_game")
def cancel_game_callback(call):
    user_id = call.from_user.id                                                  
    if user_id in game_sessions:
        partner_id = game_sessions[user_id]["partner"]

        if user_id in game_sessions:
            del game_sessions[user_id]
        if partner_id in game_sessions:
            del game_sessions[partner_id]

        bot.answer_callback_query(call.id, "❌ Game cancelled")
        bot.edit_message_text("❌ Game cancelled by partner",
                             call.message.chat.id,
                             call.message.message_id,
                             reply_markup=chat_keyboard(in_game=False))

        if partner_id in active_connections:
            bot.send_message(partner_id, "❌ Partner cancelled the game",
                            reply_markup=chat_keyboard(in_game=False))

@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id

    if user_id in game_sessions and game_sessions[user_id]["guesser"] == user_id:
        session = game_sessions[user_id]
        guessed_word = message.text.lower().strip()
        correct_word = session["word"].lower().strip()

        if guessed_word == correct_word:
            partner_id = session["partner"]

            session["score"][user_id] += 1
            game_sessions[partner_id]["score"][user_id] += 1

            new_explainer = user_id
            new_guesser = partner_id

            session["explainer"] = new_explainer                                             session["guesser"] = new_guesser
            game_sessions[partner_id]["explainer"] = new_explainer
            game_sessions[partner_id]["guesser"] = new_guesser
            new_word = random.choice(config.GAME_WORDS)
            session["word"] = new_word
            game_sessions[partner_id]["word"] = new_word

            score_text = f"🏆 *Score:* {session['score'][user_id]} - {session['score'][partner_id]}"

            bot.send_message(user_id,
                            f"✅ *Correct!*\n\n"
                            f"{score_text}\n\n"
                            f"Now you explain a new word",
                            parse_mode="Markdown")

            bot.send_message(partner_id,
                            f"🎯 *Partner guessed it!*\n\n"
                            f"The word was: _{correct_word}_\n"
                            f"{score_text}\n\n"
                            f"Now you guess",
                            parse_mode="Markdown")

            start_game_round(user_id, partner_id)
        else:
            bot.reply_to(message, "❌ Wrong, try again")

    elif user_id in active_connections:
        if user_id in game_sessions:
            bot.reply_to(message, "⚠️ *It's your turn to explain!*\n\n"
                         "Send a voice message to explain the word.")
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
    if user_id in active_connections:                                                    if user_id in game_sessions and game_sessions[user_id]["guesser"] == user_id:
            bot.reply_to(message, "⚠️ *You need to guess the word now!*\n\n"
                         "Write a text message with your answer.")
        else:
            bot.reply_to(message, "⚠️ *Voice messages only!*\n\n"
                         "Use the microphone to record a voice message.",
                         parse_mode="Markdown")

def apply_voice_effect(input_path, output_path, effect_name):
    try:
        if not os.path.exists(input_path):
            return False

        effect_commands = {
            "🤖 Robot": f"ffmpeg -i {input_path} -af 'asetrate=44100*0.8,aresample=44100' -ac 1 {output_path} -y",
            "👹 Demon": f"ffmpeg -i {input_path} -af 'asetrate=44100*0.7,aresample=44100' {output_path} -y",
            "🐿️ Chipmunk": f"ffmpeg -i {input_path} -af 'asetrate=44100*1.5,aresample=44100' {output_path} -y"
        }

        if effect_name in effect_commands:
            cmd = effect_commands[effect_name]
        else:
            cmd = f"ffmpeg -i {input_path} -af 'asetrate=44100*0.8,aresample=44100' {output_path} -y"

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0 and os.path.exists(output_path):
            return True
        else:
            print(f"FFmpeg error: {result.stderr}")
            return False

    except Exception as e:
        print(f"Effect error: {e}")
        return False

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]

        effects_on = effects_enabled.get(user_id, True)

        if user_id in game_sessions and game_sessions[user_id]["guesser"] == user_id:
            bot.reply_to(message, "⚠️ *It's your turn to guess!*\n\n"
                         "Write a text message with your answer.")
            return

        try:
            file_info = bot.get_file(message.voice.file_id)
            downloaded_file = bot.download_file(file_info.file_path)

            original_path = f"{config.TEMP_FOLDER}/{user_id}_original.ogg"
            with open(original_path, 'wb') as f:
                f.write(downloaded_file)

            processing_msg = bot.send_message(user_id, "🔄 *Processing voice...*",
                                              parse_mode="Markdown")

            if effects_on and user_id in user_effects:
                effect = user_effects[user_id]
                processed_path = f"{config.TEMP_FOLDER}/{user_id}_processed.ogg"

                if apply_voice_effect(original_path, processed_path, effect):
                    with open(processed_path, 'rb') as f:
                        bot.send_voice(partner_id, f)

                    bot.edit_message_text("✅ *Voice message sent*",
                                          user_id, processing_msg.message_id,
                                          parse_mode="Markdown")
                else:
                    with open(original_path, 'rb') as f:
                        bot.send_voice(partner_id, f)

                    bot.edit_message_text("✅ *Voice message sent* (original)",
                                          user_id, processing_msg.message_id,
                                          parse_mode="Markdown")
            else:
                with open(original_path, 'rb') as f:
                    bot.send_voice(partner_id, f)

                bot.edit_message_text("✅ *Voice message sent*",
                                      user_id, processing_msg.message_id,
                                      parse_mode="Markdown")

            if not config.SAVE_VOICE_FILES:
                if os.path.exists(original_path):
                    os.remove(original_path)
                if os.path.exists(f"{config.TEMP_FOLDER}/{user_id}_processed.ogg"):
                    os.remove(f"{config.TEMP_FOLDER}/{user_id}_processed.ogg")

        except Exception as e:
            bot.send_message(user_id, f"❌ Sending error: {str(e)}")
            print(f"Voice error: {e}")
    else:
        bot.send_message(user_id, "❌ Find a partner first",
                         reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "next_partner")
def next_partner_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        partner_id = active_connections[user_id]

        if user_id in game_sessions:
            end_game_for_user(user_id)

        bot.send_message(partner_id, "⚠️ Partner left the dialogue",
                        reply_markup=main_keyboard())

        cleanup_connection(user_id, partner_id)

    bot.answer_callback_query(call.id, "🔄 Searching for a new partner")
    bot.edit_message_text("⏳ *ChronoRoom*",
                         call.message.chat.id,
                         call.message.message_id,
                         parse_mode="Markdown",
                         reply_markup=main_keyboard())

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

    if user_id in game_sessions:
        session = game_sessions[user_id]
        session["effects_on"] = not session["effects_on"]
        effects_enabled[user_id] = session["effects_on"]

        if user_id in active_connections:
            partner_id = active_connections[user_id]
            if partner_id in game_sessions:
                game_sessions[partner_id]["effects_on"] = session["effects_on"]
            effects_enabled[partner_id] = session["effects_on"]

        status = "enabled" if session["effects_on"] else "disabled"
        bot.answer_callback_query(call.id, f"🎭 Effects {status}")

        is_explainer = session["explainer"] == user_id
        if is_explainer:
            bot.edit_message_reply_markup(call.message.chat.id,
                                         call.message.message_id,
                                         reply_markup=chat_keyboard(
                                             effects_on=session["effects_on"],
                                             in_game=True))
        else:
            bot.edit_message_reply_markup(call.message.chat.id,
                                         call.message.message_id,
                                         reply_markup=guesser_keyboard(
                                             effects_on=session["effects_on"]))
    else:
        bot.answer_callback_query(call.id, "❌ Not in game")

@bot.callback_query_handler(func=lambda call: call.data == "change_word")
def change_word_callback(call):
    user_id = call.from_user.id

    if user_id in game_sessions:
        session = game_sessions[user_id]

        if session["explainer"] != user_id:
            bot.answer_callback_query(call.id, "❌ It's not your turn to explain")
            return

        partner_id = session["partner"]

        new_word = random.choice(config.GAME_WORDS)
        session["word"] = new_word
        if partner_id in game_sessions:
            game_sessions[partner_id]["word"] = new_word

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
    if user_id in game_sessions:
        partner_id = game_sessions[user_id]["partner"]

        if config.ENABLE_STATISTICS:
            words_guessed = sum(game_sessions[user_id]["score"].values())
            record_game_session(user_id, partner_id, words_guessed)

            conn = sqlite3.connect(config.DB_NAME)
            c = conn.cursor()
            for uid, score in game_sessions[user_id]["score"].items():
                if score > 0:
                    c.execute(f"UPDATE {config.DB_TABLE_USERS} SET game_wins = game_wins + 1 WHERE user_id = ?", (uid,))
            conn.commit()
            conn.close()

        del game_sessions[user_id]
        if partner_id in game_sessions:
            del game_sessions[partner_id]

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

    if user_id in active_connections:
        partner_id = active_connections[user_id]

    bot.answer_callback_query(call.id, f"✅ Set: {selected_effect}")

    bot.edit_message_text(f"🎭 *Effect set:* {selected_effect}",
                         call.message.chat.id,
                         call.message.message_id,
                         parse_mode="Markdown",                                                           reply_markup=voice_selection_keyboard())

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

@bot.callback_query_handler(func=lambda call: call.data == "go_home")            def go_home_callback(call):
    user_id = call.from_user.id                                                  
    if user_id in active_connections:
        if user_id in game_sessions:
            end_game_for_user(user_id)

        partner_id = active_connections[user_id]
                                                                                         bot.send_message(partner_id, "⚠️ Partner left the dialogue",
                        reply_markup=main_keyboard())

        cleanup_connection(user_id, partner_id)

    bot.answer_callback_query(call.id, "🏠 Main menu")
    bot.edit_message_text("⏳ *ChronoRoom*",
                         call.message.chat.id,
                         call.message.message_id,
                         parse_mode="Markdown",
                         reply_markup=main_keyboard())

def cleanup_connection(user_id, partner_id):
    if partner_id in active_connections:
        del active_connections[partner_id]
    if user_id in active_connections:
        del active_connections[user_id]

    if user_id in user_effects:
        del user_effects[user_id]
    if partner_id in user_effects:
        del user_effects[partner_id]

    if user_id in effects_enabled:
        del effects_enabled[user_id]
    if partner_id in effects_enabled:
        del effects_enabled[partner_id]

    if user_id in game_sessions:
        del game_sessions[user_id]
    if partner_id in game_sessions:
        del game_sessions[partner_id]

@bot.callback_query_handler(func=lambda call: call.data == "show_menu")          def show_menu_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.from_user.id, "⚙️ *ChronoRoom Menu*",
                    parse_mode="Markdown",
                    reply_markup=menu_keyboard())

@bot.callback_query_handler(func=lambda call: call.data == "back_to_chat")
def back_to_chat_callback(call):
    user_id = call.from_user.id

    if user_id in active_connections:
        in_game = user_id in game_sessions
        effects_on = effects_enabled.get(user_id, True)

        if in_game:
            is_explainer = game_sessions[user_id]["explainer"] == user_id
            if is_explainer:
                text = "🎮 *Game continues*\n\nYou explain the word by voice"
                keyboard = chat_keyboard(effects_on=effects_on, in_game=True)
            else:
                text = "🎮 *Game continues*\n\nYou guess the word by text"                       keyboard = guesser_keyboard(effects_on=effects_on)
        else:
            text = "💬 *Dialogue active*\n\nSend voice messages"
            keyboard = chat_keyboard(effects_on=effects_on, in_game=False)

        bot.answer_callback_query(call.id, "💬 Returning to chat")
        bot.edit_message_text(text,                                                                           call.message.chat.id,
                             call.message.message_id,
                             parse_mode="Markdown",
                             reply_markup=keyboard)
    else:                                                                                bot.answer_callback_query(call.id, "🏠 Returning to menu")
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
    print("⏳ ChronoRoom started")
    print(f"🎭 Effects: {len(config.EFFECTS)}")
    print(f"🎮 Words for game: {len(config.GAME_WORDS)}")
    print(f"📢 Channel: {config.CHANNEL_LINK}")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        print("✅ FFmpeg installed")
    except:
        print("⚠️ FFmpeg not found")

    if not os.path.exists(config.TEMP_FOLDER):
        os.makedirs(config.TEMP_FOLDER)

    for filename in os.listdir(config.TEMP_FOLDER):
        file_path = os.path.join(config.TEMP_FOLDER, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except:
            pass

    Thread(target=auto_change_effects, daemon=True).start()

    print("⚡ Bot is running...")
    bot.polling(none_stop=True, interval=0)
