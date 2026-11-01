import json
from decimal import Decimal
from unittest import mock
from urllib.error import URLError

from django.test import TestCase

from assistant.budget import BudgetUnavailable, month_summary
from assistant.gateway import LLMGatewayError, complete
from assistant.models import BudgetReservation, LLMSettings, UsageRecord

URLOPEN = "assistant.gateway.urlopen"


def configure(**overrides):
    values = {
        "base_url": "https://llm.test/v1",
        "model": "deepseek-flash",
        "api_key": "sk-live-secret-1234",
        "monthly_budget_rub": Decimal("1000.00"),
        "input_price_rub": Decimal("100.00"),
        "cached_price_rub": Decimal("20.00"),
        "output_price_rub": Decimal("300.00"),
        "max_input_tokens": 12000,
        "max_output_tokens": 1000,
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return LLMSettings.objects.create(**values)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(text="Привет", prompt=1000, completion=500, cached=200):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


class RefusalTests(TestCase):
    def test_unconfigured_assistant_never_calls_the_provider(self):
        with mock.patch(URLOPEN) as urlopen:
            with self.assertRaisesMessage(BudgetUnavailable, "не настроен"):
                complete([{"role": "user", "content": "Привет"}])

        urlopen.assert_not_called()

    def test_exhausted_budget_never_calls_the_provider(self):
        configure(monthly_budget_rub=Decimal("1.00"))
        with mock.patch(URLOPEN) as urlopen:
            with self.assertRaisesMessage(BudgetUnavailable, "исчерпан"):
                complete([{"role": "user", "content": "Привет"}])

        urlopen.assert_not_called()
        self.assertEqual(month_summary().spent, Decimal("0"))

    def test_too_large_input_is_refused_before_the_provider(self):
        configure(max_input_tokens=10)
        with mock.patch(URLOPEN) as urlopen:
            with self.assertRaisesMessage(BudgetUnavailable, "слишком"):
                complete([{"role": "user", "content": "а" * 1000}])

        urlopen.assert_not_called()


class RequestTests(TestCase):
    def test_text_request_carries_model_limits_and_key_then_records_usage(self):
        configure()
        with mock.patch(URLOPEN, return_value=FakeResponse(answer())) as urlopen:
            result = complete([{"role": "user", "content": "Привет"}])

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://llm.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-live-secret-1234")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["max_tokens"], 1000)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Привет"}])
        self.assertNotIn("tools", payload)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)
        self.assertEqual(result.text, "Привет")
        self.assertEqual(result.usage.input_tokens, 1000)
        self.assertEqual(result.usage.cached_input_tokens, 200)
        self.assertEqual(result.usage.output_tokens, 500)
        self.assertEqual(result.cost_rub, Decimal("0.234000"))
        self.assertEqual(result.tool_calls, ())
        usage = UsageRecord.objects.get()
        self.assertEqual(usage.cost_rub, Decimal("0.234000"))
        self.assertEqual(month_summary().spent, Decimal("0.234000"))

    def test_reservation_exists_while_the_request_is_in_flight(self):
        configure()

        def provider(request, timeout):
            self.assertEqual(
                BudgetReservation.objects.filter(status=BudgetReservation.Status.PENDING).count(),
                1,
            )
            return FakeResponse(answer())

        with mock.patch(URLOPEN, side_effect=provider):
            complete([{"role": "user", "content": "Привет"}])

        self.assertEqual(
            BudgetReservation.objects.filter(status=BudgetReservation.Status.PENDING).count(), 0
        )

    def test_vision_and_tools_pass_through_and_tool_calls_come_back(self):
        configure()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Что на фото?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,AAAA"},
                    },
                ],
            }
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_catalog",
                    "description": "Ищет продукт",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        reply = answer(text=None)
        reply["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_catalog", "arguments": '{"query": "кефир"}'},
            }
        ]
        with mock.patch(URLOPEN, return_value=FakeResponse(reply)) as urlopen:
            result = complete(messages, tools=tools)

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(result.text, "")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].id, "call-1")
        self.assertEqual(result.tool_calls[0].name, "search_catalog")
        self.assertEqual(result.tool_calls[0].arguments, '{"query": "кефир"}')


class FailureTests(TestCase):
    def setUp(self):
        configure()

    def assert_released(self):
        self.assertEqual(UsageRecord.objects.count(), 0)
        self.assertEqual(
            BudgetReservation.objects.filter(status=BudgetReservation.Status.PENDING).count(), 0
        )
        self.assertEqual(
            BudgetReservation.objects.filter(status=BudgetReservation.Status.RELEASED).count(), 1
        )
        self.assertEqual(month_summary().spent, Decimal("0"))

    def test_provider_failure_releases_the_reservation(self):
        with mock.patch(URLOPEN, side_effect=URLError("down")):
            with self.assertRaisesMessage(LLMGatewayError, "не ответил"):
                complete([{"role": "user", "content": "Привет"}])

        self.assert_released()

    def test_malformed_response_without_usage_releases_the_reservation(self):
        broken = {"choices": [{"message": {"content": "Привет"}}]}
        with mock.patch(URLOPEN, return_value=FakeResponse(broken)):
            with self.assertRaises(LLMGatewayError):
                complete([{"role": "user", "content": "Привет"}])

        self.assert_released()

    def test_http_error_releases_the_reservation(self):
        with mock.patch(URLOPEN, side_effect=OSError("boom")):
            with self.assertRaises(LLMGatewayError):
                complete([{"role": "user", "content": "Привет"}])

        self.assert_released()
