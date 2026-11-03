from django.core.management.base import BaseCommand

from core.jobs import run_tick


class Command(BaseCommand):
    help = "Recover interrupted tasks, prune old results and schedule due summaries"

    def handle(self, *args: object, **options: object) -> None:
        recovered = run_tick()
        self.stdout.write(
            f"Recovered {recovered} interrupted task(s); pruned old results; "
            "scheduled due summaries."
        )
