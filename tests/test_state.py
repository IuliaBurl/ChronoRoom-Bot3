import random
import sys
import threading
import unittest

from state import ChatState

EFFECTS = ["🤖 Robot", "👹 Demon", "🐿️ Chipmunk"]
WORDS = ["cat", "dog", "sun", "rain", "tree"]


def new_state(words=WORDS):
    return ChatState(EFFECTS, words)


def pair(st, a=1, b=2):
    """Connects a and b: a waits, b arrives."""
    assert st.find_partner_or_wait(a, a, 10)[0] == "waiting"
    status, _ = st.find_partner_or_wait(b, b, 20)
    assert status == "matched"


def started_game(st, a=1, b=2):
    pair(st, a, b)
    _, session = st.create_game(a)
    st.confirm_game(a, a, 1)
    assert st.confirm_game(b, b, 2)[0] == "started"
    return session


def assert_invariants(test, st):
    c, w, s = st._connections, st._waiting, st._sessions
    for uid, partner in c.items():
        test.assertEqual(c.get(partner), uid, "dialogues must be symmetric")
        test.assertNotEqual(uid, partner)
    test.assertFalse(set(c) & set(w), "nobody may wait and talk at the same time")
    for uid, session in s.items():
        test.assertIn(uid, c, "a game exists only inside a dialogue")
        test.assertIs(s.get(c[uid]), session, "both players share the same session")
    for uid in list(st._user_effects) + list(st._effects_enabled):
        test.assertIn(uid, c, "effect data only for connected users")


class MatchmakingTests(unittest.TestCase):
    def test_two_users_are_matched(self):
        st = new_state()
        self.assertEqual(st.find_partner_or_wait(1, 100, 10)[0], "waiting")
        status, (partner_id, data) = st.find_partner_or_wait(2, 200, 20)
        self.assertEqual((status, partner_id), ("matched", 1))
        self.assertEqual(data["chat_id"], 100)
        self.assertEqual(st.partner_of(1), 2)
        self.assertEqual(st.partner_of(2), 1)
        self.assertIn(st._user_effects[1], EFFECTS)
        assert_invariants(self, st)

    def test_busy_and_already_searching(self):
        st = new_state()
        st.find_partner_or_wait(1, 1, 1)
        self.assertEqual(st.search_status(1), "searching")
        self.assertEqual(st.find_partner_or_wait(1, 1, 1)[0], "already_searching")
        st.find_partner_or_wait(2, 2, 2)
        self.assertEqual(st.search_status(1), "connected")
        self.assertEqual(st.find_partner_or_wait(1, 1, 1)[0], "busy")
        self.assertEqual(st.search_status(99), "idle")

    def test_third_user_waits(self):
        st = new_state()
        pair(st)
        self.assertEqual(st.find_partner_or_wait(3, 3, 3)[0], "waiting")

    def test_stale_queue_entry_is_skipped_and_removed(self):
        st = new_state()
        pair(st, 1, 2)
        st._waiting[1] = {"chat_id": 1, "message_id": 1, "token": 99}   # stale: 1 is already talking
        status, token = st.find_partner_or_wait(3, 3, 3)
        self.assertEqual(status, "waiting")
        self.assertNotIn(1, st._waiting)

    def test_expire_search_checks_the_token(self):
        st = new_state()
        _, token1 = st.find_partner_or_wait(1, 1, 1)
        self.assertTrue(st.cancel_search(1))
        _, token2 = st.find_partner_or_wait(1, 1, 1)
        self.assertNotEqual(token1, token2)
        self.assertIsNone(st.expire_search(1, token1), "an old timer must not cancel a newer search")
        self.assertIsNotNone(st.expire_search(1, token2))
        self.assertEqual(st.search_status(1), "idle")

    def test_matched_user_is_removed_from_queue_and_timer_is_harmless(self):
        st = new_state()
        _, token = st.find_partner_or_wait(1, 1, 1)
        st.find_partner_or_wait(2, 2, 2)
        self.assertIsNone(st.expire_search(1, token))
        self.assertEqual(st.partner_of(1), 2)


class DisconnectTests(unittest.TestCase):
    def test_disconnect_cleans_everything_and_is_idempotent(self):
        st = new_state()
        session = started_game(st)
        partner, game = st.disconnect(1)
        self.assertEqual(partner, 2)
        self.assertTrue(game["started"])
        self.assertEqual((st._connections, st._sessions, st._user_effects, st._effects_enabled),
                         ({}, {}, {}, {}))
        self.assertEqual(st.disconnect(1), (None, None))
        self.assertEqual(st.disconnect(2), (None, None))
        self.assertIsNone(st.round_snapshot(session), "a timer of a dead game must find nothing")

    def test_no_dialogue(self):
        self.assertEqual(new_state().disconnect(5), (None, None))


class EffectsTests(unittest.TestCase):
    def test_toggle_requires_dialogue(self):
        st = new_state()
        self.assertIsNone(st.toggle_effects(1))
        pair(st)
        self.assertFalse(st.toggle_effects(1))
        self.assertTrue(st.toggle_effects(1))

    def test_chosen_effect_survives_connect_rotation_and_new_partner(self):
        st = new_state()
        self.assertTrue(st.set_effect(1, "👹 Demon"))        # chosen BEFORE any partner exists
        pair(st, 1, 2)
        self.assertEqual(st._user_effects[1], "👹 Demon")
        for _ in range(200):
            st.rotate_effects()
        self.assertEqual(st._user_effects[1], "👹 Demon", "automatic rotation must not undo the choice")
        st.disconnect(1)
        pair(st, 1, 3)
        self.assertEqual(st._user_effects[1], "👹 Demon", "choice must survive a partner change")

    def test_chosen_effect_applies_immediately_in_a_dialogue(self):
        st = new_state()
        pair(st)
        st.set_effect(1, "🐿️ Chipmunk")
        self.assertEqual(st.voice_route(1)["effect"], "🐿️ Chipmunk")

    def test_random_unpins_and_rotation_resumes(self):
        st = new_state()
        pair(st)
        st.set_effect(1, "👹 Demon")
        st.set_effect(1, None)
        seen = set()
        for _ in range(300):
            st.rotate_effects()
            seen.add(st._user_effects[1])
        self.assertGreater(len(seen), 1)

    def test_unknown_effect_rejected(self):
        self.assertFalse(new_state().set_effect(1, "💥 Boom"))

    def test_rotation_skips_users_with_effects_disabled(self):
        st = new_state()
        pair(st)
        st.toggle_effects(1)
        before = st._user_effects[1]
        st._user_effects[1] = "sentinel"
        for _ in range(50):
            st.rotate_effects()
        self.assertEqual(st._user_effects[1], "sentinel")
        self.assertIn(st._user_effects[2], EFFECTS)


class GameTests(unittest.TestCase):
    def test_create_requires_dialogue_and_is_not_duplicated(self):
        st = new_state()
        self.assertEqual(st.create_game(1), ("no_dialogue", None))
        pair(st)
        status, session = st.create_game(1)
        self.assertEqual(status, "ok")
        self.assertEqual(st.create_game(2), ("exists", None))
        self.assertIs(st._sessions[1], st._sessions[2])
        self.assertNotEqual(session["explainer"], session["guesser"])

    def test_game_effects_flag_reflects_current_settings(self):
        st = new_state()
        pair(st)
        st.toggle_effects(1)                      # user 1 turned effects off before the game
        _, session = st.create_game(1)
        self.assertFalse(session["effects_on"])

    def test_both_must_confirm(self):
        st = new_state()
        pair(st)
        st.create_game(1)
        self.assertEqual(st.confirm_game(1, 1, 5)[0], "waiting")
        self.assertEqual(st.confirm_game(1, 1, 5)[0], "waiting", "double press by the same user")
        status, session, prompts = st.confirm_game(2, 2, 6)
        self.assertEqual(status, "started")
        self.assertCountEqual(prompts, [(1, 5), (2, 6)])
        self.assertEqual(st.confirm_game(2, 2, 6)[0], "already_started")
        self.assertEqual(st.confirm_game(9, 9, 9)[0], "no_session")

    def test_correct_guess_scores_once_and_swaps_roles(self):
        st = new_state()
        session = started_game(st)
        guesser, explainer = session["guesser"], session["explainer"]
        word, round_id = session["word"], session["round_id"]

        status, info = st.process_guess(guesser, f"  {word.upper()} ")
        self.assertEqual(status, "correct")
        self.assertEqual((info["old_word"], info["partner_id"]), (word, explainer))
        self.assertEqual((info["my_score"], info["partner_score"]), (1, 0))
        self.assertEqual((session["explainer"], session["guesser"]), (guesser, explainer))
        self.assertEqual(session["round_id"], round_id + 1)
        self.assertNotEqual(session["word"], word)

        # the very same message arriving again (double send / second thread) must not score
        self.assertEqual(st.process_guess(guesser, word)[0], "not_guessing")
        self.assertEqual(session["score"][guesser], 1)

    def test_wrong_and_non_guesser(self):
        st = new_state()
        session = started_game(st)
        self.assertEqual(st.process_guess(session["guesser"], "zzz")[0], "wrong")
        self.assertEqual(st.process_guess(session["explainer"], session["word"])[0], "not_guessing")
        self.assertEqual(st.process_guess(77, "x")[0], "not_guessing")

    def test_guess_before_start_is_ignored(self):
        st = new_state()
        pair(st)
        _, session = st.create_game(1)
        self.assertEqual(st.process_guess(session["guesser"], session["word"])[0], "not_guessing")

    def test_timeout_switches_roles_and_stale_timers_are_ignored(self):
        st = new_state()
        session = started_game(st)
        old_guesser, round_id, word = session["guesser"], session["round_id"], session["word"]

        self.assertEqual(st.round_timeout(session, round_id), word)
        self.assertEqual(session["explainer"], old_guesser)
        self.assertIsNone(st.round_timeout(session, round_id), "the same timer cannot fire twice")

        st.process_guess(session["guesser"], session["word"])           # round moves on
        self.assertIsNone(st.round_timeout(session, round_id + 1), "a timer of an earlier round is stale")

    def test_timeout_of_finished_game_is_ignored(self):
        st = new_state()
        session = started_game(st)
        round_id = session["round_id"]
        st.end_game(1)
        self.assertIsNone(st.round_timeout(session, round_id))
        self.assertIsNone(st.round_snapshot(session))

    def test_cancel_only_before_start(self):
        st = new_state()
        pair(st)
        st.create_game(1)
        self.assertEqual(st.cancel_game(1), 2)
        self.assertEqual(st._sessions, {})
        self.assertIsNone(st.cancel_game(1))
        started_game(st, 3, 4)
        self.assertIsNone(st.cancel_game(3))

    def test_end_game_returns_snapshot(self):
        st = new_state()
        session = started_game(st)
        st.process_guess(session["guesser"], session["word"])
        game = st.end_game(2)
        self.assertTrue(game["started"])
        self.assertEqual(sum(game["score"].values()), 1)
        self.assertIsNone(st.end_game(1))
        self.assertEqual(st.partner_of(1), 2, "ending a game keeps the dialogue")

    def test_change_word(self):
        st = new_state(words=["a", "b"])
        session = started_game(st)
        self.assertEqual(st.change_word(session["guesser"]), ("not_explainer", None))
        for _ in range(20):
            old = session["word"]
            status, new = st.change_word(session["explainer"])
            self.assertEqual(status, "ok")
            self.assertNotEqual(new, old, "a new word must differ from the current one")
        pair(st, 5, 6)
        st.create_game(5)
        self.assertEqual(st.change_word(5), ("no_game", None))

    def test_toggle_game_effects_is_shared(self):
        st = new_state()
        session = started_game(st)
        effects_on, role = st.toggle_game_effects(session["explainer"])
        self.assertEqual((effects_on, role), (False, "explainer"))
        self.assertFalse(st.chat_view(session["guesser"])["effects_on"])
        self.assertIsNone(st.toggle_game_effects(42))

    def test_chat_view_and_voice_route(self):
        st = new_state()
        session = started_game(st)
        g, e = session["guesser"], session["explainer"]
        self.assertEqual(st.chat_view(g)["role"], "guesser")
        self.assertEqual(st.chat_view(e)["role"], "explainer")
        self.assertTrue(st.voice_route(g)["guessing"])
        self.assertFalse(st.voice_route(e)["guessing"])
        self.assertIsNone(st.voice_route(99))
        self.assertFalse(st.chat_view(99)["connected"])


