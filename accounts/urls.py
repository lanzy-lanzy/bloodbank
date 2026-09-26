from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.urls import path

from accounts import views
from accounts.models import User

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("profile/", views.profile, name="profile"),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/create/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_update"),
    path("users/<int:pk>/toggle-lock/", views.UserLockToggleView.as_view(), name="user_toggle_lock"),
    path("registrations/", views.RegistrationListView.as_view(), name="registration_list"),
    path("registrations/<int:pk>/", views.RegistrationReviewView.as_view(), name="registration_review"),
]


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "full_name", "role", "is_active", "is_locked")
    list_filter = ("role", "is_active", "is_locked")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Blood Bank Role", {"fields": ("role", "phone", "middle_name", "is_locked", "locked_until", "deactivated_reason")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Blood Bank Role", {"fields": ("role",)}),
    )
