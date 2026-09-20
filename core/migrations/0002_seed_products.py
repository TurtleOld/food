from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations

SYSTEM_USERNAME = "system"

SEED_PRODUCTS = [
    ("Куриная грудка", "g", "165.0", "31.0", "3.6", "0.0"),
    ("Куриное яйцо", "g", "157.0", "12.7", "10.9", "0.7"),
    ("Говядина", "g", "250.0", "26.0", "16.0", "0.0"),
    ("Лосось", "g", "208.0", "20.0", "13.0", "0.0"),
    ("Творог 5%", "g", "121.0", "17.2", "5.0", "1.8"),
    ("Молоко 3.2%", "ml", "60.0", "3.0", "3.2", "4.7"),
    ("Йогурт натуральный", "g", "66.0", "5.0", "3.2", "3.5"),
    ("Сыр твёрдый", "g", "364.0", "26.0", "27.0", "0.0"),
    ("Рис белый варёный", "g", "130.0", "2.7", "0.3", "28.0"),
    ("Гречка варёная", "g", "110.0", "4.2", "1.1", "21.0"),
    ("Овсянка на воде", "g", "88.0", "3.0", "1.7", "15.0"),
    ("Макароны варёные", "g", "131.0", "5.0", "1.1", "25.0"),
    ("Хлеб пшеничный", "g", "265.0", "8.1", "3.2", "48.8"),
    ("Картофель варёный", "g", "82.0", "2.0", "0.4", "16.7"),
    ("Банан", "g", "89.0", "1.1", "0.3", "22.8"),
    ("Яблоко", "g", "52.0", "0.3", "0.2", "13.8"),
    ("Огурец", "g", "15.0", "0.7", "0.1", "3.6"),
    ("Помидор", "g", "18.0", "0.9", "0.2", "3.9"),
    ("Оливковое масло", "ml", "884.0", "0.0", "100.0", "0.0"),
    ("Мёд", "g", "304.0", "0.3", "0.0", "82.4"),
    ("Миндаль", "g", "579.0", "21.2", "49.9", "21.6"),
]


def seed_products(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Product = apps.get_model("core", "Product")

    system_user, _ = User.objects.get_or_create(
        username=SYSTEM_USERNAME,
        defaults={
            "is_active": False,
            "password": make_password(None),
        },
    )

    for name, base_unit, calories, proteins, fats, carbs in SEED_PRODUCTS:
        Product.objects.get_or_create(
            name=name,
            defaults={
                "base_unit": base_unit,
                "calories": Decimal(calories),
                "proteins": Decimal(proteins),
                "fats": Decimal(fats),
                "carbs": Decimal(carbs),
                "author": system_user,
            },
        )


def unseed_products(apps, schema_editor):
    Product = apps.get_model("core", "Product")
    Product.objects.filter(name__in=[name for name, *_ in SEED_PRODUCTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_products, unseed_products),
    ]
