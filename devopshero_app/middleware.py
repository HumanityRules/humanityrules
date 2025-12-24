from django.http import Http404
from django.shortcuts import render


class HtmxErrorMiddleware:
    """
    Middleware to handle error responses for HTMX requests.
    
    When an HTMX request results in an error (like 404), this middleware
    intercepts the response and returns a user-friendly partial template
    instead of the full Django debug page.
    
    This prevents the page from being corrupted when navigating to non-existent
    pages via HTMX, and allows the browser back button to work correctly.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Only handle HTMX requests (use django-htmx's request.htmx)
        if not getattr(request, 'htmx', None):
            return response
        
        # Check for error status codes
        if response.status_code >= 400:
            return self._create_htmx_error_response(request, response.status_code)
        
        return response
    
    def process_exception(self, request, exception):
        """
        Handle exceptions for HTMX requests before Django's error handlers.
        This is especially important for DEBUG=True mode where Django shows debug pages.
        """
        # Only handle HTMX requests
        if not getattr(request, 'htmx', None):
            return None  # Let Django handle it
        
        # Handle 404 errors
        if isinstance(exception, Http404):
            return self._create_htmx_error_response(request, 404)
        
        # For other exceptions, return a 500 error page
        return self._create_htmx_error_response(request, 500)
    
    def _create_htmx_error_response(self, request, status_code):
        """Create a proper HTMX-compatible error response."""
        error_context = self._get_error_context(status_code)
        error_response = render(
            request, 
            "devopshero_app/partials/_error.html", 
            error_context
        )
        # Return 200 status for HTMX requests to ensure proper swap behavior
        # The error content itself informs the user about the actual error
        error_response.status_code = 200
        # Tell HTMX to retarget to #main-content
        error_response['HX-Retarget'] = '#main-content'
        error_response['HX-Reswap'] = 'innerHTML'
        return error_response
    
    def _get_error_context(self, status_code):
        """Return appropriate error message based on status code."""
        error_messages = {
            400: ("400 - Bad Request", "The request could not be understood."),
            401: ("401 - Unauthorized", "You need to log in to access this page."),
            403: ("403 - Forbidden", "You don't have permission to access this page."),
            404: ("404 - Page Not Found", "The page you're looking for doesn't exist."),
            500: ("500 - Server Error", "Something went wrong on our end."),
        }
        
        error_code, error_message = error_messages.get(
            status_code, 
            (f"{status_code} - Error", "An unexpected error occurred.")
        )
        
        return {
            "error_code": error_code,
            "error_message": error_message,
        }

