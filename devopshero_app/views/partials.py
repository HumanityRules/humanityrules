import random
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def random_quote(request):
    """Returns a partial HTML snippet with a random quote - for HTMX demo"""
    quotes = [
        ("The only way to do great work is to love what you do.", "Your mama"),
        ("Infrastructure as code is the foundation of modern DevOps.", "Anonymous"),
        ("Automate everything you can, so you can focus on what matters.", "DevOps Wisdom"),
        ("Fail fast, learn faster.", "UI/UX Engineer"),
        ("There is no cloud, it's just someone else's computer.", "Unknown"),
    ]
    quote, author = random.choice(quotes)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    return render(request, "devopshero_app/partials/quote.html", {
        "quote": quote,
        "author": author,
        "timestamp": timestamp,
    })

