"""SQLite persistence: per-user counters and finished game sessions.

Statistics are best-effort: a database problem is logged and never breaks a dialogue.
"""
import logging
import sqlite3
from contextlib import closing

import config

logger = logging.getLogger(__name__)

STAT_FIELDS = {'searches', 'connections', 'game_wins'}


def _connect():
    return closing(sqlite3.connect(config.DB_NAME))


def init_db():
    with _connect() as conn:
        conn.execute(f'''CREATE TABLE IF NOT EXISTS {config.DB_TABLE_USERS}
                         (user_id INTEGER PRIMARY KEY,
                          username TEXT,
                          searches INTEGER DEFAULT 0,
                          connections INTEGER DEFAULT 0,
                          game_wins INTEGER DEFAULT 0,
                          joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute(f'''CREATE TABLE IF NOT EXISTS {config.DB_TABLE_GAMES}
                         (game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                          user1_id INTEGER,
                          user2_id INTEGER,
                          words_guessed INTEGER DEFAULT 0,
                          game_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()


def get_or_create_user(user_id, username=None):
    """Atomic upsert: two simultaneous first messages cannot collide on the primary key.

    A missing username (the person removed it) is stored as NULL too,
    so the stored value never goes stale.
    """
    try:
        with _connect() as conn:
            conn.execute(
                f'''INSERT INTO {config.DB_TABLE_USERS} (user_id, username) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET username = excluded.username''',
                (user_id, username))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Could not save user %s: %s", user_id, e)


def update_stats(user_id, field):
    if not config.ENABLE_STATISTICS or field not in STAT_FIELDS:
        return
    try:
        with _connect() as conn:
            conn.execute(f"UPDATE {config.DB_TABLE_USERS} SET {field} = {field} + 1 WHERE user_id = ?",
                         (user_id,))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Could not update %s for %s: %s", field, user_id, e)


def record_game_session(user1_id, user2_id, score1, score2):
    """Stores a finished game and credits a win to the player with the higher score (ties: nobody)."""
    if not config.ENABLE_STATISTICS:
        return
    try:
        with _connect() as conn:
            conn.execute(
                f"INSERT INTO {config.DB_TABLE_GAMES} (user1_id, user2_id, words_guessed) VALUES (?, ?, ?)",
                (user1_id, user2_id, score1 + score2))
            winner = user1_id if score1 > score2 else user2_id if score2 > score1 else None
            if winner is not None:
                conn.execute(
                    f"UPDATE {config.DB_TABLE_USERS} SET game_wins = game_wins + 1 WHERE user_id = ?",
                    (winner,))
            conn.commit()
    except sqlite3.Error as e:
        logger.error("Could not record game session: %s", e)
