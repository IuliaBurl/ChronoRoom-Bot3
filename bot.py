import logging
import os
import subprocess
import threading
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from state import ChatState
from voice import apply_voice_effect, remove_file

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(config.BOT_TOKEN)

# All shared in-memory state (search queue, dialogues, effects, games) lives here.
# It is thread-safe: see state.py.
state = ChatState(config.EFFECTS, config.GAME_WORDS)


# ---------------------------------------------------------------------------
# Safe Telegram helpers.
# Telegram calls fail for ordinary reasons (the partner blocked the bot, a button was
# pressed on an old message, "message is not modified"). A failed notification must never
# abort the handler halfway and leave the dialogue state inconsistent.
# ---------------------------------------------------------------------------

def safe_answer(call_id, text=None, show_alert=False):
    try:
        bot.answer_callback_query(call_id, text, show_alert=show_alert)
    except Exception as e:
        logger.debug(f"Could not answer callback: {e}")


def safe_send(chat_id, text, **kwargs):
    """Returns the sent message, or None if Telegram refused (e.g. the user blocked the bot)."""
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.warning(f"Could not send message to {chat_id}: {e}")
        return None


def safe_edit(text, chat_id, message_id, **kwargs) -> bool:
    """True if the message now shows the text (including 'message is not modified')."""
    try:
        bot.edit_message_text(text, chat_id, message_id, **kwargs)
        return True
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return True
        logger.warning(f"Could not edit message {message_id} in {chat_id}: {e}")
        return False


def edit_or_send(chat_id, message_id, text, **kwargs):
    if not safe_edit(text, chat_id, message_id, **kwargs):
        safe_send(chat_id, text, **kwargs)


def safe_edit_markup(chat_id, message_id, markup):
    try:
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=markup)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Could not edit markup of {message_id} in {chat_id}: {e}")


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

def check_subscription(user_id):
    """Fails closed: if Telegram cannot confirm the membership, access is not granted."""
    if not config.CHECK_SUBSCRIPTION:
        return True
    try:
        member = bot.get_chat_member(config.CHANNEL_ID, user_id)
    except Exception as e:
        logger.warning(f"Subscription check error: {e}")
        return False
    if member.status in ('member', 'administrator', 'creator'):
        return True
    return member.status == 'restricted' and bool(getattr(member, 'is_member', False))


def send_subscription_prompt(user_id):
    safe_send(user_id,
              "⏳ *ChronoRoom*\n\n"
              "Voice chat with altered voice\n\n"
              "📢 Subscribe to the channel for access:", parse_mode="Markdown",
              reply_markup=start_keyboard())


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

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

    markup.row(
        InlineKeyboardButton("🎭 Voice", callback_data="voice_selection"),
        InlineKeyboardButton("📱 Home", callback_data="go_home")
    )
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


def keyboard_for(user_id):
    """The right chat keyboard for what this user is doing right now."""
    view = state.chat_view(user_id)
    if view["in_game"] and view["role"] == "guesser":
        return guesser_keyboard(effects_on=view["effects_on"])
    return chat_keyboard(effects_on=view["effects_on"], in_game=view["in_game"])


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
    markup.row(InlineKeyboardButton("↩️ Back", callback_data="back_to_chat"))
    return markup


def game_start_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Start game", callback_data="confirm_game_start"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_game")
    )
    return markup


def request_username_keyboard(requester_id):
    """The buttons carry the id of the person who asked, so an old request can never
    reveal the username to somebody else (a later partner)."""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Show", callback_data=f"show_username:{requester_id}"),
        InlineKeyboardButton("❌ Hide", callback_data=f"hide_username:{requester_id}")
    )
    return markup


# ---------------------------------------------------------------------------
# Start / subscription
# ---------------------------------------------------------------------------

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    database.get_or_create_user(user_id, message.from_user.username)

    if check_subscription(user_id):
        send_welcome(user_id)
    else:
        send_subscription_prompt(user_id)


@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription_callback(call):
    user_id = call.from_user.id

    if check_subscription(user_id):
        safe_answer(call.id, "✅ Access granted")
        edit_or_send(call.message.chat.id, call.message.message_id, "⏳ *ChronoRoom*",
                     parse_mode="Markdown", reply_markup=main_keyboard())
    else:
        safe_answer(call.id, "❌ You are not subscribed", show_alert=True)


