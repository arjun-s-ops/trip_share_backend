# api/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Follower, GroupDetails, Post, Notification
from django.contrib.contenttypes.models import ContentType

# 1. When someone follows you
@receiver(post_save, sender=Follower)
def create_follow_notification(sender, instance, created, **kwargs):
    if created:
        # instance.follower follows instance.following
        Notification.objects.create(
            recipient=instance.following,
            actor=instance.follower,
            verb='started following you',
            target=None,   # you could link to the follow object itself if needed
        )

# 2. When someone joins a trip you admin (GroupDetails creation/update)
@receiver(post_save, sender=GroupDetails)
def create_join_notification(sender, instance, created, **kwargs):
    # This signal fires when a group is created or updated.
    # We want to notify the admin when a new member joins.
    # However, the group is created when the trip is published (admin only).
    # Members join later via confirm_join view, which updates members_list.
    # So we need to detect when members_list changes. We'll do that in the view instead,
    # because signals don't easily track changes on JSONField.
    # Alternative: send notification from confirm_join view directly (simpler).
    pass