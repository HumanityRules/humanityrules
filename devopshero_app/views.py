from django.shortcuts import render


def index(request):
    return render(request, "devopshero_app/index.html")

