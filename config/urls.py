from django.urls import include, path

# Django serves only the API. The pages are the React app (frontend/, :5173).
urlpatterns = [
    path("api/", include("ads.urls")),   # the JSON API
    path("api/", include("tracking.urls")),  # your applications (logged in)
    # Day 11: GET /metrics — every metric of THIS process, in Prometheus' text
    # format. Prometheus scrapes it every 15s. (Fine locally; in production it
    # shouldn't be open to the internet.)
    path("", include("django_prometheus.urls")),
]
