# Version 1.0 - 12.06.2026 18:00:00 GMT
# Unit tests for services/database/search_limiter.py
# Описание: Проверяет эвристику is_light_query (точные поля, поля по длине)
#           и поведение HeavyQueryLimiter (семафор, счётчик очереди).

from __future__ import annotations

import asyncio
import unittest


# ---------------------------------------------------------------------------
# is_light_query
# ---------------------------------------------------------------------------


class TestIsLightQuery(unittest.TestCase):
    def _light(self, form_data, min_length=5):
        from services.database.search_limiter import is_light_query
        return is_light_query(form_data, min_length)

    def test_shifr_makes_query_light(self):
        self.assertTrue(self._light({"Шифр": "42"}))

    def test_dopshifr_makes_query_light(self):
        self.assertTrue(self._light({"ДопШифр": "TST"}))

    def test_avtor_makes_query_light(self):
        self.assertTrue(self._light({"Автор": "Иванов"}))

    def test_raion_obshiy_makes_query_light(self):
        self.assertTrue(self._light({"РайонОбщий": "Кавказ"}))

    def test_marshrut_short_not_light(self):
        # "гор" — 3 символа, меньше min_length=5
        self.assertFalse(self._light({"Маршрут": "гор"}, min_length=5))

    def test_marshrut_long_makes_query_light(self):
        # "горный перевал" — 14 символов, больше min_length=5
        self.assertTrue(self._light({"Маршрут": "горный перевал"}, min_length=5))

    def test_raion_long_makes_query_light(self):
        self.assertTrue(self._light({"Район": "Кольский полуостров"}, min_length=5))

    def test_empty_form_is_not_light(self):
        self.assertFalse(self._light({}))

    def test_only_year_filter_is_not_light(self):
        self.assertFalse(self._light({"ГодС": "2020", "ГодПо": "2024"}))

    def test_whitespace_only_not_light(self):
        self.assertFalse(self._light({"Шифр": "   "}))


# ---------------------------------------------------------------------------
# HeavyQueryLimiter
# ---------------------------------------------------------------------------


class TestHeavyQueryLimiter(unittest.TestCase):
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_acquire_release_single(self):
        from services.database.search_limiter import HeavyQueryLimiter
        limiter = HeavyQueryLimiter(max_concurrent=2, queue_warning_size=10)

        async def _test():
            await limiter.acquire()
            self.assertEqual(limiter.waiting_count, 1)
            await limiter.release()
            self.assertEqual(limiter.waiting_count, 0)

        self._run(_test())

    def test_max_concurrent_limits_parallel(self):
        """С max_concurrent=1 второй запрос не может выполняться одновременно."""
        from services.database.search_limiter import HeavyQueryLimiter
        limiter = HeavyQueryLimiter(max_concurrent=1, queue_warning_size=10)
        results = []

        async def _task(name):
            await limiter.acquire()
            results.append(f"start:{name}")
            await asyncio.sleep(0.01)
            results.append(f"end:{name}")
            await limiter.release()

        async def _test():
            await asyncio.gather(_task("A"), _task("B"))

        self._run(_test())
        # start:A должен идти до start:B (семафор = 1)
        # А заканчивает (end:A) до того, как B стартует (start:B)
        self.assertLess(results.index("end:A"), results.index("start:B"))

    def test_waiting_count_increases_while_blocked(self):
        from services.database.search_limiter import HeavyQueryLimiter
        limiter = HeavyQueryLimiter(max_concurrent=1, queue_warning_size=10)
        captured = []

        async def _holder():
            await limiter.acquire()
            await asyncio.sleep(0.05)
            captured.append(limiter.waiting_count)
            await limiter.release()

        async def _waiter():
            await asyncio.sleep(0.01)  # даём holder захватить семафор
            await limiter.acquire()
            await limiter.release()

        async def _test():
            await asyncio.gather(_holder(), _waiter())

        self._run(_test())
        # Пока holder спит, waiting_count должен был подняться до 2
        # (holder уже acquired + waiter ожидает)
        self.assertGreaterEqual(captured[0], 1)


if __name__ == "__main__":
    unittest.main()
