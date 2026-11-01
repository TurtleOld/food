from django.core.management.base import BaseCommand

from core.jobs import run_tick


class Command(BaseCommand):
    help = "Recover interrupted background tasks and prune old task results"

    def handle(self, *args: object, **options: object) -> None:
        recovered = run_tick()
        self.stdout.write(f"Recovered {recovered} interrupted task(s); pruned old results.")
