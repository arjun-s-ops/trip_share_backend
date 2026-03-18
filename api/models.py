from django.db import models
from django.contrib.auth.models import User

class UserDetails(models.Model):
    user             = models.OneToOneField(User, on_delete=models.CASCADE, related_name='details')
    supabase_uid     = models.CharField(max_length=128, unique=True)
    name             = models.CharField(max_length=255)
    email            = models.EmailField(unique=True)
    phone            = models.CharField(max_length=15, blank=True, null=True)
    bio              = models.TextField(blank=True, default="")
    profile_picture  = models.TextField(blank=True, null=True)
    trips_registered = models.JSONField(default=list, blank=True)
    trips_success    = models.JSONField(default=list, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_details'

    def __str__(self):
        return self.name

class Trip(models.Model):
    user        = models.ForeignKey(User, on_delete=models.CASCADE)
    destination = models.CharField(max_length=255)
    start_date  = models.DateField()
    end_date    = models.DateField()
    vehicle     = models.CharField(max_length=50)
    passengers  = models.IntegerField()
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, default='active')

    class Meta:
        db_table = 'trip_details'

class Route(models.Model):
    trip           = models.OneToOneField(Trip, on_delete=models.CASCADE)
    start_location = models.CharField(max_length=255)
    stops          = models.JSONField(default=list)
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'route_details'

class Vehicle(models.Model):
    trip           = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='vehicle_details')
    vehicle_number = models.CharField(max_length=20)
    vehicle_model  = models.CharField(max_length=100)

    class Meta:
        db_table = 'vehicle_details'

class PaymentDetails(models.Model):
    trip             = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='payment_info')
    price_per_head   = models.IntegerField()
    booking_deadline = models.DateTimeField()
    cancel_deadline  = models.DateTimeField()
    PAYMENT_CHOICES  = [('UPI', 'UPI'), ('Bank', 'Bank Transfer')]
    payment_method   = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    upi_id           = models.CharField(max_length=100, null=True, blank=True)
    account_no       = models.CharField(max_length=50, null=True, blank=True)
    ifsc             = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        db_table = 'payment_details'

class ContactDetails(models.Model):
    trip              = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='contact_info')
    phone             = models.CharField(max_length=15)
    email             = models.EmailField()
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contact_details'

class GroupDetails(models.Model):
    trip          = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='group_info')
    group_name    = models.CharField(max_length=255)
    admin         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_groups')
    members_count = models.IntegerField(default=1)
    members_list  = models.JSONField(default=list)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'group_details'

class SeatAvailability(models.Model):
    trip            = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='seat_info')
    total_seats     = models.IntegerField()
    available_seats = models.IntegerField()

    class Meta:
        db_table = 'remaining_seats'

class Post(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    trip       = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='posts', null=True, blank=True)
    image_url  = models.TextField()
    caption    = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'posts'
        ordering = ['-created_at']

    def __str__(self):
        return f"Post by {self.user.username} - {self.trip.destination if self.trip else 'No trip'}"

class Follower(models.Model):
    follower   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='following')
    following  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'followers'
        unique_together = ('follower', 'following')

    def __str__(self):
        return f"{self.follower.username} → {self.following.username}"

class CompletedTrip(models.Model):
    user        = models.ForeignKey(User, on_delete=models.CASCADE)
    trip        = models.ForeignKey(Trip, on_delete=models.CASCADE)
    destination = models.CharField(max_length=255)
    start_date  = models.DateField()
    end_date    = models.DateField()
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "completed_trips"

    def __str__(self):
        return f"{self.user.username} - {self.destination}"

# api/models.py (append at the end)
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class Notification(models.Model):
    recipient   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    actor       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='actor_notifications')
    verb        = models.CharField(max_length=255)          # e.g. "followed you", "joined your trip"
    target_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    target_object_id    = models.PositiveIntegerField(null=True, blank=True)
    target      = GenericForeignKey('target_content_type', 'target_object_id')
    read        = models.BooleanField(default=False)
    timestamp   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.actor} {self.verb} -> {self.recipient}"