import datetime
from io import StringIO

from django.core.management import call_command
from django.db import transaction
from django.tasks import TaskResultStatus, task, task_backends
from django.tasks.backends.dummy import DummyBackend
from django.test import TestCase, override_settings
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from core.jobs import enqueue_on_commit, recover_interrupted, run_tick

DUMMY_TASKS = {
    "default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"},
}


@task
def add(left: int, right: int) -> int:
    return left + right


def make_result(
    *,
    status: str,
    started_at: datetime.datetime | None = None,
    finished_at: datetime.datetime | None = None,
) -> DBTaskResult:
    return DBTaskResult.objects.create(
        status=status,
        args_kwargs={"args": [1, 2], "kwargs": {}},
        task_path="core.tests.test_jobs.add",
        queue_name="default",
        backend_name="default",
        run_after=timezone.now(),
        exception_class_path="",
        traceback="",
        started_at=started_at,
        finished_at=finished_at,
    )


@override_settings(TASKS=DUMMY_TASKS)
class EnqueueOnCommitTest(TestCase):
    def setUp(self) -> None:
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        backend.results.clear()

    def test_task_is_enqueued_only_after_the_transaction_commits(self) -> None:
        with self.captureOnCommitCallbacks() as callbacks:
            enqueue_on_commit(add, 1, 2)
        self.assertEqual(len(callbacks), 1)

        with self.captureOnCommitCallbacks(execute=True):
            enqueue_on_commit(add, 1, 2)

        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        self.assertEqual(len(backend.results), 1)
        self.assertEqual(backend.results[0].args, [1, 2])

    def test_rolled_back_transaction_enqueues_nothing(self) -> None:
        with self.captureOnCommitCallbacks() as callbacks:
            with transaction.atomic():
                enqueue_on_commit(add, 1, 2)
                transaction.set_rollback(True)

        self.assertEqual(callbacks, [])


class RecoverInterruptedTest(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()

    def test_old_running_task_returns_to_the_queue_without_start_time(self) -> None:
        result = make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=31),
        )

        recovered = recover_interrupted()

        result.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(result.status, TaskResultStatus.READY)
        self.assertIsNone(result.started_at)

    def test_fresh_running_task_is_left_alone(self) -> None:
        result = make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=1),
        )

        recovered = recover_interrupted()

        result.refresh_from_db()
        self.assertEqual(recovered, 0)
        self.assertEqual(result.status, TaskResultStatus.RUNNING)

    def test_finished_task_is_left_alone(self) -> None:
        result = make_result(
            status=TaskResultStatus.SUCCESSFUL,
            started_at=self.now - datetime.timedelta(hours=1),
            finished_at=self.now - datetime.timedelta(hours=1),
        )

        recover_interrupted()

        result.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)

    def test_second_recovery_finds_nothing(self) -> None:
        make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=31),
        )

        self.assertEqual(recover_interrupted(), 1)
        self.assertEqual(recover_interrupted(), 0)


class RunTickTest(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()

    def test_tick_recovers_stuck_task_and_prunes_old_results(self) -> None:
        stuck = make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=31),
        )
        old_success = make_result(
            status=TaskResultStatus.SUCCESSFUL,
            finished_at=self.now - datetime.timedelta(days=15),
        )
        old_failure = make_result(
            status=TaskResultStatus.FAILED,
            finished_at=self.now - datetime.timedelta(days=15),
        )
        fresh_success = make_result(
            status=TaskResultStatus.SUCCESSFUL,
            finished_at=self.now - datetime.timedelta(days=1),
        )
        old_running = make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=31),
        )

        with self.assertLogs("core.jobs", level="WARNING"):
            self.assertEqual(run_tick(), 2)

        self.assertFalse(
            DBTaskResult.objects.filter(pk__in=[old_success.pk, old_failure.pk]).exists()
        )
        self.assertTrue(DBTaskResult.objects.filter(pk=fresh_success.pk).exists())
        stuck.refresh_from_db()
        old_running.refresh_from_db()
        self.assertEqual(stuck.status, TaskResultStatus.READY)
        self.assertEqual(old_running.status, TaskResultStatus.READY)

    def test_tick_with_nothing_to_do_is_a_no_op(self) -> None:
        self.assertEqual(run_tick(), 0)

    def test_tick_command_runs_the_same_maintenance(self) -> None:
        result = make_result(
            status=TaskResultStatus.RUNNING,
            started_at=self.now - datetime.timedelta(minutes=31),
        )

        stdout = StringIO()
        with self.assertLogs("core.jobs", level="WARNING"):
            call_command("tick", stdout=stdout)

        result.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.READY)
        self.assertIn("Recovered 1 interrupted task(s)", stdout.getvalue())
