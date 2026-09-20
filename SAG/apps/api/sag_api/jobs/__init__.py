from sag_api.jobs.queue import JobQueue

__all__ = ["InProcessAsyncQueue", "JobQueue"]


def __getattr__(name: str):
    if name == "InProcessAsyncQueue":
        from sag_api.jobs.inproc import InProcessAsyncQueue
        return InProcessAsyncQueue
    raise AttributeError(name)
