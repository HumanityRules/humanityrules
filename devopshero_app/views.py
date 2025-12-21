from django.shortcuts import render
import random
from datetime import datetime


def index(request):
    return render(request, "devopshero_app/index.html")


def random_quote(request):
    """Returns a partial HTML snippet with a random quote - for HTMX demo"""
    quotes = [
        ("The only way to do great work is to love what you do.", "Steve Jobs"),
        ("Infrastructure as code is the foundation of modern DevOps.", "Anonymous"),
        ("Automate everything you can, so you can focus on what matters.", "DevOps Wisdom"),
        ("Fail fast, learn faster.", "Silicon Valley Proverb"),
        ("There is no cloud, it's just someone else's computer.", "Unknown"),
    ]
    quote, author = random.choice(quotes)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    return render(request, "devopshero_app/partials/quote.html", {
        "quote": quote,
        "author": author,
        "timestamp": timestamp,
    })

