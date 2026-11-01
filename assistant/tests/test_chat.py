"""Ход Ассистента через HTTP-шов: отправка, ожидание, ошибки, фото и инструменты."""

import copy
import datetime
import json
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.tasks import task_backends
from django.tasks.backends.dummy import DummyBackend
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from assistant.chat import (
    EMPTY_ANSWER,
    INTERRUPTED,
    PROVIDER_FAILED,
    TOO_MANY_STEPS,
    TURN_FAILED,
    execute_turn,
)
from assistant.gateway import LLMGatewayError, LLMResult, ToolCall, Usage
from assistant.models import Conversation, Message, Proposal, Turn
from core.models import Barcode, DailyTarget, DiaryEntry, Product, Profile, WeightMeasurement
from core.off import OffLookup, Outcome
from core.profile import target_suggestion

User = get_user_model()

DUMMY_TASKS = {
    "default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"},
}
COST = Decimal("0.010000")


def answer(text: str = "Готово") -> LLMResult:
    return LLMResult(
        text=text,
        tool_calls=(),
        usage=Usage(input_tokens=100, cached_input_tokens=0, output_tokens=50),
        cost_rub=COST,
        model="deepseek-flash",
    )


def tool_answer(name: str, arguments: object, text: str = "") -> LLMResult:
    return LLMResult(
        text=text,
        tool_calls=(
            ToolCall(
                id="call-1",
                name=name,
                arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
            ),
        ),
        usage=Usage(input_tokens=100, cached_input_tokens=0, output_tokens=50),
        cost_rub=COST,
        model="deepseek-flash",
    )


class Script:
    """Фейковый провайдер через шов `assistant.chat.complete`."""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, messages, *, tools=None):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def jpeg(color: str = "red", size: tuple[int, int] = (20, 20)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG")
    return output.getvalue()


def photo(content: bytes | None = None, name: str = "label.jpg", kind: str = "image/jpeg"):
    return SimpleUploadedFile(name, content or jpeg(), content_type=kind)


@override_settings(TASKS=DUMMY_TASKS)
class ChatTestCase(TestCase):
    def setUp(self) -> None:
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        backend.results.clear()
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(self.member)

    def send(self, text: str = "Привет", upload=None, **extra):
        data = {"text": text, **extra}
        if upload is not None:
            data["photo"] = upload
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("assistant:chat_send"), data)

    def only_turn(self) -> Turn:
        return Turn.objects.get()

    def run_with(self, turn: Turn, script: Script) -> Script:
        with mock.patch("assistant.chat.complete", side_effect=script):
            execute_turn(turn.pk)
        turn.refresh_from_db()
        return script

    def run_tool(self, name: str, arguments: object, member=None) -> tuple[Turn, dict, Script]:
        if member is not None:
            self.client.force_login(member)
        self.send("Запрос")
        turn = Turn.objects.latest("pk")
        script = Script(tool_answer(name, arguments), answer())
        self.run_with(turn, script)
        self.assertEqual(turn.status, Turn.Status.SUCCEEDED, turn.error_message)
        output = json.loads(script.calls[1]["messages"][-1]["content"])
        return turn, output, script


