from django.urls import include, path

from ads import views as ads_views

urlpatterns = [
    path("", ads_views.index),           # the HTML page
    path("api/", include("ads.urls")),   # the JSON API
    # Day 11: GET /metrics — every metric of THIS process, in Prometheus' text
    # format. Prometheus scrapes it every 15s. (Fine locally; in production it
    # shouldn't be open to the internet.)
    path("", include("django_prometheus.urls")),
]
