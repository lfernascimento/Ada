"""Live execution transport. No model thinking blocks or provider credentials."""
from contextvars import ContextVar

sink=ContextVar('runtime_event_sink',default=None)

def publish(packet):
    callback=sink.get()
    if callback:
        try:callback(packet)
        except Exception:pass  # A disconnected viewer must not roll back an audit event.

tasks={}
interrupted=set()

def register(run_id):
    import asyncio
    tasks[run_id]=asyncio.current_task()

def unregister(run_id):
    tasks.pop(run_id,None);interrupted.discard(run_id)

def interrupt(run_id):
    task=tasks.get(run_id)
    if task and not task.done():
        interrupted.add(run_id);task.cancel()
