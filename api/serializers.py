from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Trip, Route, Vehicle, PaymentDetails, ContactDetails, GroupDetails, UserDetails

# --- USER SERIALIZERS ---

class UserDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDetails
        fields = ['name', 'email', 'phone', 'trips_registered', 'trips_success']

class UserProfileSerializer(serializers.ModelSerializer):
    # Include UserDetails data in profile response
    details = UserDetailsSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'details']


# --- TRIP SERIALIZERS ---

class TripSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trip
        fields = ['id', 'destination', 'start_date', 'end_date', 'vehicle', 'passengers']

class RouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Route
        fields = ['trip', 'start_location', 'stops', 'start_datetime', 'end_datetime']

class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ['trip', 'vehicle_number', 'vehicle_model']

class PaymentDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentDetails
        fields = [
            'trip', 'price_per_head', 
            'booking_deadline', 'cancel_deadline', 
            'payment_method', 'upi_id', 'account_no', 'ifsc'
        ]

class ContactDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactDetails
        fields = ['trip', 'phone', 'email', 'is_phone_verified', 'is_email_verified']

class GroupDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupDetails
        fields = ['id', 'trip', 'group_name', 'admin', 'members_count', 'members_list']