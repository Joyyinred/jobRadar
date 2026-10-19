"""
Pages for GET /api/ads/: 20 ads at a time instead of every ad at once.

PageNumberPagination (?page=2), not cursor pagination: you can jump straight
to "page 5 of 10" and see how many there are — what a list you browse needs.
Its weak spot, an ad arriving while you page (everything shifts by one),
matters for a fast feed, not for a list that grows by a few imports a day.

Response:
    {"count": 191, "page": 2, "pages": 10, "page_size": 20,
     "next": ".../?page=3", "previous": ".../?page=1", "results": [...]}
"page" / "pages" are added to DRF's usual count/next/previous, so the page
doesn't have to work out the number of pages itself. A page past the end
(?page=99) is a 404 in the usual error format (exceptions.exception_handler).
"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class AdPagination(PageNumberPagination):
    page_size = 20

    def get_paginated_response(self, data):
        return Response({
            "count": self.page.paginator.count,
            "page": self.page.number,
            "pages": self.page.paginator.num_pages,
            "page_size": self.page_size,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })
