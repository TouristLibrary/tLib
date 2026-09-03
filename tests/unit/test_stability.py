# Version 1.0 - 12.06.2026 18:00:00 GMT
# Unit tests for services/file_watcher/stability.py
# Описание: Проверяет стейт-машину stability-window:
#           observe/prune/is_stable/is_group_stable,
#           N-скановую задержку обычных групп,
#           мгновенный проход при required=1 (используется для .delete и reindex.*).

from __future__ import annotations

import unittest
from pathlib import Path


def _get_stability():
    """Импортирует модуль и сбрасывает его глобальный стейт перед каждым тестом."""
    import services.file_watcher.stability as st
    st._observations.clear()
    return st


class TestObserve(unittest.TestCase):
    def test_first_observe_count_is_one(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.zip"
            p.write_bytes(b"x")
            st = _get_stability()
            st.observe([p])
            self.assertIn(p, st._observations)
            _, _, count = st._observations[p]
            self.assertEqual(count, 1)

    def test_unchanged_file_increments_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.zip"
            p.write_bytes(b"stable")
            st = _get_stability()
            st.observe([p])
            st.observe([p])
            _, _, count = st._observations[p]
            self.assertEqual(count, 2)

    def test_changed_file_resets_count_to_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.zip"
            p.write_bytes(b"v1")
            st = _get_stability()
            st.observe([p])
            p.write_bytes(b"v2_longer")
            # После изменения размера — счётчик сбрасывается
            st.observe([p])
            _, _, count = st._observations[p]
            self.assertEqual(count, 1)

    def test_nonexistent_file_skipped(self):
        st = _get_stability()
        ghost = Path("/nonexistent/path/ghost.zip")
        # Не должно бросать исключение
        st.observe([ghost])
        self.assertNotIn(ghost, st._observations)


class TestIsStable(unittest.TestCase):
    def test_stable_after_n_scans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "stable.zip"
            p.write_bytes(b"content")
            st = _get_stability()
            for _ in range(3):
                st.observe([p])
            self.assertTrue(st.is_stable(p, required=3))

    def test_not_stable_before_n_scans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "notstable.zip"
            p.write_bytes(b"content")
            st = _get_stability()
            st.observe([p])
            st.observe([p])
            self.assertFalse(st.is_stable(p, required=3))

    def test_unknown_path_is_not_stable(self):
        st = _get_stability()
        self.assertFalse(st.is_stable(Path("/some/unknown.zip"), required=1))

    def test_required_one_stable_after_single_observe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "quick.zip"
            p.write_bytes(b"x")
            st = _get_stability()
            st.observe([p])
            self.assertTrue(st.is_stable(p, required=1))


class TestIsGroupStable(unittest.TestCase):
    def test_all_stable_returns_true(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "00001-TST.json"
            b = Path(td) / "00001-TST.zip"
            a.write_bytes(b"{}")
            b.write_bytes(b"PK")
            st = _get_stability()
            for _ in range(3):
                st.observe([a, b])
            self.assertTrue(st.is_group_stable([a, b], required=3))

    def test_one_unstable_returns_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "00001-TST.json"
            b = Path(td) / "00001-TST.zip"
            a.write_bytes(b"{}")
            b.write_bytes(b"PK")
            st = _get_stability()
            # Наблюдаем только 2 скана
            st.observe([a, b])
            st.observe([a, b])
            self.assertFalse(st.is_group_stable([a, b], required=3))


class TestPrune(unittest.TestCase):
    def test_prune_removes_stale_entries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.json"
            b = Path(td) / "b.json"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            st = _get_stability()
            st.observe([a, b])
            # Только a остаётся «существующим»
            st.prune({a})
            self.assertIn(a, st._observations)
            self.assertNotIn(b, st._observations)

    def test_prune_empty_set_clears_all(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.zip"
            p.write_bytes(b"x")
            st = _get_stability()
            st.observe([p])
            st.prune(set())
            self.assertEqual(len(st._observations), 0)


if __name__ == "__main__":
    unittest.main()
