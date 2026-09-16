from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def day(request: HttpRequest) -> HttpResponse:
    """Render the signed-in member's personal day page."""
    return render(request, "core/day.html")


def healthz(request: HttpRequest) -> HttpResponse:
    """Report that the application process is up."""
    return HttpResponse("ok")