class ConcurrencyTests(unittest.TestCase):
    """Hammers the state from many threads; whatever the interleaving, invariants must hold."""

    def setUp(self):
        # switch threads very often so that a missing lock would actually show up
        self._switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)

    def tearDown(self):
        sys.setswitchinterval(self._switch_interval)

    def test_random_operations_keep_invariants(self):
        st = new_state()
        users = list(range(1, 61))
        errors = []
        stop_at = 4000

        def worker(seed):
            rnd = random.Random(seed)
            try:
                for _ in range(stop_at):
                    u = rnd.choice(users)
                    op = rnd.randrange(11)
                    if op <= 2:
                        st.find_partner_or_wait(u, u, 1)
                    elif op == 3:
                        st.disconnect(u)
                    elif op == 4:
                        st.cancel_search(u)
                    elif op == 5:
                        st.create_game(u)
                    elif op == 6:
                        st.confirm_game(u, u, 1)
                    elif op == 7:
                        view = st.chat_view(u)
                        session = st._sessions.get(u)
                        if session:
                            st.process_guess(u, session["word"])
                    elif op == 8:
                        session = st._sessions.get(u)
                        if session:
                            st.round_timeout(session, session["round_id"])
                    elif op == 9:
                        st.toggle_effects(u)
                        st.set_effect(u, rnd.choice(EFFECTS + [None]))
                    else:
                        st.rotate_effects()
                        st.end_game(u)
                        st.toggle_game_effects(u)
            except Exception as e:      # any exception in a thread is a failure
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertFalse(any(t.is_alive() for t in threads), "deadlock?")
        self.assertEqual(errors, [])
        assert_invariants(self, st)

    def test_many_users_searching_at_once_are_paired_exactly(self):
        st = new_state()
        n = 200
        results = {}

        def search(uid):
            results[uid] = st.find_partner_or_wait(uid, uid, 1)[0]

        threads = [threading.Thread(target=search, args=(i,)) for i in range(1, n + 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert_invariants(self, st)
        self.assertEqual(len(st._connections), n, "everyone is paired (even count)")
        self.assertEqual(st._waiting, {})
        self.assertEqual(sorted(results.values()).count("matched"), n // 2)

    def test_simultaneous_correct_guesses_score_once(self):
        for _ in range(50):
            st = new_state()
            session = started_game(st)
            guesser, word = session["guesser"], session["word"]
            outcomes = []
            threads = [threading.Thread(target=lambda: outcomes.append(st.process_guess(guesser, word)[0]))
                       for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(outcomes.count("correct"), 1)
            self.assertEqual(session["score"][guesser], 1)


if __name__ == "__main__":
    unittest.main()
