from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("random-quote/", views.random_quote, name="random_quote"),
]