def send_welcome(user_id):
    welcome_text = (
        "⏳ *ChronoRoom*\n\n"
        "Voice chat with altered voice\n\n"
        "• Random partner\n"
        "• Voice changes automatically\n"
        "• Mini-game “Guess the Word”\n"
        "• Complete anonymity"
    )
    safe_send(user_id, welcome_text, parse_mode="Markdown", reply_markup=main_keyboard())


# ---------------------------------------------------------------------------
# Matchmaking
# ---------------------------------------------------------------------------

def show_partner_found(user_id, chat_id, message_id):
    edit_or_send(chat_id, message_id, "✅ *Partner found*\n\n🎤 Send voice messages",
                 parse_mode="Markdown", reply_markup=chat_keyboard())


def begin_search(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    # The gate must protect the feature itself, not just /start: otherwise anyone could
    # skip the subscription by sending any text and pressing "Find a partner".
    if not check_subscription(user_id):
        safe_answer(call.id, "📢 Subscribe to the channel first", show_alert=True)
        send_subscription_prompt(user_id)
        return

    status = state.search_status(user_id)
    if status == "connected":
        safe_answer(call.id, "❌ End the current dialogue first")
        return
    if status == "searching":
        safe_answer(call.id, "🔍 Already searching...")
        return

    safe_answer(call.id, "🔍 Starting search...")
    # Not everybody starts with /start (any text shows the menu); without a row the
    # statistics updates below would silently do nothing.
    database.get_or_create_user(user_id, call.from_user.username)
    database.update_stats(user_id, 'searches')

    # Shown BEFORE joining the queue: once queued, another user may match us and edit
    # this very message to "Partner found", which a late "Searching..." must not overwrite.
    safe_edit(f"🔮 *Searching for a partner...*\n⏱ {config.SEARCH_TIMEOUT} seconds",
              chat_id, message_id, parse_mode="Markdown")

    result, payload = state.find_partner_or_wait(user_id, chat_id, message_id)

    if result == "matched":
        partner_id, partner_data = payload
        show_partner_found(user_id, chat_id, message_id)
        show_partner_found(partner_id, partner_data["chat_id"], partner_data["message_id"])
        database.update_stats(user_id, 'connections')
        database.update_stats(partner_id, 'connections')
        logger.info(f"Connection: {user_id} <-> {partner_id}")
    elif result == "waiting":
        timer = threading.Timer(config.SEARCH_TIMEOUT, search_timeout, args=(user_id, payload))
        timer.daemon = True
        timer.start()


def search_timeout(user_id, token):
    data = state.expire_search(user_id, token)
    if data is None:
        return

    edit_or_send(data["chat_id"], data["message_id"], "❌ *Partner not found*\nTry again later",
                 parse_mode="Markdown", reply_markup=main_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "search_partner")
def search_partner_callback(call):
    begin_search(call)


def finish_game_stats(game):
    """Stores the result of a game that was actually played."""
    if not game or not game["started"]:
        return
    player1, player2 = game["players"]
    database.record_game_session(player1, player2, game["score"][player1], game["score"][player2])


def leave_dialogue(user_id) -> bool:
    """Ends the user's dialogue. Returns False if there was none.

    The state is cleaned up FIRST and atomically; telling the partner is best effort.
    (Previously a partner who had blocked the bot made this raise before the cleanup,
    and the other user stayed 'in a dialogue' forever.)
    """
    partner_id, game = state.disconnect(user_id)
    if partner_id is None:
        return False
    finish_game_stats(game)
    safe_send(partner_id, "⚠️ Partner left the dialogue", reply_markup=main_keyboard())
    return True


def drop_unreachable_partner(user_id):
    """The partner blocked the bot / deleted the chat: free the user instead of failing forever."""
    _, game = state.disconnect(user_id)
    finish_game_stats(game)
    safe_send(user_id, "⚠️ Your partner is no longer available.\nFind a new one:",
              reply_markup=main_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "next_partner")
def next_partner_callback(call):
    if leave_dialogue(call.from_user.id):
        begin_search(call)
    else:
        safe_answer(call.id, "❌ No active dialogue")


@bot.callback_query_handler(func=lambda call: call.data == "go_home")
def go_home_callback(call):
    user_id = call.from_user.id

    state.cancel_search(user_id)
    leave_dialogue(user_id)

    safe_answer(call.id, "🏠 Main menu")
    edit_or_send(call.message.chat.id, call.message.message_id, "⏳ *ChronoRoom*",
                 parse_mode="Markdown", reply_markup=main_keyboard())


# ---------------------------------------------------------------------------
# Mini-game "Guess the Word"
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data == "start_game")
def start_game_callback(call):
    user_id = call.from_user.id

    status, session = state.create_game(user_id)
    if status == "no_dialogue":
        safe_answer(call.id, "❌ Find a partner first")
        return
    if status == "exists":
        safe_answer(call.id, "❌ A game is already in progress")
        return

    explainer = session["explainer"]
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

    for uid in session["players"]:
        role = "explainer" if uid == explainer else "guesser"
        safe_send(uid,
                  f"{game_rules}\n\n"
                  f"🎯 *Your role:* {role}\n"
                  f"⏱ *Round time:* {config.GAME_ROUND_TIME} seconds\n\n"
                  f"Start the game?",
                  parse_mode="Markdown",
                  reply_markup=game_start_keyboard())

    safe_answer(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "confirm_game_start")
