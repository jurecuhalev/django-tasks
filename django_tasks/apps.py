from django.apps import AppConfig


class TasksAppConfig(AppConfig):
    name = "django_tasks"

    def ready(self) -> None:
        from . import signal_handlers  # noqa
