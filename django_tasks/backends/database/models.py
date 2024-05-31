import uuid
from typing import TYPE_CHECKING, Any, Generic, Optional, TypeVar

from django.db import models
from django.utils import timezone
from django.utils.module_loading import import_string
from typing_extensions import ParamSpec

from django_tasks.task import DEFAULT_QUEUE_NAME, ResultStatus, Task

T = TypeVar("T")
P = ParamSpec("P")

if TYPE_CHECKING:
    from .backend import TaskResult

    class GenericBase(Generic[P, T]):
        pass

else:

    class GenericBase:
        """
        https://code.djangoproject.com/ticket/33174
        """

        def __class_getitem__(cls, _):
            return cls


class DBTaskResultQuerySet(models.QuerySet):
    def ready(self) -> "DBTaskResultQuerySet":
        """
        Return tasks which are ready to be processed.
        """
        return (
            self.filter(
                status=ResultStatus.NEW,
            )
            .filter(models.Q(run_after=None) | models.Q(run_after__lte=timezone.now()))
            .order_by("-priority", "run_after")
        )

    def complete(self) -> "DBTaskResultQuerySet":
        return self.filter(status=ResultStatus.COMPLETE)

    def failed(self) -> "DBTaskResultQuerySet":
        return self.filter(status=ResultStatus.FAILED)

    def get_locked(self, retries: int = 3) -> Optional["DBTaskResult"]:
        """
        Get a job, locking the row and accounting for deadlocks.
        """
        for attempt in range(1, retries + 1):
            try:
                return self.select_for_update().first()
            except Exception:
                if attempt == retries:
                    raise
        return None


class DBTaskResult(GenericBase[P, T], models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    status = models.CharField(
        choices=ResultStatus.choices,
        default=ResultStatus.NEW,
        max_length=max(len(value) for value in ResultStatus.values),
    )

    args_kwargs = models.JSONField()

    priority = models.PositiveSmallIntegerField(null=True)

    task_path = models.TextField()

    queue_name = models.TextField(default=DEFAULT_QUEUE_NAME)
    backend_name = models.TextField()

    run_after = models.DateTimeField(null=True)

    result = models.JSONField(default=None, null=True)

    objects = DBTaskResultQuerySet.as_manager()

    @property
    def task(self) -> Task[P, T]:
        task = import_string(self.task_path)

        assert isinstance(task, Task)

        return task.using(
            priority=self.priority,
            queue_name=self.queue_name,
            run_after=self.run_after,
            backend=self.backend_name,
        )

    @property
    def task_result(self) -> "TaskResult[T]":
        from .backend import TaskResult

        result = TaskResult[T](
            db_result=self,
            task=self.task,
            id=str(self.id),
            status=ResultStatus[self.status],
            args=self.args_kwargs["args"],
            kwargs=self.args_kwargs["kwargs"],
            backend=self.backend_name,
        )

        result._result = self.result

        return result

    def claim(self) -> None:
        """
        Mark as job as being run
        """
        self.status = ResultStatus.RUNNING
        self.save(update_fields=["status"])

    def set_result(self, result: Any) -> None:
        self.status = ResultStatus.COMPLETE
        self.result = result
        self.save(update_fields=["status", "result"])

    def set_failed(self) -> None:
        self.status = ResultStatus.FAILED
        self.save(update_fields=["status"])