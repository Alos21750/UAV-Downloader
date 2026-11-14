"""Non-blocking barriers for commands owned by different desktop services."""
from concurrent.futures import Future
import threading
from uav_downloader.core.stop import StopTimeoutError


def all_completed(futures):
    """Finish after every input, preserving any error without blocking a caller."""
    futures = tuple(futures)
    result = Future()
    lock = threading.Lock()

    def completed(_=None):
        with lock:
            if result.done() or not all(future.done() for future in futures):
                return
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as error:
                    errors.append(error)
            if errors:
                # An unfinished worker must not be presented as only a save
                # failure when several close operations fail together.
                result.set_exception(next((error for error in errors if isinstance(error, StopTimeoutError)), errors[0]))
            else:
                result.set_result(True)

    for future in futures:
        future.add_done_callback(completed)
    completed()
    return result
