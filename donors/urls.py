from django.urls import path

from donors import views

app_name = "donors"

urlpatterns = [
    path("", views.DonorListView.as_view(), name="list"),
    path("create/", views.DonorCreateView.as_view(), name="create"),
    path("my/", views.MyDonorProfileView.as_view(), name="my_profile"),
    path("my/edit/", views.MyDonorProfileEditView.as_view(), name="my_profile_edit"),
    path("my/interview-sheet/", views.MyInterviewSheetView.as_view(), name="my_interview_sheet"),
    path("my/interview-sheet/preview/", views.MyInterviewSheetDocumentView.as_view(),
         name="my_interview_sheet_document"),
    path("my/interview-sheet/pdf/", views.MyInterviewSheetPdfView.as_view(),
         name="my_interview_sheet_pdf"),
    path("<int:pk>/", views.DonorDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DonorUpdateView.as_view(), name="edit"),
    path("<int:pk>/status/", views.DonorStatusView.as_view(), name="status"),
    path("<int:pk>/delete/", views.DonorDeleteView.as_view(), name="delete"),
    path("<int:pk>/interview-sheet/", views.InterviewSheetView.as_view(), name="interview_sheet"),
    path("<int:pk>/interview-sheet/preview/", views.InterviewSheetDocumentView.as_view(),
         name="interview_sheet_document"),
    path("<int:pk>/interview-sheet/pdf/", views.InterviewSheetPdfView.as_view(),
         name="interview_sheet_pdf"),
    path("<int:pk>/screenings/create/", views.ScreeningCreateView.as_view(), name="screening_create"),
    path("screenings/<int:pk>/", views.ScreeningDetailView.as_view(), name="screening_detail"),
]
