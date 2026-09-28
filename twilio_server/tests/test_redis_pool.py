import importlib.util
import unittest

import redis

import redis_pool


class ConnectTests(unittest.TestCase):
    def test_threads_beyond_the_ceiling_wait_instead_of_opening_connections(self):
        client = redis_pool.connect(3, "redis://default:secret@redis.example.com:18934/0")
        pool = client.connection_pool
        # A plain ConnectionPool would open one per thread, or raise at its limit.
        self.assertIsInstance(pool, redis.BlockingConnectionPool)
        self.assertEqual(pool.max_connections, 3)
        self.assertEqual(pool.timeout, redis_pool.WAIT)
        settings = pool.connection_kwargs
        self.assertEqual((settings["host"], settings["port"], settings["password"]),
                         ("redis.example.com", 18934, "secret"))
        self.assertEqual((settings["socket_connect_timeout"], settings["socket_timeout"]), (5, 10))


@unittest.skipUnless(importlib.util.find_spec("celery"), "needs celery")
class TaskSenderTests(unittest.TestCase):
    def test_the_voice_service_keeps_one_connection_for_sending_tasks(self):
        import worker_client
        self.assertEqual(worker_client.celery_app.conf.broker_pool_limit, 1)


if __name__ == "__main__":
    unittest.main()
