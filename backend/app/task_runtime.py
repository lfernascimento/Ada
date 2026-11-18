"""Single local task at a time, with one bounded budget context per entry point."""
import functools,threading
from fastapi import HTTPException
from . import config
from .model_gateway import TaskBudget,active_budget,active_images
_gate=threading.BoundedSemaphore(1)
def task_limited(fn):
    @functools.wraps(fn)
    async def wrapped(*args,**kwargs):
        if not _gate.acquire(blocking=False):raise HTTPException(409,'Outra tarefa de modelo está em andamento.')
        token=None
        image_token=active_images.set([])
        try:
            if not 0<config.TASK_BUDGET_USD<=5:raise HTTPException(503,'Orçamento inválido.')
            if active_budget.get() is None:token=active_budget.set(TaskBudget(limit=config.TASK_BUDGET_USD))
            return await fn(*args,**kwargs)
        finally:
            if token is not None:active_budget.reset(token)
            active_images.reset(image_token)
            _gate.release()
    return wrapped
