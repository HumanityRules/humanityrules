"""Models for the notes application."""
from django.conf import settings
from django.db import models


class Note(models.Model):
    """A simple note with title and content."""

    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notes',
    )

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title
