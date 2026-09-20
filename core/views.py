from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import ProductCreateForm, ProductEditForm
from core.models import Product


@login_required
def day(request: HttpRequest) -> HttpResponse:
    """Render the signed-in member's personal day page."""
    return render(request, "core/day.html")


def healthz(request: HttpRequest) -> HttpResponse:
    """Report that the application process is up."""
    return HttpResponse("ok")


@login_required
def product_list(request: HttpRequest) -> HttpResponse:
    """Render the shared product catalog, optionally filtered by name."""
    query = request.GET.get("q", "").strip()
    all_products = Product.objects.select_related("author")
    products: list[Product] = list(all_products)
    if query:
        needle = query.lower()
        products = [product for product in products if needle in product.name.lower()]
    return render(request, "core/product_list.html", {"products": products, "query": query})


@login_required
def product_create(request: HttpRequest) -> HttpResponse:
    """Create a new catalog product authored by the signed-in member."""
    if request.method == "POST":
        form = ProductCreateForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.author = request.user
            product.save()
            messages.success(request, "Продукт создан")
            return redirect("core:product_list")
    else:
        form = ProductCreateForm()
    return render(request, "core/product_form.html", {"form": form})


@login_required
def product_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit any catalog product; the base unit stays fixed."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductEditForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "Продукт обновлён")
            return redirect("core:product_list")
    else:
        form = ProductEditForm(instance=product)
    return render(request, "core/product_form.html", {"form": form, "product": product})


@login_required
def product_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a catalog product, unless a diary entry still references it."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        try:
            product.delete()
        except ProtectedError:
            messages.error(
                request,
                "Продукт нельзя удалить: на него ссылаются записи дневника",
            )
        else:
            messages.success(request, "Продукт удалён")
        return redirect("core:product_list")
    return render(request, "core/product_confirm_delete.html", {"product": product})
