import unittest
from rolling import choose_evictions

def row(name, size):
    return dict(target=name, bytes=size)

class BudgetTests(unittest.TestCase):
    def test_active_files_are_kept_when_rolling(self):
        rows = [row('old', 4), row('playing', 4), row('future', 2)]
        self.assertEqual(choose_evictions(rows, {'playing'}, 5, 10), [rows[0], rows[2]])

    def test_insufficient_space_cannot_evict_playing_or_next(self):
        with self.assertRaisesRegex(RuntimeError, 'RAM_CAPACITY_PINNED'):
            choose_evictions([row('playing', 6), row('next', 3)], {'playing', 'next'}, 2, 10)

    def test_exact_fit_does_not_evict(self):
        self.assertEqual(choose_evictions([row('A', 6)], set(), 4, 10), [])

if __name__ == '__main__':
    unittest.main()
