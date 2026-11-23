import unittest

from gateway import AimdLimiter, TokenBucket


def backends(**changes):
    base = {name: {"enabled": True, "weight": 100} for name in ("app1", "app2", "app3")}
    for name, values in changes.items():
        base[name].update(values)
    return base


class TokenBucketTests(unittest.TestCase):
    def test_burst_then_reject(self):
        bucket = TokenBucket(rate=10, capacity=5)
        self.assertEqual([bucket.allow("normal", now=0) for _ in range(6)], [True] * 5 + [False])

    def test_tokens_refill_over_time(self):
        bucket = TokenBucket(rate=10, capacity=5)
        for _ in range(5):
            bucket.allow("normal", now=0)
        self.assertEqual([bucket.allow("normal", now=0.2) for _ in range(3)], [True, True, False])

    def test_priorities_are_shed_in_order(self):
        bucket = TokenBucket(rate=0, capacity=10)
        for _ in range(5):
            self.assertTrue(bucket.allow("normal", now=0))   # 5 tokens left
        self.assertFalse(bucket.allow("low", now=0))          # low needs more than half the bucket
        self.assertTrue(bucket.allow("normal", now=0))
        for _ in range(10):
            bucket.allow("normal", now=0)                     # drain it
        self.assertFalse(bucket.allow("normal", now=0))
        self.assertTrue(bucket.allow("critical", now=0))      # critical is never limited


class AimdLimiterTests(unittest.TestCase):
    def make(self):
        return AimdLimiter(per_backend=30, increase_per_s=5, decrease_factor=0.5)

    def test_starts_at_full_capacity(self):
        self.assertEqual(self.make().refresh(backends(), now=0), 90)

    def test_limit_shrinks_to_remaining_capacity_then_grows_back(self):
        limiter = self.make()
        limiter.refresh(backends(), now=0)
        down = backends(app1={"enabled": False}, app3={"enabled": False})
        self.assertEqual(limiter.refresh(down, now=1), 30)    # 1 of 3 servers left
        self.assertEqual(limiter.refresh(backends(), now=3), 40)   # +5 per second
        self.assertEqual(limiter.refresh(backends(), now=100), 90)  # capped at full capacity

    def test_ramping_server_counts_by_weight(self):
        limiter = self.make()
        self.assertEqual(limiter.refresh(backends(app1={"weight": 10}), now=0), 63)

    def test_rejects_above_limit(self):
        limiter = self.make()
        down = backends(app1={"enabled": False}, app3={"enabled": False})
        results = [limiter.try_acquire(down, now=0) for _ in range(31)]
        self.assertEqual(results, [True] * 30 + [False])

    def test_failure_halves_the_limit(self):
        limiter = self.make()
        limiter.try_acquire(backends(), now=0)
        limiter.release(failed=True)
        self.assertEqual(limiter.limit, 45)


if __name__ == "__main__":
    unittest.main()