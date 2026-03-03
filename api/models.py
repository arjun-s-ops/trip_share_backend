from django.db import models
from django.contrib.auth.models import User

# --- USER DETAILS ---
class UserDetails(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='details')
    firebase_uid = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    trips_registered = models.JSONField(default=list, blank=True)
    trips_success = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_details'

    def __str__(self):
        return self.name

# --- TRIP (Step 1) ---
class Trip(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE) 
    destination = models.CharField(max_length=255)
    start_date = models.DateField() 
    end_date = models.DateField()   
    vehicle = models.CharField(max_length=50) 
    passengers = models.IntegerField() # This is Max Capacity
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'trip_details'

# --- ROUTE (Step 2) ---
class Route(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE) # Default related_name='route'
    start_location = models.CharField(max_length=255)
    stops = models.JSONField(default=list) 
    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'route_details'

class Vehicle(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='vehicle_details')
    vehicle_number = models.CharField(max_length=20)
    vehicle_model = models.CharField(max_length=100)

    class Meta:
        db_table = 'vehicle_details'

# --- PAYMENT (Step 3) ---
class PaymentDetails(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='payment_info')
    price_per_head = models.IntegerField()
    booking_deadline = models.DateTimeField()
    cancel_deadline = models.DateTimeField()
    PAYMENT_CHOICES = [('UPI', 'UPI'), ('Bank', 'Bank Transfer')]
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    upi_id = models.CharField(max_length=100, null=True, blank=True)
    account_no = models.CharField(max_length=50, null=True, blank=True)
    ifsc = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        db_table = 'payment_details'

# --- CONTACT (Step 4) ---
class ContactDetails(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='contact_info')
    phone = models.CharField(max_length=15)
    email = models.EmailField()
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contact_details'

# --- GROUP (Step 5) ---
class GroupDetails(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='group_info')
    group_name = models.CharField(max_length=255) 
    admin = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_groups')
    members_count = models.IntegerField(default=1)
    members_list = models.JSONField(default=list) 
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'group_details'

# --- SEATS (New Model) ---
class SeatAvailability(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='seat_info')
    total_seats = models.IntegerField()
    available_seats = models.IntegerField() # Decreases on booking

    class Meta:
        db_table = 'remaining_seats'