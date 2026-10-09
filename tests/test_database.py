import os
import tempfile
import unittest
from unittest import mock

import config
import database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "test.db")
        patcher = mock.patch.object(config, "DB_NAME", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        database.init_db()

    def row(self, user_id):
        with database._connect() as conn:
            return conn.execute(
                f"SELECT username, searches, connections, game_wins FROM {config.DB_TABLE_USERS} WHERE user_id = ?",
                (user_id,)).fetchone()

    def test_schema_has_no_unused_total_time(self):
        with database._connect() as conn:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({config.DB_TABLE_USERS})")]
        self.assertNotIn("total_time", columns)

    def test_upsert_creates_then_updates(self):
        database.get_or_create_user(1, "old")
        database.get_or_create_user(1, "new")
        self.assertEqual(self.row(1)[0], "new")
        database.get_or_create_user(1, None)       # the person removed the username
        self.assertIsNone(self.row(1)[0])

    def test_upsert_keeps_counters(self):
        database.get_or_create_user(1, "a")
        database.update_stats(1, "searches")
        database.get_or_create_user(1, "b")
        self.assertEqual(self.row(1), ("b", 1, 0, 0))

    def test_update_stats_only_allows_known_fields(self):
        database.get_or_create_user(1, "a")
        database.update_stats(1, "searches")
        database.update_stats(1, "connections")
        database.update_stats(1, "searches = 99, game_wins")      # injection attempt
        database.update_stats(1, "total_time")                    # removed field
        self.assertEqual(self.row(1), ("a", 1, 1, 0))

    def test_game_winner_is_the_higher_score_only(self):
        for uid in (1, 2, 3, 4):
            database.get_or_create_user(uid, f"u{uid}")
        database.record_game_session(1, 2, 3, 1)      # 1 wins
        database.record_game_session(3, 4, 2, 2)      # tie: nobody
        database.record_game_session(1, 2, 0, 1)      # 2 wins
        self.assertEqual([self.row(u)[3] for u in (1, 2, 3, 4)], [1, 1, 0, 0])
        with database._connect() as conn:
            words = [r[0] for r in conn.execute(f"SELECT words_guessed FROM {config.DB_TABLE_GAMES} ORDER BY game_id")]
        self.assertEqual(words, [4, 4, 1])

    def test_statistics_can_be_disabled(self):
        database.get_or_create_user(1, "a")
        with mock.patch.object(config, "ENABLE_STATISTICS", False):
            database.update_stats(1, "searches")
            database.record_game_session(1, 2, 5, 0)
        self.assertEqual(self.row(1), ("a", 0, 0, 0))
        with database._connect() as conn:
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {config.DB_TABLE_GAMES}").fetchone()[0], 0)

    def test_database_errors_are_logged_not_raised(self):
        with mock.patch.object(config, "DB_NAME", os.path.join(self._dir.name, "missing", "x.db")):
            with self.assertLogs("database", level="ERROR"):
                database.update_stats(1, "searches")
                database.get_or_create_user(1, "a")
                database.record_game_session(1, 2, 1, 0)


if __name__ == "__main__":
    unittest.main()
