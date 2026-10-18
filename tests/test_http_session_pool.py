from concurrent.futures import ThreadPoolExecutor
import threading
import time

from runtime.http_session_pool import HttpSessionPool


def test_pool_reuses_sessions_between_short_lived_threads_without_concurrent_use():
    guard = threading.Lock()
    active = set()
    calls = {}
    created = []

    class Session:
        def __init__(self, session_id):
            self.session_id = session_id

        def post(self, url, *, json, timeout):
            with guard:
                assert self.session_id not in active
                active.add(self.session_id)
                calls[self.session_id] = calls.get(self.session_id, 0) + 1
            time.sleep(0.002)
            with guard:
                active.remove(self.session_id)
            return json

    def factory():
        session = Session(len(created))
        created.append(session)
        return session

    pool = HttpSessionPool(factory, max_size=4)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda number: pool.post("http://local", json={"n": number}, timeout=1),
                range(40),
            )
        )

    assert results == [{"n": number} for number in range(40)]
    assert len(created) <= 4
    assert sum(calls.values()) == 40
    assert any(count > 1 for count in calls.values())
