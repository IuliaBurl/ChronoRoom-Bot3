"""Shared in-memory state: search queue, dialogues, voice effects and game sessions.

pyTelegramBotAPI runs handlers in worker threads and the bot also has timer threads, so
every piece of shared state lives in ONE object guarded by ONE re-entrant lock.
Handlers never touch the dictionaries directly; they call methods of ChatState, and each
method is a single atomic transition (check + change under the same lock).

Rules that keep it deadlock-free and fast:
- methods never do I/O (Telegram, database, FFmpeg) while holding the lock;
- they return everything the caller needs to notify people *afterwards*.
"""
import itertools
import random
import threading
from typing import Dict, List, Optional


class ChatState:
    def __init__(self, effects: List[str], words: List[str]):
        if not effects or not words:
            raise ValueError("effects and words must not be empty")
        self._lock = threading.RLock()
        self._effects = list(effects)
        self._words = list(words)
        self._search_counter = itertools.count(1)

        self._waiting: Dict[int, dict] = {}       # user_id -> {chat_id, message_id, token}
        self._connections: Dict[int, int] = {}    # user_id -> partner_id (always symmetric)
        self._user_effects: Dict[int, str] = {}   # current effect of a connected user
        self._effects_enabled: Dict[int, bool] = {}
        self._manual_effects: Dict[int, str] = {}  # effects the user picked on purpose
        self._sessions: Dict[int, dict] = {}      # user_id -> game session (shared by both players)

    def clear(self):
        with self._lock:
            for d in (self._waiting, self._connections, self._user_effects,
                      self._effects_enabled, self._manual_effects, self._sessions):
                d.clear()

    # ------------------------------------------------------------------ search / dialogues

    def search_status(self, user_id: int) -> str:
        """'connected', 'searching' or 'idle'"""
        with self._lock:
            if user_id in self._connections:
                return "connected"
            if user_id in self._waiting:
                return "searching"
            return "idle"

    def find_partner_or_wait(self, user_id: int, chat_id: int, message_id: int):
        """Atomically pairs the user with a waiting person or puts the user into the queue.

        Returns (status, payload):
          ('busy', None)                       the user is already in a dialogue
          ('already_searching', None)          the user is already in the queue
          ('matched', (partner_id, data))      connected; data is the partner's queue entry
          ('waiting', token)                   queued; token identifies this particular search
        """
        with self._lock:
            if user_id in self._connections:
                return "busy", None
            if user_id in self._waiting:
                return "already_searching", None

            for candidate_id in list(self._waiting):
                if candidate_id not in self._connections:
                    data = self._waiting.pop(candidate_id)
                    self._connect(user_id, candidate_id)
                    return "matched", (candidate_id, data)
                self._waiting.pop(candidate_id, None)   # stale entry

            token = next(self._search_counter)
            self._waiting[user_id] = {"chat_id": chat_id, "message_id": message_id, "token": token}
            return "waiting", token

    def _connect(self, a: int, b: int):
        self._connections[a] = b
        self._connections[b] = a
        for uid in (a, b):
            self._user_effects[uid] = self._manual_effects.get(uid) or random.choice(self._effects)
            self._effects_enabled[uid] = True

    def cancel_search(self, user_id: int) -> bool:
        with self._lock:
            return self._waiting.pop(user_id, None) is not None

    def expire_search(self, user_id: int, token: int) -> Optional[dict]:
        """Removes the queue entry if it is still the same search. Returns it, or None if outdated."""
        with self._lock:
            data = self._waiting.get(user_id)
            if not data or data["token"] != token:
                return None
            del self._waiting[user_id]
            return data

    def partner_of(self, user_id: int) -> Optional[int]:
        with self._lock:
            return self._connections.get(user_id)

    def disconnect(self, user_id: int):
        """Atomically ends the user's dialogue (and the game inside it).

        Returns (partner_id, session): partner_id is None if there was no dialogue;
        session is the game that was running, if any.
        """
        with self._lock:
            partner_id = self._connections.pop(user_id, None)
            if partner_id is not None and self._connections.get(partner_id) == user_id:
                del self._connections[partner_id]

            session = self._sessions.pop(user_id, None)
            participants = [user_id]
            if partner_id is not None:
                participants.append(partner_id)
                session = session or self._sessions.pop(partner_id, None)
                self._sessions.pop(partner_id, None)

            for uid in participants:
                self._user_effects.pop(uid, None)
                self._effects_enabled.pop(uid, None)
            return partner_id, self._snapshot(session)

    # ------------------------------------------------------------------ voice effects

    def voice_route(self, user_id: int) -> Optional[dict]:
        """Everything handle_voice needs, read atomically. None if the user has no dialogue."""
        with self._lock:
            partner_id = self._connections.get(user_id)
            if partner_id is None:
                return None
            session = self._sessions.get(user_id)
            return {
                "partner_id": partner_id,
                "effect": self._user_effects.get(user_id),
                "effects_on": self._effects_enabled.get(user_id, True),
                "guessing": bool(session and session["started"] and session["guesser"] == user_id),
            }

    def chat_view(self, user_id: int) -> dict:
        """What the keyboard / text for this user should look like right now."""
        with self._lock:
            session = self._sessions.get(user_id)
            started = bool(session and session["started"])
            role = None
            if started:
                role = "explainer" if session["explainer"] == user_id else "guesser"
            return {
                "connected": user_id in self._connections,
                "effects_on": self._effects_enabled.get(user_id, True),
                "in_game": started,
                "role": role,
            }

    def toggle_effects(self, user_id: int) -> Optional[bool]:
        """Switches the user's own effects on/off. None if the user is not in a dialogue."""
        with self._lock:
            if user_id not in self._connections:
                return None
            new_value = not self._effects_enabled.get(user_id, True)
            self._effects_enabled[user_id] = new_value
            return new_value

    def set_effect(self, user_id: int, effect: Optional[str]) -> bool:
        """effect=None means 'random': automatic rotation. A concrete effect is pinned
        for this user and survives partner changes and the automatic rotation."""
        with self._lock:
            if effect is None:
                self._manual_effects.pop(user_id, None)
                chosen = random.choice(self._effects)
            elif effect in self._effects:
                self._manual_effects[user_id] = effect
                chosen = effect
            else:
                return False
            if user_id in self._connections:
                self._user_effects[user_id] = chosen
            return True

    def rotate_effects(self):
        """Periodic automatic change; users who picked an effect themselves are left alone."""
        with self._lock:
            for uid in self._connections:
                if uid not in self._manual_effects and self._effects_enabled.get(uid, True):
                    self._user_effects[uid] = random.choice(self._effects)

    # ------------------------------------------------------------------ game

    def _pick_word(self, exclude: Optional[str] = None) -> str:
        choices = [w for w in self._words if w != exclude] or self._words
        return random.choice(choices)

    @staticmethod
    def _snapshot(session: Optional[dict]) -> Optional[dict]:
        if session is None:
            return None
        return {"players": session["players"], "started": session["started"],
                "score": dict(session["score"])}

    def _alive(self, session: dict) -> bool:
        return self._sessions.get(session["players"][0]) is session

    def _rotate_round(self, session: dict, new_explainer: int):
        player1, player2 = session["players"]
        session["explainer"] = new_explainer
        session["guesser"] = player2 if new_explainer == player1 else player1
        session["word"] = self._pick_word(exclude=session["word"])
        session["round_id"] += 1

    def create_game(self, user_id: int):
        """Returns ('ok', session) | ('no_dialogue', None) | ('exists', None)."""
        with self._lock:
            partner_id = self._connections.get(user_id)
            if partner_id is None:
                return "no_dialogue", None
            if user_id in self._sessions:
                return "exists", None

            explainer = random.choice([user_id, partner_id])
            session = {
                "players": (user_id, partner_id),
                "word": self._pick_word(),
                "explainer": explainer,
                "guesser": user_id if explainer == partner_id else partner_id,
                # start from what the players currently have, so the button shows the truth
                "effects_on": (self._effects_enabled.get(user_id, True)
                               and self._effects_enabled.get(partner_id, True)),
                "score": {user_id: 0, partner_id: 0},
                "confirmed": set(),
                "prompts": {},
                "started": False,
                "round_id": 1,
            }
            self._sessions[user_id] = session
            self._sessions[partner_id] = session
            return "ok", session

    def confirm_game(self, user_id: int, chat_id: int, message_id: int):
        """Returns (status, session, prompts): status is 'no_session', 'already_started',
        'waiting' or 'started'; prompts (list of (chat_id, message_id)) only for 'started'."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return "no_session", None, None
            if session["started"]:
                return "already_started", session, None

            session["confirmed"].add(user_id)
            session["prompts"][user_id] = (chat_id, message_id)
            if len(session["confirmed"]) == 2:
                session["started"] = True
                return "started", session, list(session["prompts"].values())
            return "waiting", session, None

    def cancel_game(self, user_id: int) -> Optional[int]:
        """Cancels a game that has not started yet. Returns the partner's id, or None."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session or session["started"]:
                return None
            for uid in session["players"]:
                self._sessions.pop(uid, None)
            player1, player2 = session["players"]
            return player2 if user_id == player1 else player1

    def end_game(self, user_id: int) -> Optional[dict]:
        """Ends the game. Returns a snapshot {players, started, score} or None."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return None
            for uid in session["players"]:
                self._sessions.pop(uid, None)
            return self._snapshot(session)

    def round_snapshot(self, session: dict) -> Optional[dict]:
        """Consistent copy of the current round, or None if the game is over."""
        with self._lock:
            if not self._alive(session):
                return None
            return {
                "word": session["word"],
                "explainer": session["explainer"],
                "guesser": session["guesser"],
                "effects_on": session["effects_on"],
                "round_id": session["round_id"],
            }

    def process_guess(self, user_id: int, text: str):
        """Checks a text message as a guess.

        Returns ('not_guessing', None) | ('wrong', None) | ('correct', info) where info has
        session, old_word, partner_id, my_score, partner_score. Scoring and the switch to the
        next round happen in the same atomic step, so a guess cannot be counted twice.
        """
        with self._lock:
            session = self._sessions.get(user_id)
            if not session or not session["started"] or session["guesser"] != user_id:
                return "not_guessing", None
            if text.lower().strip() != session["word"].lower().strip():
                return "wrong", None

            player1, player2 = session["players"]
            partner_id = player2 if user_id == player1 else player1
            old_word = session["word"]
            session["score"][user_id] += 1
            self._rotate_round(session, new_explainer=user_id)
            return "correct", {
                "session": session,
                "old_word": old_word,
                "partner_id": partner_id,
                "my_score": session["score"][user_id],
                "partner_score": session["score"][partner_id],
            }

    def round_timeout(self, session: dict, round_id: int) -> Optional[str]:
        """Switches roles when the time is up. Returns the old word, or None if the timer is outdated
        (the round already ended, or the game is over)."""
        with self._lock:
            if not self._alive(session) or session["round_id"] != round_id:
                return None
            old_word = session["word"]
            self._rotate_round(session, new_explainer=session["guesser"])
            return old_word

    def change_word(self, user_id: int):
        """Returns ('ok', new_word) | ('no_game', None) | ('not_explainer', None)."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session or not session["started"]:
                return "no_game", None
            if session["explainer"] != user_id:
                return "not_explainer", None
            session["word"] = self._pick_word(exclude=session["word"])
            return "ok", session["word"]

    def toggle_game_effects(self, user_id: int):
        """Shared effects switch of a game. Returns (effects_on, role) or None if not in a game."""
        with self._lock:
            session = self._sessions.get(user_id)
            if not session:
                return None
            session["effects_on"] = not session["effects_on"]
            for uid in session["players"]:
                self._effects_enabled[uid] = session["effects_on"]
            role = "explainer" if session["explainer"] == user_id else "guesser"
            return session["effects_on"], role
