from django.urls import path

from donors import views

app_name = "donors"

urlpatterns = [
    path("", views.DonorListView.as_view(), name="list"),
    path("create/", views.DonorCreateView.as_view(), name="create"),
    path("my/", views.MyDonorProfileView.as_view(), name="my_profile"),
    path("my/edit/", views.MyDonorProfileEditView.as_view(), name="my_profile_edit"),
    path("<int:pk>/", views.DonorDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DonorUpdateView.as_view(), name="edit"),
    path("<int:pk>/status/", views.DonorStatusView.as_view(), name="status"),
    path("<int:pk>/delete/", views.DonorDeleteView.as_view(), name="delete"),
    path("<int:pk>/screenings/create/", views.ScreeningCreateView.as_view(), name="screening_create"),
    path("screenings/<int:pk>/", views.ScreeningDetailView.as_view(), name="screening_detail"),
]
