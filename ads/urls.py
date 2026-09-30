from django.urls import path

from . import views

# Mounted under "api/" in config/urls.py, so "ads/" here is /api/ads/.
urlpatterns = [
    path("ads/", views.ads_collection),  # GET = list, POST = create
    # <int:ad_id> grabs the number from the URL and passes it to get_ad as ad_id.
    # /api/ads/1/  ->  get_ad(request, ad_id=1)
    path("ads/<int:ad_id>/", views.get_ad),
]
