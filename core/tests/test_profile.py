import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DailyTarget, Profile, WeightMeasurement

User = get_user_model()


def _years_ago(years: int) -> datetime.date:
    return datetime.date(datetime.date.today().year - years, 1, 1)


def _birthday_years_ago(years: int) -> datetime.date:
    today = datetime.date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return datetime.date(today.year - years, 2, 28)


class ProfilePageTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:profile")

    def test_page_requires_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(response, f"/login/?next={self.url}")

    def test_navigation_links_to_profile(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, "Профиль")

    def test_page_offers_a_manual_target_while_profile_is_empty(self):
        response = self.client.get(self.url)

        self.assertContains(response, reverse("core:daily_target_edit"))
        self.assertContains(response, "Задать цель вручную")
        self.assertNotContains(response, "Принять цель")

    def test_page_explains_what_the_calculation_needs(self):
        response = self.client.get(self.url)

        self.assertContains(response, "Для расчёта нужны")

    def test_replace_note_appears_only_when_there_is_a_suggestion(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(self.url)

        self.assertContains(response, "Текущая цель")
        self.assertNotContains(response, "заменит")

    def test_page_shows_only_the_current_members_data(self):
        Profile.objects.create(member=User.objects.create_user(username="bob"), height_cm=199)

        response = self.client.get(self.url)

        self.assertNotContains(response, "199")


class ProfileFormTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:profile")

    def test_partial_profile_is_saved(self):
        response = self.client.post(self.url, {"height_cm": "180"}, follow=True)

        self.assertRedirects(response, self.url)
        profile = Profile.objects.get(member=self.member)
        self.assertEqual(profile.height_cm, 180)
        self.assertIsNone(profile.birth_date)
        self.assertEqual(profile.sex, "")
        self.assertEqual(profile.activity, "")
        self.assertContains(response, "Профиль сохранён")

    def test_profile_is_updated_in_place(self):
        self.client.post(self.url, {"height_cm": "180"})
        self.client.post(self.url, {"height_cm": "181"})

        self.assertEqual(Profile.objects.filter(member=self.member).count(), 1)
        self.assertEqual(Profile.objects.get(member=self.member).height_cm, 181)

    def test_unrealistic_height_is_rejected(self):
        for height in ("49", "251"):
            with self.subTest(height=height):
                response = self.client.post(self.url, {"height_cm": height})

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Рост")
                self.assertFalse(Profile.objects.exists())

    def test_non_numeric_height_is_rejected(self):
        response = self.client.post(self.url, {"height_cm": "высокий"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "целое число")
        self.assertFalse(Profile.objects.exists())

    def test_future_birth_date_is_rejected(self):
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)

        response = self.client.post(self.url, {"birth_date": tomorrow.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "будущем")
        self.assertFalse(Profile.objects.exists())

    def test_birth_date_before_1900_is_rejected(self):
        response = self.client.post(self.url, {"birth_date": "1899-12-31"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1900")
        self.assertFalse(Profile.objects.exists())

    def test_profile_of_another_member_is_untouched(self):
        other = User.objects.create_user(username="bob")

        self.client.post(self.url, {"height_cm": "180"})

        self.assertFalse(Profile.objects.filter(member=other).exists())


class WeightMeasurementTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:profile")

    def _post(self, date, weight):
        return self.client.post(self.url, {"date": date, "weight_kg": weight})

    def test_weight_is_saved_for_the_current_member(self):
        today = datetime.date.today()

        response = self._post(today.isoformat(), "80.5")

        self.assertRedirects(response, self.url)
        measurement = WeightMeasurement.objects.get()
        self.assertEqual(measurement.member, self.member)
        self.assertEqual(measurement.date, today)
        self.assertEqual(measurement.weight_kg, Decimal("80.5"))

    def test_future_weight_date_is_rejected(self):
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)

        response = self._post(tomorrow.isoformat(), "80.5")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "будущем")
        self.assertFalse(WeightMeasurement.objects.exists())

    def test_invalid_weight_is_rejected(self):
        for weight in ("NaN", "Infinity", "-Infinity", "abc", ""):
            with self.subTest(weight=weight):
                response = self._post(datetime.date.today().isoformat(), weight)

                self.assertEqual(response.status_code, 200)
                self.assertFalse(WeightMeasurement.objects.exists())

    def test_unrealistic_weight_is_rejected(self):
        for weight in ("19.9", "400.1"):
            with self.subTest(weight=weight):
                response = self._post(datetime.date.today().isoformat(), weight)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Вес")
                self.assertFalse(WeightMeasurement.objects.exists())

    def test_invalid_weight_keeps_the_saved_profile_on_screen(self):
        self.client.post(self.url, {"height_cm": "180"})

        response = self._post(datetime.date.today().isoformat(), "NaN")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="180"')
        self.assertNotContains(response, 'height_cm" value=""')

    def test_new_weight_does_not_change_the_daily_target(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        self._post(datetime.date.today().isoformat(), "95.0")

        target = DailyTarget.objects.get(member=self.member)
        self.assertEqual(target.calories, Decimal("2000.0"))
        self.assertEqual(target.proteins, Decimal("120.0"))

    def test_measurements_are_listed_newest_first(self):
        older = datetime.date.today() - datetime.timedelta(days=10)
        self._post(older.isoformat(), "80.0")
        self._post(datetime.date.today().isoformat(), "90.0")

        response = self.client.get(self.url)

        content = response.content.decode()
        self.assertLess(content.index("90.0"), content.index("80.0"))

    def test_other_members_weight_is_not_shown(self):
        WeightMeasurement.objects.create(
            member=User.objects.create_user(username="bob"),
            date=datetime.date.today(),
            weight_kg=Decimal("123.4"),
        )

        response = self.client.get(self.url)

        self.assertNotContains(response, "123.4")

    def test_bmi_uses_the_latest_measurement(self):
        self.client.post(self.url, {"height_cm": "180"})
        older = datetime.date.today() - datetime.timedelta(days=10)
        self._post(older.isoformat(), "80.0")
        self._post(datetime.date.today().isoformat(), "90.0")

        response = self.client.get(self.url)

        self.assertContains(response, "ИМТ")
        self.assertContains(response, "27.8")
        self.assertNotContains(response, "24.7")


class TargetSuggestionTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:profile")

    def _complete_profile(self, sex="male", activity="sedentary", years=30):
        return self.client.post(
            self.url,
            {
                "height_cm": "180",
                "birth_date": _years_ago(years).isoformat(),
                "sex": sex,
                "activity": activity,
            },
        )

    def _add_weight(self, weight="80.0"):
        return self.client.post(
            self.url,
            {"date": datetime.date.today().isoformat(), "weight_kg": weight},
        )

    def test_complete_adult_profile_suggests_maintenance_and_macros(self):
        self._complete_profile()
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "2492")
        self.assertContains(response, "124.6")
        self.assertContains(response, "83.1")
        self.assertContains(response, "311.5")

    def test_female_formula_uses_the_female_constant(self):
        self._complete_profile(sex="female")
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "2260")

    def test_activity_factor_is_applied(self):
        self._complete_profile(activity="high")
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "3560")

    def test_calculation_is_hidden_for_a_minor(self):
        self._complete_profile(years=17)
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "совершеннолетним")
        self.assertNotContains(response, "Принять цель")

    def test_adult_on_the_eighteenth_birthday_is_eligible(self):
        birthday = _birthday_years_ago(18)
        self.client.post(
            self.url,
            {
                "height_cm": "180",
                "birth_date": birthday.isoformat(),
                "sex": "male",
                "activity": "sedentary",
            },
        )
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "Принять цель")

    def test_day_before_the_eighteenth_birthday_is_not_adult(self):
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        try:
            birthday = tomorrow.replace(year=tomorrow.year - 18)
        except ValueError:
            birthday = datetime.date(tomorrow.year - 18, 2, 28)
        self.client.post(
            self.url,
            {
                "height_cm": "180",
                "birth_date": birthday.isoformat(),
                "sex": "male",
                "activity": "sedentary",
            },
        )
        self._add_weight()

        response = self.client.get(self.url)

        self.assertContains(response, "совершеннолетним")
        self.assertNotContains(response, "Принять цель")

    def test_calculation_needs_each_profile_field(self):
        complete = {
            "height_cm": "180",
            "birth_date": _years_ago(30).isoformat(),
            "sex": "male",
            "activity": "sedentary",
        }
        for missing in complete:
            with self.subTest(missing=missing):
                Profile.objects.all().delete()
                WeightMeasurement.objects.all().delete()
                self.client.post(
                    self.url, {key: value for key, value in complete.items() if key != missing}
                )
                self._add_weight()

                response = self.client.get(self.url)

                self.assertContains(response, "Для расчёта нужны")
                self.assertNotContains(response, "Принять цель")

    def test_calculation_needs_a_weight(self):
        self._complete_profile()

        response = self.client.get(self.url)

        self.assertContains(response, "Для расчёта нужны")
        self.assertNotContains(response, "Принять цель")

    def test_negative_adjustment_lowers_the_suggestion(self):
        self._complete_profile()
        self._add_weight()

        response = self.client.get(self.url, {"adjustment": "-400"})

        self.assertContains(response, "2092")
        self.assertContains(response, "104.6")

    def test_positive_adjustment_raises_the_suggestion(self):
        self._complete_profile()
        self._add_weight()

        response = self.client.get(self.url, {"adjustment": "300"})

        self.assertContains(response, "2792")

    def test_invalid_adjustment_is_explained_without_a_server_error(self):
        self._complete_profile()
        self._add_weight()

        response = self.client.get(self.url, {"adjustment": "NaN"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Поправка")
        self.assertContains(response, "2492")

    def test_out_of_range_adjustment_is_explained(self):
        self._complete_profile()
        self._add_weight()

        response = self.client.get(self.url, {"adjustment": "5000"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Поправка")
        self.assertContains(response, "2492")

    def test_adjustment_cannot_leave_zero_calories(self):
        self.client.post(
            self.url,
            {
                "height_cm": "50",
                "birth_date": _years_ago(30).isoformat(),
                "sex": "female",
                "activity": "sedentary",
            },
        )
        self._add_weight("20.0")

        response = self.client.get(self.url, {"adjustment": "-1000"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Поправка слишком велика")
        self.assertNotContains(response, "Принять цель")

    def test_values_can_be_edited_and_accepted_into_the_target(self):
        self._complete_profile()
        self._add_weight()

        page = self.client.get(self.url)
        self.assertContains(page, "Проверьте")
        self.assertContains(page, f'action="{reverse("core:daily_target_edit")}"')

        response = self.client.post(
            reverse("core:daily_target_edit"),
            {"calories": "2100", "proteins": "120", "fats": "70", "carbs": "230"},
        )

        self.assertRedirects(response, reverse("core:day"))
        target = DailyTarget.objects.get(member=self.member)
        self.assertEqual(target.calories, Decimal("2100.0"))
        self.assertEqual(target.proteins, Decimal("120.0"))

    def test_accepting_does_not_happen_without_a_submit(self):
        self._complete_profile()
        self._add_weight()

        self.client.get(self.url)

        self.assertFalse(DailyTarget.objects.exists())

    def test_existing_target_is_shown_next_to_the_suggestion(self):
        self._complete_profile()
        self._add_weight()
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(self.url)

        self.assertContains(response, "Текущая цель")
        self.assertContains(response, "2000.0")
        self.assertContains(response, "заменит")
