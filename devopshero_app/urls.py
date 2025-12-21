from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("team/", views.team, name="team"),
    path("random-quote/", views.random_quote, name="random_quote"),
]