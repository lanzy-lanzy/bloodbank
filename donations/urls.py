from django.urls import path

from donations import views

app_name = "donations"

urlpatterns = [
    path("", views.DonationListView.as_view(), name="list"),
    path("create/", views.DonationCreateView.as_view(), name="create"),
    path("my/", views.MyDonationHistoryView.as_view(), name="my_history"),
    path("<int:pk>/", views.DonationDetailView.as_view(), name="detail"),
    path("<int:pk>/transition/", views.DonationTransitionView.as_view(), name="transition"),
    path("<int:pk>/collect/", views.DonationCollectView.as_view(), name="collect"),
]
