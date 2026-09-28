from django.urls import path  # noqa: F401  (used once ads/urls.py exists)

# Step 1: `docker compose up` with this empty list and confirm the stack boots.
# Step 2: create ads/urls.py, then replace the list below with:
#
#     from django.urls import include
#     urlpatterns = [path("api/", include("ads.urls"))]
urlpatterns = []
