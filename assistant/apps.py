from django.apps import AppConfig


class AssistantConfig(AppConfig):
    """Провайдер модели, общий семейный бюджет и учёт его расхода."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "assistant"
