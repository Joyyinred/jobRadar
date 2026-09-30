from django.urls import include, path

from ads import views as ads_views

urlpatterns = [
    path("", ads_views.index),           # the HTML page
    path("api/", include("ads.urls")),   # the JSON API
]
