import threading
import time

from background_scheduler import SharedTaskScheduler


def _scheduler():
    return SharedTaskScheduler(lambda *_: None, interval_seconds=60, max_workers=1)


def test_scheduler_tracks_completion_and_deduplicates():
    scheduler = _scheduler()
    done = threading.Event()
    try:
        assert scheduler.submit(("test", 1), lambda: (done.set() or {"ok": True}))
        assert not scheduler.submit(("test", 1), lambda: None)
        scheduler.start()
        assert done.wait(2)
        tasks = scheduler.list_tasks()
        assert tasks[0]["status"] == "succeeded"
        assert tasks[0]["result"] == {"ok": True}
    finally:
        scheduler.shutdown()


def test_scheduler_retries_and_records_failure():
    scheduler = _scheduler()
    attempts = []
    done = threading.Event()

    def callback():
        attempts.append(time.time())
        if len(attempts) < 2:
            raise RuntimeError("temporary")
        done.set()

    try:
        assert scheduler.submit("retry", callback, max_retries=1)
        scheduler.start()
        assert done.wait(3)
        assert len(attempts) == 2
        assert scheduler.list_tasks()[0]["status"] == "succeeded"
    finally:
        scheduler.shutdown()