def confirm_game_start_callback(call):
    user_id = call.from_user.id
    chat_id, message_id = call.message.chat.id, call.message.message_id

    status, session, prompts = state.confirm_game(user_id, chat_id, message_id)

    if status == "no_session":
        safe_answer(call.id, "❌ Game session not found")
    elif status == "already_started":
        safe_answer(call.id, "ℹ️ The game has already started")
    elif status == "started":
        safe_answer(call.id, "✅ Game starting!")
        for prompt_chat_id, prompt_message_id in prompts:
            safe_edit("🎮 *Game starting!*", prompt_chat_id, prompt_message_id, parse_mode="Markdown")
        start_game_round(session)
    else:
        safe_answer(call.id, "✅ Waiting for partner's consent")
        safe_edit("⏳ Waiting for partner's consent...", chat_id, message_id)


def start_game_round(session):
    snap = state.round_snapshot(session)
    if snap is None:      # the game ended while we were getting here
        return

    word, effects_on = snap["word"], snap["effects_on"]

    safe_send(snap["explainer"],
              f"🎯 *Your word:* _{word}_\n\n"
              f"Explain it with a voice message!\n"
              f"⏱ You have {config.GAME_ROUND_TIME} seconds",
              parse_mode="Markdown",
              reply_markup=chat_keyboard(effects_on=effects_on, in_game=True))

    safe_send(snap["guesser"],
              f"🎯 *Guess the word!*\n\n"
              f"Your partner explains the word by voice\n"
              f"Write your answer as a text message\n"
              f"⏱ You have {config.GAME_ROUND_TIME} seconds",
              parse_mode="Markdown",
              reply_markup=guesser_keyboard(effects_on=effects_on))

    timer = threading.Timer(config.GAME_ROUND_TIME, game_round_timeout, args=(session, snap["round_id"]))
    timer.daemon = True
    timer.start()


def game_round_timeout(session, round_id):
    old_word = state.round_timeout(session, round_id)
    if old_word is None:
        return

    for uid in session["players"]:
        safe_send(uid, f"⏱ *Time is up!*\n\nThe word was: _{old_word}_", parse_mode="Markdown")

    start_game_round(session)


@bot.callback_query_handler(func=lambda call: call.data == "cancel_game")
def cancel_game_callback(call):
    user_id = call.from_user.id

    partner_id = state.cancel_game(user_id)
    if partner_id is None:
        safe_answer(call.id, "❌ Game session not found")
        return

    safe_answer(call.id, "❌ Game cancelled")
    safe_edit("❌ Game cancelled", call.message.chat.id, call.message.message_id,
              reply_markup=keyboard_for(user_id))
    safe_send(partner_id, "❌ Partner cancelled the game", reply_markup=keyboard_for(partner_id))


