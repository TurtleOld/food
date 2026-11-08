from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class SheetWaitingStateTests(TestCase):
    """Общая шторка: состояние ожидания и повтор доступны на каждой странице с шторкой."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)

    def sheet_pages(self):
        return {
            "diary": reverse("core:day"),
            "catalog": reverse("core:product_list"),
        }

    def test_sheet_has_a_waiting_state_for_assistive_technology(self):
        for name, url in self.sheet_pages().items():
            with self.subTest(page=name):
                response = self.client.get(url)

                self.assertContains(response, 'id="sheet-body"')
                self.assertContains(response, 'aria-busy="false"')
                self.assertContains(response, 'role="status"')
                self.assertContains(response, "Загружаем форму")

    def test_sheet_explains_a_failed_load_and_offers_retry(self):
        for name, url in self.sheet_pages().items():
            with self.subTest(page=name):
                response = self.client.get(url)

                self.assertContains(response, 'role="alert"')
                self.assertContains(response, "Форма не загрузилась")
                self.assertContains(response, "Попробовать снова")
