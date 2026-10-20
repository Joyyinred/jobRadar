from django.urls import path

from . import views

# Mounted under "api/" in config/urls.py, next to the ads URLs.
urlpatterns = [
    path("applications/", views.list_applications),             # GET: yours, + summary
    path("ads/<int:ad_id>/application/", views.ad_application),  # GET / PUT / DELETE: yours for this ad
]