@bot.callback_query_handler(func=lambda call: call.data == "end_game")
def end_game_callback(call):
    user_id = call.from_user.id

    game = state.end_game(user_id)
    if game is None:
        safe_answer(call.id, "❌ Not in game")
        return

    finish_game_stats(game)
    player1, player2 = game["players"]
    partner_id = player2 if user_id == player1 else player1

    safe_answer(call.id, "🏁 Game ended")
    safe_edit("🏁 *Game ended*", call.message.chat.id, call.message.message_id,
              parse_mode="Markdown", reply_markup=keyboard_for(user_id))
    safe_send(partner_id, "🏁 *Game ended*", parse_mode="Markdown", reply_markup=keyboard_for(partner_id))


@bot.callback_query_handler(func=lambda call: call.data == "change_word")
def change_word_callback(call):
    user_id = call.from_user.id

    status, new_word = state.change_word(user_id)
    if status == "no_game":
        safe_answer(call.id, "❌ Not in game")
    elif status == "not_explainer":
        safe_answer(call.id, "❌ It's not your turn to explain")
    else:
        safe_answer(call.id, "🔄 Word changed")
        safe_send(user_id,
                  f"🔄 *New word:* _{new_word}_\n\n"
                  f"Explain it with a voice message!",
                  parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "toggle_effects_game")
def toggle_effects_game_callback(call):
    user_id = call.from_user.id

    result = state.toggle_game_effects(user_id)
    if result is None:
        safe_answer(call.id, "❌ Not in game")
        return

    effects_on, role = result
    safe_answer(call.id, f"🎭 Effects {'enabled' if effects_on else 'disabled'}")

    if role == "explainer":
        keyboard = chat_keyboard(effects_on=effects_on, in_game=True)
    else:
        keyboard = guesser_keyboard(effects_on=effects_on)
    safe_edit_markup(call.message.chat.id, call.message.message_id, keyboard)


# ---------------------------------------------------------------------------
# Text / media messages
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id

    status, info = state.process_guess(user_id, message.text)

    if status == "correct":
        session, partner_id = info["session"], info["partner_id"]

        safe_send(user_id,
                  f"✅ *Correct!*\n\n"
                  f"🏆 *Score:* {info['my_score']} - {info['partner_score']}\n\n"
                  f"Now you explain a new word",
                  parse_mode="Markdown")

        safe_send(partner_id,
                  f"🎯 *Partner guessed it!*\n\n"
                  f"The word was: _{info['old_word']}_\n"
                  f"🏆 *Score:* {info['partner_score']} - {info['my_score']}\n\n"
                  f"Now you guess",
                  parse_mode="Markdown")

        start_game_round(session)
        return

    if status == "wrong":
        bot.reply_to(message, "❌ Wrong, try again")
        return

    view = state.chat_view(user_id)
    if view["connected"]:
        if view["role"] == "explainer":
            bot.reply_to(message, "⚠️ *It's your turn to explain!*\n\n"
                                  "Send a voice message to explain the word.",
                         parse_mode="Markdown")
        else:
            bot.reply_to(message, "⚠️ *Voice messages only!*\n\n"
                                  "Send a voice message or use the buttons below.",
                         parse_mode="Markdown")
    elif check_subscription(user_id):
        safe_send(user_id, "🎤 *ChronoRoom - voice chat*",
                  parse_mode="Markdown", reply_markup=main_keyboard())
    else:
        send_subscription_prompt(user_id)


