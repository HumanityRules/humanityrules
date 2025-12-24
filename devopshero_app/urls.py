from django.urls import path
from . import views
from . import auth_views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("workspaces/", views.workspaces, name="workspaces"),
    path("random-quote/", views.random_quote, name="random_quote"),
    
    # Authentication
    path("auth/login/", auth_views.auth_login, name="login"),
    path("auth/callback/", auth_views.auth_callback, name="auth_callback"),
    path("auth/logout/", auth_views.auth_logout, name="logout"),
]