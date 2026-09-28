from django.urls import path

from requests import views

app_name = "requests"

urlpatterns = [
    path("", views.RequestListView.as_view(), name="list"),
    path("create/", views.RequestCreateView.as_view(), name="create"),
    path("organizations/", views.OrganizationListView.as_view(), name="organization_list"),
    path("organizations/create/", views.OrganizationFormView.as_view(), name="organization_create"),
    path("organizations/<int:pk>/edit/", views.OrganizationFormView.as_view(), name="organization_edit"),
    path("badge/", views.RequestBadgeView.as_view(), name="badge"),
    path("walk-ins/", views.WalkInDeskView.as_view(), name="walk_in_desk"),
    path("walk-ins/badge/", views.WalkInBadgeView.as_view(), name="walk_in_badge"),
    path("<int:pk>/", views.RequestDetailView.as_view(), name="detail"),
    path("<int:pk>/action/", views.RequestActionView.as_view(), name="action"),
    path("<int:pk>/allocations/<int:allocation_pk>/feedback/",
         views.RequesterFulfillmentView.as_view(), name="allocation_feedback"),
]