@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'sticker'])
def handle_other_messages(message):
    view = state.chat_view(message.from_user.id)

    if view["connected"]:
        if view["role"] == "guesser":
            bot.reply_to(message, "⚠️ *You need to guess the word now!*\n\n"
                                  "Write a text message with your answer.",
                         parse_mode="Markdown")
        else:
            bot.reply_to(message, "⚠️ *Voice messages only!*\n\n"
                                  "Use the microphone to record a voice message.",
                         parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Voice messages
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id

    route = state.voice_route(user_id)
    if route is None:
        safe_send(user_id, "❌ Find a partner first", reply_markup=main_keyboard())
        return

    if route["guessing"]:
        bot.reply_to(message, "⚠️ *It's your turn to guess!*\n\n"
                              "Write a text message with your answer.",
                     parse_mode="Markdown")
        return

    voice = message.voice
    if voice.duration > config.MAX_VOICE_DURATION:
        bot.reply_to(message, f"⚠️ Voice message is too long (max {config.MAX_VOICE_DURATION} seconds)")
        return

    # "duration" is reported by the sender's client and can be faked, the size cannot.
    if voice.file_size and voice.file_size > config.MAX_VOICE_FILE_SIZE:
        bot.reply_to(message, "⚠️ Voice message is too large")
        return

    partner_id = route["partner_id"]
    effect = route["effect"]
    use_effect = bool(config.ENABLE_EFFECTS and route["effects_on"] and effect)

    original_path = f"{config.TEMP_FOLDER}/{user_id}_{message.message_id}_original.ogg"
    processed_path = f"{config.TEMP_FOLDER}/{user_id}_{message.message_id}_processed.ogg"

    sent_original = True
    processing_msg = None
    try:
        file_info = bot.get_file(voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with open(original_path, 'wb') as f:
            f.write(downloaded_file)

        processing_msg = safe_send(user_id, "🔄 *Processing voice...*", parse_mode="Markdown")

        path_to_send = original_path
        if use_effect and apply_voice_effect(original_path, processed_path, effect,
                                             config.MAX_VOICE_DURATION + 1):
            path_to_send = processed_path
            sent_original = False

        with open(path_to_send, 'rb') as f:
            bot.send_voice(partner_id, f)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        if getattr(e, "error_code", None) == 403:
            drop_unreachable_partner(user_id)      # the partner blocked the bot
        else:
            safe_send(user_id, "❌ Failed to send the voice message. Try again.")
    else:
        # The voice is already delivered; a failing status edit must not report a failure.
        if processing_msg is not None:
            status = "✅ *Voice message sent*"
            if sent_original and use_effect:
                status += " (original)"
            safe_edit(status, user_id, processing_msg.message_id, parse_mode="Markdown")
    finally:
        if not config.SAVE_VOICE_FILES:
            remove_file(original_path)
        remove_file(processed_path)


# ---------------------------------------------------------------------------
# Effects and voice selection
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data == "toggle_effects_chat")
def toggle_effects_chat_callback(call):
    user_id = call.from_user.id

    effects_on = state.toggle_effects(user_id)
    if effects_on is None:
        safe_answer(call.id, "❌ Toggle error")
        return

    safe_answer(call.id, f"🎭 Effects {'enabled' if effects_on else 'disabled'}")
    safe_edit_markup(call.message.chat.id, call.message.message_id, keyboard_for(user_id))


EFFECT_CHOICES = {
    "effect_robot": "🤖 Robot",
    "effect_demon": "👹 Demon",
    "effect_chipmunk": "🐿️ Chipmunk",
}


@bot.callback_query_handler(func=lambda call: call.data.startswith("effect_"))
def effect_selection_callback(call):
    user_id = call.from_user.id

    if call.data == "effect_random":
        state.set_effect(user_id, None)
        label = "🎲 Random (changes automatically)"
    elif call.data in EFFECT_CHOICES and state.set_effect(user_id, EFFECT_CHOICES[call.data]):
        label = EFFECT_CHOICES[call.data]
    else:
        safe_answer(call.id, "❌ Unknown effect")
        return

    safe_answer(call.id, f"✅ Set: {label}")
    safe_edit(f"🎭 *Effect set:* {label}", call.message.chat.id, call.message.message_id,
              parse_mode="Markdown", reply_markup=voice_selection_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "voice_selection")
def voice_selection_menu_callback(call):
    safe_answer(call.id)
    edit_or_send(call.message.chat.id, call.message.message_id,
                 "🎭 *Voice effect selection*\n\n"
                 "Choose an effect for your voice:",
                 parse_mode="Markdown", reply_markup=voice_selection_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "show_menu")
def show_menu_callback(call):
    safe_answer(call.id)
    edit_or_send(call.message.chat.id, call.message.message_id, "⚙️ *ChronoRoom Menu*",
                 parse_mode="Markdown", reply_markup=menu_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "back_to_chat")
def back_to_chat_callback(call):
    user_id = call.from_user.id
    view = state.chat_view(user_id)

    if view["connected"]:
        if view["in_game"] and view["role"] == "explainer":
            text = "🎮 *Game continues*\n\nYou explain the word by voice"
        elif view["in_game"]:
            text = "🎮 *Game continues*\n\nYou guess the word by text"
        else:
            text = "💬 *Dialogue active*\n\nSend voice messages"

        safe_answer(call.id, "💬 Returning to chat")
        edit_or_send(call.message.chat.id, call.message.message_id, text,
                     parse_mode="Markdown", reply_markup=keyboard_for(user_id))
    else:
        safe_answer(call.id, "🏠 Returning to menu")
        edit_or_send(call.message.chat.id, call.message.message_id, "⏳ *ChronoRoom*",
                     parse_mode="Markdown", reply_markup=main_keyboard())


# ---------------------------------------------------------------------------
# Username exchange
# ---------------------------------------------------------------------------

def _requester_id(call):
    """Parses 'show_username:<id>' / 'hide_username:<id>'; None for old-style or broken data."""
    try:
        return int(call.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


@bot.callback_query_handler(func=lambda call: call.data == "request_username")
def request_username_callback(call):
    user_id = call.from_user.id

    partner_id = state.partner_of(user_id)
    if partner_id is None:
        safe_answer(call.id, "❌ No active dialogue")
        return

    sent = safe_send(partner_id,
                     "🎭 *Username request*\nShow your username to the partner?",
                     parse_mode="Markdown",
                     reply_markup=request_username_keyboard(user_id))
    safe_answer(call.id, "📨 Request sent" if sent else "❌ Could not deliver the request")


def _answer_username_request(call, show):
    user_id = call.from_user.id
    partner_id = state.partner_of(user_id)

    if partner_id is None:
        safe_answer(call.id, "❌ Dialogue ended")
        return
    if _requester_id(call) != partner_id:
        # The request came from an earlier partner: never reveal anything to the current one.
        safe_answer(call.id, "❌ This request is outdated")
        safe_edit_markup(call.message.chat.id, call.message.message_id, None)
        return

    if show:
        username = call.from_user.username
        if not username:
            safe_answer(call.id, "❌ You don't have a username")
            return
        safe_send(partner_id, f"👤 @{username}")
        safe_answer(call.id, "✅ Sent")
    else:
        safe_send(partner_id, "❌ Partner kept anonymity")
        safe_answer(call.id, "✅ Anonymity preserved")

    safe_edit_markup(call.message.chat.id, call.message.message_id, None)   # one answer per request


@bot.callback_query_handler(func=lambda call: call.data.startswith("show_username"))
def show_username_callback(call):
    _answer_username_request(call, show=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("hide_username"))
def hide_username_callback(call):
    _answer_username_request(call, show=False)


# ---------------------------------------------------------------------------
# Background work and startup
# ---------------------------------------------------------------------------

def auto_change_effects():
    while True:
        time.sleep(config.EFFECT_CHANGE_TIME)
        try:
            state.rotate_effects()
        except Exception:
            logger.exception("Effect rotation failed")


def clean_temp_folder():
    """Removes files left behind by a crash (kept originals are not touched if saving is on)."""
    for filename in os.listdir(config.TEMP_FOLDER):
        if config.SAVE_VOICE_FILES and not filename.endswith("_processed.ogg"):
            continue
        file_path = os.path.join(config.TEMP_FOLDER, filename)
        if os.path.isfile(file_path):
            remove_file(file_path)


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set")
    if config.CHECK_SUBSCRIPTION and not config.CHANNEL_LINK:
        raise SystemExit("CHANNEL_LINK must be set when the subscription check is enabled "
                         "(or set CHECK_SUBSCRIPTION=0)")

    database.init_db()

    logger.info("ChronoRoom started")
    logger.info(f"Effects: {len(config.EFFECTS)}")
    logger.info(f"Words for game: {len(config.GAME_WORDS)}")
    logger.info(f"Subscription check: {'on' if config.CHECK_SUBSCRIPTION else 'off'}")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        logger.info("FFmpeg installed")
    except FileNotFoundError:
        logger.warning("FFmpeg not found: voice messages will be sent without effects")

    os.makedirs(config.TEMP_FOLDER, exist_ok=True)
    clean_temp_folder()

    threading.Thread(target=auto_change_effects, daemon=True).start()

    logger.info("Bot is running...")
    bot.infinity_polling(interval=0)


if __name__ == "__main__":
    main()