class ChatShellTest(ChatTestCase):
    def test_chat_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("assistant:chat"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_empty_chat_renders_the_composer_and_nav(self):
        response = self.client.get(reverse("assistant:chat"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="feed"')
        self.assertContains(response, "Отправить")
        self.assertContains(response, reverse("assistant:chat_send"))

    def test_post_creates_one_personal_turn_and_shows_waiting(self):
        response = self.send("Сколько калорий в молоке?")

        self.assertRedirects(response, reverse("assistant:chat"))
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.member, self.member)
        turn = self.only_turn()
        self.assertEqual(turn.status, Turn.Status.PENDING)
        self.assertEqual(turn.request_text, "Сколько калорий в молоке?")
        self.assertEqual(turn.messages.get().role, Message.Role.USER)
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        self.assertEqual([result.args for result in backend.results], [[turn.pk]])

        page = self.client.get(reverse("assistant:chat"))
        self.assertContains(page, "Сколько калорий в молоке?")
        self.assertContains(page, "Ассистент думает")
        self.assertContains(page, 'hx-trigger="every 2s"')

    def test_htmx_post_returns_only_the_feed_fragment(self):
        response = self.client.post(
            reverse("assistant:chat_send"),
            {"text": "Привет"},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="feed"')
        self.assertNotContains(response, "<html")

    def test_repeated_submit_of_the_same_request_does_not_duplicate_the_turn(self):
        self.send("Один и тот же запрос")
        self.send("  Один   и тот же запрос ")

        self.assertEqual(Turn.objects.count(), 1)
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        self.assertEqual(len(backend.results), 1)

    def test_blank_message_is_rejected_without_a_turn(self):
        response = self.send("   ")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите сообщение или приложите фото")
        self.assertEqual(Turn.objects.count(), 0)

    def test_too_long_message_is_rejected_without_a_turn(self):
        response = self.send("я" * 4001)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4000")
        self.assertEqual(Turn.objects.count(), 0)

    def test_feed_shows_only_own_conversation(self):
        foreign = Conversation.for_member(self.other)
        turn = Turn.objects.create(conversation=foreign, request_dedupe_key="x")
        Message.objects.create(
            conversation=foreign,
            turn=turn,
            role=Message.Role.USER,
            text="Чужой секрет",
        )

        response = self.client.get(reverse("assistant:chat"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Чужой секрет")

    def test_finished_turn_is_shown_after_polling(self):
        self.send("Дай совет")
        turn = self.only_turn()
        self.run_with(turn, Script(answer("Пейте воду.")))

        page = self.client.get(reverse("assistant:chat"))

        self.assertContains(page, "Дай совет")
        self.assertContains(page, "Пейте воду.")
        self.assertNotContains(page, "Ассистент думает")


class PhotoTest(ChatTestCase):
    def test_valid_photo_is_sent_once_as_an_image_and_deleted_after_the_answer(self):
        response = self.send("Что на этикетке?", photo())

        self.assertRedirects(response, reverse("assistant:chat"))
        turn = self.only_turn()
        message = turn.messages.get()
        self.assertTrue(message.has_image)
        self.assertTrue(message.photo)

        script = self.run_with(turn, Script(answer("Вижу молоко.")))

        content = script.calls[0]["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Что на этикетке?"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        message.refresh_from_db()
        self.assertIsNone(message.photo)
        self.assertTrue(message.has_image)
        page = self.client.get(reverse("assistant:chat"))
        self.assertContains(page, "Фото (удалено после ответа)")

    def test_photo_is_resized_and_exif_is_stripped(self):
        output = BytesIO()
        image = Image.new("RGB", (2000, 1000), "blue")
        exif = Image.Exif()
        exif[274] = 6
        image.save(output, format="JPEG", exif=exif)

        self.send(
            "Большое фото",
            SimpleUploadedFile("big.jpg", output.getvalue(), content_type="image/jpeg"),
        )

        photo_data = self.only_turn().messages.get().photo
        assert photo_data is not None
        stored = bytes(photo_data)
        with Image.open(BytesIO(stored)) as prepared:
            self.assertEqual(max(prepared.size), 1600)
            self.assertEqual(len(prepared.getexif()), 0)

    def test_history_never_resends_a_photo(self):
        self.send("Первое фото", photo())
        first = self.only_turn()
        self.run_with(first, Script(answer("Вижу.")))
        self.send("Второе")
        second = Turn.objects.latest("pk")

        script = self.run_with(second, Script(answer("Хорошо.")))

        prompt = script.calls[0]["messages"]
        self.assertTrue(all(isinstance(item["content"], str) for item in prompt))
        self.assertNotIn("image_url", json.dumps(prompt))

    def test_photo_only_message_is_accepted(self):
        self.send("", photo())

        turn = self.only_turn()
        message = turn.messages.get()
        self.assertEqual(message.text, "")
        self.assertTrue(message.has_image)

        script = self.run_with(turn, Script(answer("Нужно количество.")))
        content = script.calls[0]["messages"][-1]["content"]
        self.assertEqual([part["type"] for part in content], ["image_url"])

    def test_oversized_photo_is_rejected_before_a_turn_exists(self):
        with override_settings(ASSISTANT_PHOTO_MAX_BYTES=200):
            response = self.send("Фото", photo(jpeg(size=(400, 400))))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "МБ")
        self.assertEqual(Turn.objects.count(), 0)

    def test_unsupported_file_is_rejected_before_a_turn_exists(self):
        response = self.send(
            "Файл",
            SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JPEG")
        self.assertEqual(Turn.objects.count(), 0)

    def test_photo_is_deleted_after_a_provider_error(self):
        self.send("Что на фото?", photo())
        turn = self.only_turn()

        with self.assertLogs("assistant.chat", level="WARNING"):
            self.run_with(turn, Script(LLMGatewayError("down")))

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertIsNone(turn.messages.get().photo)


class FailureAndRetryTest(ChatTestCase):
    def test_unconfigured_assistant_fails_with_a_clear_russian_message(self):
        self.send("Привет")
        turn = self.only_turn()

        execute_turn(turn.pk)

        turn.refresh_from_db()
        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertIn("не настроен", turn.error_message)
        page = self.client.get(reverse("assistant:chat"))
        self.assertContains(page, "не настроен")
        self.assertContains(page, "Повторить")

    def test_provider_error_fails_the_turn_and_allows_an_explicit_retry(self):
        self.send("Привет")
        turn = self.only_turn()
        with self.assertLogs("assistant.chat", level="WARNING"):
            self.run_with(turn, Script(LLMGatewayError("down")))

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, PROVIDER_FAILED)
        page = self.client.get(reverse("assistant:chat"))
        self.assertContains(page, PROVIDER_FAILED)
        retry_form = f'<textarea name="text" hidden>{"Привет"}</textarea>'
        self.assertContains(page, retry_form)

        self.send("Привет")
        self.assertEqual(Turn.objects.count(), 2)
        retry = Turn.objects.latest("pk")
        self.assertEqual(retry.status, Turn.Status.PENDING)

    def test_empty_answer_is_a_failure_not_an_empty_bubble(self):
        self.send("Привет")
        turn = self.only_turn()

        self.run_with(turn, Script(answer("   ")))

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, EMPTY_ANSWER)
        self.assertFalse(turn.messages.filter(role=Message.Role.ASSISTANT).exists())

    def test_unexpected_error_keeps_a_clear_message_and_no_photo(self):
        self.send("Привет", photo())
        turn = self.only_turn()

        with self.assertLogs("assistant.chat", level="ERROR"):
            self.run_with(turn, Script(RuntimeError("boom")))

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, TURN_FAILED)
        self.assertIsNone(turn.messages.get().photo)

    def test_turn_without_text_does_not_offer_a_broken_retry(self):
        self.send("", photo())
        turn = self.only_turn()
        with self.assertLogs("assistant.chat", level="WARNING"):
            self.run_with(turn, Script(LLMGatewayError("down")))

        page = self.client.get(reverse("assistant:chat"))

        self.assertContains(page, "Фото удалено")
        self.assertNotContains(page, "Повторить")


class ExecutionTest(ChatTestCase):
    def test_answer_is_stored_once_and_usage_cost_is_kept_on_the_turn(self):
        self.send("Как дела?")
        turn = self.only_turn()

        script = self.run_with(turn, Script(answer("Хорошо.")))

        self.assertEqual(len(script.calls), 1)
        self.assertEqual(turn.status, Turn.Status.SUCCEEDED)
        self.assertEqual(turn.cost_rub, COST)
        self.assertIsNotNone(turn.finished_at)
        self.assertEqual(
            list(turn.messages.values_list("role", "text")),
            [(Message.Role.USER, "Как дела?"), (Message.Role.ASSISTANT, "Хорошо.")],
        )
        self.assertIn("tools", script.calls[0])
        self.assertEqual(
            {item["function"]["name"] for item in script.calls[0]["tools"]},
            {
                "search_catalog",
                "lookup_barcode",
                "read_diary",
                "read_profile",
                "propose_product",
                "propose_entry",
            },
        )

    def test_repeated_task_delivery_executes_the_turn_once(self):
        self.send("Привет")
        turn = self.only_turn()
        script = Script(answer("Один ответ"))

        with mock.patch("assistant.chat.complete", side_effect=script):
            execute_turn(turn.pk)
            execute_turn(turn.pk)

        self.assertEqual(len(script.calls), 1)
        self.assertEqual(turn.messages.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_stale_running_turn_fails_without_a_second_provider_call(self):
        self.send("Привет")
        turn = self.only_turn()
        Turn.objects.filter(pk=turn.pk).update(
            status=Turn.Status.RUNNING,
            started_at=timezone.now() - datetime.timedelta(hours=1),
        )

        execute_turn(turn.pk)

        turn.refresh_from_db()
        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, INTERRUPTED)

    def test_lost_running_turn_is_finished_by_the_next_view(self):
        self.send("Завис", photo())
        turn = self.only_turn()
        Turn.objects.filter(pk=turn.pk).update(
            status=Turn.Status.RUNNING,
            started_at=timezone.now() - datetime.timedelta(hours=1),
        )

        page = self.client.get(reverse("assistant:chat"))

        turn.refresh_from_db()
        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, INTERRUPTED)
        self.assertIsNone(turn.messages.get().photo)
        self.assertContains(page, INTERRUPTED)
        self.assertContains(page, "Повторить")

    def test_fresh_running_turn_is_left_to_its_worker(self):
        self.send("Привет")
        turn = self.only_turn()
        started = timezone.now()
        Turn.objects.filter(pk=turn.pk).update(
            status=Turn.Status.RUNNING,
            started_at=started,
        )

        execute_turn(turn.pk)

        turn.refresh_from_db()
        self.assertEqual(turn.status, Turn.Status.RUNNING)
        self.assertEqual(turn.started_at, started)

    def test_history_is_bounded_and_excludes_failed_turns(self):
        for text in ("Первый", "Второй", "Третий"):
            self.send(text)
            self.run_with(Turn.objects.latest("pk"), Script(answer(f"Ответ: {text}")))
        self.send("Сломанный")
        broken = Turn.objects.latest("pk")
        with self.assertLogs("assistant.chat", level="WARNING"):
            self.run_with(broken, Script(LLMGatewayError("down")))

        with override_settings(ASSISTANT_HISTORY_MESSAGES=2):
            self.send("Новый вопрос")
            turn = Turn.objects.latest("pk")
            script = self.run_with(turn, Script(answer("Ответ")))

        roles = [(item["role"], item["content"]) for item in script.calls[0]["messages"]]
        self.assertEqual(roles[0][0], "system")
        self.assertEqual(roles[-1], ("user", "Новый вопрос"))
        self.assertEqual(len(roles), 4)
        self.assertNotIn(("user", "Сломанный"), roles)
        self.assertNotIn(("user", "Первый"), roles)

    def test_stale_pending_turn_is_enqueued_again_on_the_next_view(self):
        self.send("Потерянный")
        turn = self.only_turn()
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        backend.results.clear()
        Turn.objects.filter(pk=turn.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=1)
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(reverse("assistant:chat"))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(reverse("assistant:chat"))

        self.assertEqual([result.args for result in backend.results], [[turn.pk]])

    def test_fresh_pending_turn_is_not_enqueued_twice(self):
        self.send("Ждёт")
        backend = task_backends["default"]
        assert isinstance(backend, DummyBackend)
        backend.results.clear()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(reverse("assistant:chat"))

        self.assertEqual(backend.results, [])

    def test_too_many_tool_rounds_are_bounded_and_fail_clearly(self):
        self.send("Слишком сложно")
        turn = self.only_turn()
        script = Script(*[tool_answer("read_profile", {}) for _ in range(6)])

        self.run_with(turn, script)

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, TOO_MANY_STEPS)
        self.assertEqual(len(script.calls), 5)
        self.assertEqual(turn.cost_rub, COST * 5)

    def test_one_round_with_too_many_tool_calls_is_rejected(self):
        self.send("Слишком много")
        turn = self.only_turn()
        calls = tuple(
            ToolCall(id=f"call-{index}", name="read_profile", arguments="{}") for index in range(9)
        )
        too_many = LLMResult(
            text="",
            tool_calls=calls,
            usage=Usage(100, 0, 50),
            cost_rub=COST,
            model="deepseek-flash",
        )

        script = self.run_with(turn, Script(too_many))

        self.assertEqual(turn.status, Turn.Status.FAILED)
        self.assertEqual(turn.error_message, TOO_MANY_STEPS)
        self.assertEqual(len(script.calls), 1)


class ToolTurnTest(ChatTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = Product.objects.get(name="Молоко 3.2%")

    def test_search_catalog_runs_through_a_real_turn(self):
        turn, output, script = self.run_tool("search_catalog", {"query": "молоко"})

        before = Product.objects.count()
        self.assertEqual([item["name"] for item in output["products"]], ["Молоко 3.2%"])
        self.assertEqual(output["total"], 1)
        self.assertFalse(output["truncated"])
        self.assertEqual(Product.objects.count(), before)
        self.assertEqual(script.calls[0]["messages"][-1]["content"], "Запрос")

    def test_search_catalog_rejects_a_blank_query_and_is_limited(self):
        _, blank, _ = self.run_tool("search_catalog", {"query": "  "})
        self.assertIn("error", blank)

        Product.objects.bulk_create(
            Product(
                name=f"Тестовый продукт {index:02d}",
                base_unit="g",
                calories=Decimal("100"),
                proteins=Decimal("1"),
                fats=Decimal("1"),
                carbs=Decimal("1"),
                author=self.member,
            )
            for index in range(21)
        )
        _, limited, _ = self.run_tool("search_catalog", {"query": "тестовый продукт"})

        self.assertEqual(len(limited["products"]), 20)
        self.assertEqual(limited["total"], 21)
        self.assertTrue(limited["truncated"])

    def test_barcode_tool_uses_catalog_then_open_food_facts(self):
        Barcode.objects.create(code="4006381333931", product=self.product)
        with mock.patch("assistant.tools.off_lookup") as off_lookup:
            _, found, _ = self.run_tool("lookup_barcode", {"code": "4006381333931"})
        off_lookup.assert_not_called()
        self.assertEqual(found["product"]["name"], "Молоко 3.2%")
        self.assertIsNone(found["off"])

        off = OffLookup(
            outcome=Outcome.FOUND,
            name="Кола",
            base_unit="ml",
            calories=Decimal("42.0"),
            proteins=Decimal("0.0"),
            fats=Decimal("0.0"),
            carbs=Decimal("10.6"),
            url="https://world.openfoodfacts.org/product/4006381333931",
        )
        with mock.patch("assistant.tools.off_lookup", return_value=off):
            _, fallback, _ = self.run_tool("lookup_barcode", {"code": "5901234123457"})
        self.assertIsNone(fallback["product"])
        self.assertEqual(fallback["off"]["outcome"], "found")
        self.assertEqual(fallback["off"]["url"], off.url)

        _, invalid, _ = self.run_tool("lookup_barcode", {"code": "123"})
        self.assertIn("error", invalid)

    def test_diary_tool_reads_only_the_owners_entries(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.BREAKFAST,
            product=self.product,
            amount=Decimal("200"),
        )
        DiaryEntry.objects.create(
            member=self.other,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.product,
            amount=Decimal("500"),
        )

        _, output, _ = self.run_tool("read_diary", {"date": "2026-10-31"})

        day = output["days"][0]
        self.assertEqual(len(day["entries"]), 1)
        self.assertEqual(day["entries"][0]["amount"], "200.0")
        self.assertEqual(Decimal(day["calories"]), Decimal("120.0"))

        for arguments in (
            {"date": "31.10.2026"},
            {"date": "2026-11-01", "date_to": "2026-10-31"},
            {"date": "2026-01-01", "date_to": "2026-02-15"},
            {"date": "2026-01-01", "date_to": "not-a-date"},
        ):
            _, error, _ = self.run_tool("read_diary", arguments)
            self.assertIn("error", error)

    def test_profile_tool_uses_the_real_profile_and_target(self):
        _, empty, _ = self.run_tool("read_profile", {})
        self.assertIsNone(empty["profile"])
        self.assertIsNone(empty["suggestion"])

        Profile.objects.create(
            member=self.member,
            height_cm=180,
            birth_date=datetime.date(1990, 1, 2),
            sex=Profile.Sex.MALE,
            activity=Profile.Activity.MODERATE,
        )
        WeightMeasurement.objects.create(
            member=self.member,
            date=datetime.date(2026, 10, 31),
            weight_kg=Decimal("80"),
        )
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        _, output, _ = self.run_tool("read_profile", {})
        expected = target_suggestion(self.member)

        self.assertEqual(output["profile"]["height_cm"], 180)
        self.assertTrue(output["profile"]["is_complete"])
        self.assertEqual(output["target"]["calories"], "2000.0")
        assert expected is not None
        self.assertEqual(output["suggestion"]["calories"], str(expected.calories))

    def test_entry_proposal_from_a_turn_is_confirmed_on_the_screen(self):
        self.run_tool(
            "propose_entry",
            {
                "product_id": self.product.pk,
                "amount": 150,
                "date": "2026-10-31",
                "meal_type": "lunch",
            },
        )

        proposal = Proposal.objects.get()
        self.assertEqual(proposal.member, self.member)
        page = self.client.get(reverse("assistant:proposals"))
        self.assertContains(page, "Молоко 3.2%")
        self.assertContains(page, "Подтвердить")

        confirm = self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))

        self.assertRedirects(confirm, reverse("assistant:proposals"))
        self.assertEqual(DiaryEntry.objects.get().amount, Decimal("150.0"))

    def test_entry_proposal_asks_for_missing_date_and_meal_before_existing(self):
        _, output, _ = self.run_tool(
            "propose_entry", {"product_id": self.product.pk, "amount": 100}
        )

        self.assertTrue(output["needs_clarification"])
        self.assertEqual(output["missing"], ["date", "meal_type"])
        self.assertIn("Приём пищи", output["question"])
        self.assertEqual(Proposal.objects.count(), 0)

    def test_entry_proposal_with_new_product_returns_similar_but_does_not_merge(self):
        before = list(Product.objects.values_list("pk", "name", "calories"))

        _, output, _ = self.run_tool(
            "propose_entry",
            {
                "amount": 30,
                "date": "2026-10-31",
                "meal_type": "breakfast",
                "new_product": {
                    "name": "Молоко 3.2% обезжиренное",
                    "base_unit": "ml",
                    "calories": 35,
                    "proteins": 3,
                    "fats": 1,
                    "carbs": 4.7,
                },
            },
        )

        self.assertEqual(output["proposal"]["new_product"]["name"], "Молоко 3.2% обезжиренное")
        self.assertEqual([item["name"] for item in output["similar"]], ["Молоко 3.2%"])
        self.assertEqual(list(Product.objects.values_list("pk", "name", "calories")), before)

    def test_entry_proposal_rejects_bad_amounts_dates_meals_and_products(self):
        good_new = {
            "name": "Печенье",
            "base_unit": "g",
            "calories": 420,
            "proteins": 7.5,
            "fats": 16,
            "carbs": 63,
        }
        base = {"amount": 150, "date": "2026-10-31", "meal_type": "lunch"}
        cases = [
            {"product_id": 999999, **base},
            {**base, "product_id": self.product.pk, "amount": "0"},
            {**base, "product_id": self.product.pk, "amount": "abc"},
            {**base, "product_id": self.product.pk, "amount": None},
            {**base, "product_id": self.product.pk, "date": "31.10.2026"},
            {**base, "product_id": self.product.pk, "meal_type": "supper"},
            {**base, "product_id": self.product.pk, "new_product": good_new},
            {**base},
            {**base, "new_product": {**good_new, "base_unit": "kg"}},
            {**base, "new_product": {**good_new, "calories": -1}},
            {**base, "new_product": {"name": "Без КБЖУ"}},
            {**base, "new_product": {**good_new, "name": "   "}},
            {**base, "new_product": {**good_new, "name": "Я" * 300}},
        ]

        for arguments in cases:
            with self.subTest(arguments=arguments):
                _, output, _ = self.run_tool("propose_entry", arguments)
                self.assertIn("error", output)
                self.assertEqual(Proposal.objects.count(), 0)

    def test_product_proposal_from_a_turn_has_no_entry_and_similar_is_limited(self):
        Product.objects.bulk_create(
            Product(
                name=f"Похожий продукт {index}",
                base_unit="g",
                calories=Decimal("100"),
                proteins=Decimal("1"),
                fats=Decimal("1"),
                carbs=Decimal("1"),
                author=self.member,
            )
            for index in range(6)
        )
        _, output, _ = self.run_tool(
            "propose_product",
            {
                "new_product": {
                    "name": "Похожий продукт",
                    "base_unit": "g",
                    "calories": 100,
                    "proteins": 1,
                    "fats": 1,
                    "carbs": 1,
                }
            },
        )

        self.assertEqual(output["proposal"]["kind"], Proposal.Kind.PRODUCT)
        self.assertNotIn("date", output["proposal"])
        self.assertEqual(len(output["similar"]), 5)
        proposal = Proposal.objects.get()
        self.assertIsNone(proposal.date)
        self.assertIsNone(proposal.amount)
        self.assertEqual(DiaryEntry.objects.count(), 0)

        page = self.client.get(reverse("assistant:proposals"))
        self.assertContains(page, "новый Продукт")
        self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))
        product = Product.objects.get(name="Похожий продукт")
        self.assertEqual(product.author, self.member)
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_product_proposal_requires_full_new_product_data(self):
        _, output, _ = self.run_tool("propose_product", {"new_product": {"name": "X"}})

        self.assertIn("error", output)
        self.assertEqual(Proposal.objects.count(), 0)

    def test_huge_numbers_are_domain_errors_not_crashes(self):
        _, entry, _ = self.run_tool(
            "propose_entry",
            {
                "product_id": self.product.pk,
                "amount": "1e1000",
                "date": "2026-10-31",
                "meal_type": "lunch",
            },
        )
        self.assertIn("error", entry)

        _, product, _ = self.run_tool(
            "propose_product",
            {
                "new_product": {
                    "name": "Огромный продукт",
                    "base_unit": "g",
                    "calories": "1e1000",
                    "proteins": 1,
                    "fats": 1,
                    "carbs": 1,
                }
            },
        )
        self.assertIn("error", product)
        self.assertEqual(Proposal.objects.count(), 0)

    def test_tool_errors_are_returned_to_the_model_without_failing_the_turn(self):
        _, unknown, script = self.run_tool("delete_everything", {})
        self.assertIn("error", unknown)
        self.assertEqual(len(script.calls), 2)

        _, malformed, _ = self.run_tool("read_diary", "{oops")
        self.assertIn("error", malformed)

        _, not_object, _ = self.run_tool("read_diary", "[]")
        self.assertIn("error", not_object)

        _, injected, _ = self.run_tool(
            "read_diary", {"member": self.member.pk, "date": "2026-10-31"}
        )
        self.assertIn("Неверные аргументы", injected["error"])

        _, missing, _ = self.run_tool("propose_product", {})
        self.assertIn("Неверные аргументы", missing["error"])
