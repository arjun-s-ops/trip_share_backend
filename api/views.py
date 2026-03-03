from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authtoken.models import Token
from firebase_admin import auth
from django.contrib.auth.models import User
from .models import Trip, Route, Vehicle, PaymentDetails, ContactDetails, GroupDetails, UserDetails, SeatAvailability
from .serializers import (
    UserProfileSerializer, 
    TripSerializer, RouteSerializer, VehicleSerializer, 
    PaymentDetailsSerializer, ContactDetailsSerializer, GroupDetailsSerializer
)

# --- AUTH VIEWS ---

@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    id_token = request.data.get('id_token')
    first_name = request.data.get('first_name', '')
    last_name = request.data.get('last_name', '')

    if not id_token:
        return Response({'error': 'ID token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Verify token with Firebase
        decoded = auth.verify_id_token(id_token)
        firebase_uid = decoded['uid']
        email = decoded.get('email')

        # Create or get standard Django User
        user, created = User.objects.get_or_create(
            username=firebase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name}
        )

        # Create or get UserDetails
        UserDetails.objects.get_or_create(
            user=user,
            defaults={
                'firebase_uid': firebase_uid,
                'name': f"{first_name} {last_name}".strip(),
                'email': email
            }
        )

        # Generate DRF Token for session management
        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            'key': token.key,
            'user_id': user.id
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    id_token = request.data.get('id_token')

    if not id_token:
        return Response({'error': 'ID token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Verify token with Firebase
        decoded = auth.verify_id_token(id_token)
        firebase_uid = decoded['uid']

        # Find user by Firebase UID (which we set as the username in signup)
        user = User.objects.get(username=firebase_uid)
        token, _ = Token.objects.get_or_create(user=user)

        # Fetch real name for SharedPreferences (kept from your original logic)
        try:
            real_name = user.details.name
        except UserDetails.DoesNotExist:
            real_name = user.first_name or user.username

        return Response({
            'key': token.key,
            'user_id': user.id,
            'first_name': real_name,
            'email': user.email
        }, status=status.HTTP_200_OK)

    except User.DoesNotExist:
        return Response({'error': 'User not found. Please sign up first.'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    serializer = UserProfileSerializer(request.user)
    return Response(serializer.data)


# --- TRIP FLOW VIEWS ---

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_trip(request):
    serializer = TripSerializer(data=request.data)
    if serializer.is_valid():
        trip = serializer.save(user=request.user)
        
        # 1. Initialize Remaining Seats Table
        SeatAvailability.objects.create(
            trip=trip,
            total_seats=trip.passengers,
            available_seats=trip.passengers 
        )

        # 2. Update User Details
        try:
            user_details = request.user.details
            current_list = list(user_details.trips_registered)
            if trip.id not in current_list:
                current_list.append(trip.id)
                user_details.trips_registered = current_list
                user_details.save()
        except UserDetails.DoesNotExist:
            pass 

        return Response({
            "message": "Trip saved successfully",
            "trip_id": trip.id
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_route(request):
    data = request.data
    trip_id = data.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=status.HTTP_404_NOT_FOUND)

    route_data = {
        'trip': trip.id,
        'start_location': data.get('start_location'),
        'stops': data.get('stops', []),
        'start_datetime': data.get('start_datetime'),
        'end_datetime': data.get('end_datetime'),
    }

    vehicle_data = {
        'trip': trip.id,
        'vehicle_number': data.get('vehicle_number'),
        'vehicle_model': data.get('vehicle_model')
    }

    try:
        route_instance = Route.objects.get(trip=trip)
        route_serializer = RouteSerializer(route_instance, data=route_data)
    except Route.DoesNotExist:
        route_serializer = RouteSerializer(data=route_data)

    try:
        vehicle_instance = Vehicle.objects.get(trip=trip)
        vehicle_serializer = VehicleSerializer(vehicle_instance, data=vehicle_data)
    except Vehicle.DoesNotExist:
        vehicle_serializer = VehicleSerializer(data=vehicle_data)

    if route_serializer.is_valid() and vehicle_serializer.is_valid():
        route_serializer.save()
        vehicle_serializer.save()
        return Response({"message": "Route and Vehicle details saved!"}, status=status.HTTP_200_OK)
    
    return Response(status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_payment(request):
    data = request.data
    trip_id = data.get('trip_id')
    
    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=status.HTTP_404_NOT_FOUND)

    payment_method = data.get('payment_method')
    details_map = data.get('payment_details', {})

    payment_data = {
        'trip': trip.id,
        'price_per_head': data.get('price_per_head'),
        'booking_deadline': data.get('booking_deadline'),
        'cancel_deadline': data.get('cancel_deadline'),
        'payment_method': payment_method,
        'upi_id': details_map.get('upi_id') if payment_method == 'UPI' else None,
        'account_no': details_map.get('account_no') if payment_method == 'Bank' else None,
        'ifsc': details_map.get('ifsc') if payment_method == 'Bank' else None,
    }

    try:
        payment_instance = PaymentDetails.objects.get(trip=trip)
        serializer = PaymentDetailsSerializer(payment_instance, data=payment_data)
    except PaymentDetails.DoesNotExist:
        serializer = PaymentDetailsSerializer(data=payment_data)

    if serializer.is_valid():
        serializer.save()
        return Response({"message": "Payment details saved successfully!"}, status=status.HTTP_201_CREATED)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_contact(request):
    data = request.data
    trip_id = data.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=status.HTTP_404_NOT_FOUND)

    contact_data = {
        'trip': trip.id,
        'phone': data.get('phone'),
        'email': data.get('email'),
        'is_phone_verified': True,
        'is_email_verified': True
    }

    try:
        contact_instance = ContactDetails.objects.get(trip=trip)
        contact_serializer = ContactDetailsSerializer(contact_instance, data=contact_data)
    except ContactDetails.DoesNotExist:
        contact_serializer = ContactDetailsSerializer(data=contact_data)

    if contact_serializer.is_valid():
        contact_serializer.save()
        
        group, created = GroupDetails.objects.get_or_create(
            trip=trip,
            defaults={
                'admin': request.user,
                'group_name': f"Trip to {trip.destination}",
                'members_count': 1,
                'members_list': [request.user.id] 
            }
        )

        return Response({
            "message": "Trip Published & Group Created!", 
            "group_id": group.id,
            "group_name": group.group_name
        }, status=status.HTTP_201_CREATED)
    
    return Response(contact_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_trips(request):
    """
    Fetches trips the user has registered for.
    INCLUDES: group_id, admin_id
    """
    try:
        user_details = request.user.details
        registered_ids = user_details.trips_registered 
        
        if not registered_ids:
            return Response([], status=status.HTTP_200_OK)

        trips = Trip.objects.filter(id__in=registered_ids)
        
        results = []
        for trip in trips:
            try:
                group = trip.group_info 
                group_name = group.group_name
                group_id = group.id 
                admin_id = group.admin.id 
            except GroupDetails.DoesNotExist:
                group_name = f"Trip to {trip.destination}"
                group_id = None
                admin_id = None

            results.append({
                "group_name": group_name,
                "group_id": group_id,   
                "admin_id": admin_id,   
                "destination": trip.destination,
                "date": trip.start_date,
                "last_message": f"Trip to {trip.destination} is confirmed!", 
                "time": "Just now" 
            })
            
        return Response(results, status=status.HTTP_200_OK)

    except UserDetails.DoesNotExist:
        return Response({"error": "User details not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_trips(request):
    """
    Returns all trips EXCLUDING the user's own trips.
    """
    try:
        # EXCLUDE self-created trips
        trips = Trip.objects.exclude(user=request.user).order_by('-created_at').select_related(
            'vehicle_details', 'route', 'payment_info', 'seat_info'
        )
        
        results = []
        
        for trip in trips:
            if not hasattr(trip, 'payment_info') or not hasattr(trip, 'route'):
                continue

            # 1. Date & Time
            start_str = "Date not set"
            start_location = "Unknown"
            if hasattr(trip, 'route'):
                start_location = trip.route.start_location
                if trip.route.start_datetime:
                    start_str = trip.route.start_datetime.strftime("%d %b, %I:%M %p")
                elif trip.start_date:
                    start_str = trip.start_date.strftime("%d %b")

            # 2. Vehicle
            vehicle_name = trip.vehicle 
            if hasattr(trip, 'vehicle_details'):
                vehicle_name = trip.vehicle_details.vehicle_model

            # 3. Price
            price = "₹0"
            if hasattr(trip, 'payment_info'):
                price = f"₹{trip.payment_info.price_per_head}"

            # 4. --- FIX: DO NOT COUNT ADMIN AS A PASSENGER ---
            max_capacity = trip.passengers # e.g., 4 passengers requested
            is_registered = False
            people_already = 0

            try:
                group = GroupDetails.objects.get(trip=trip)
                
                # Subtract 1 because the Admin is in the group but does NOT take a passenger seat.
                # max(0, ...) ensures it never goes negative if something weird happens.
                people_already = max(0, group.members_count - 1) 
                
                if request.user.id in group.members_list:
                    is_registered = True
            except GroupDetails.DoesNotExist:
                is_registered = False
            
            # Calculate seats left safely
            seats_left = max(0, max_capacity - people_already)
            
            # 5. Driver Name
            driver_name = "Unknown User"
            if hasattr(trip.user, 'details'):
                driver_name = trip.user.details.name
            else:
                driver_name = trip.user.first_name or trip.user.username

            results.append({
                "id": trip.id,
                "destination": trip.destination,
                "start_date": start_str,
                "vehicle": vehicle_name,
                "people_needed": seats_left,
                "max_capacity": max_capacity,
                "people_already": people_already,
                "price": price,
                "driver_name": driver_name,
                "user_id": trip.user.id,
                "from": start_location,
                "is_joined": is_registered 
            })

        return Response(results, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_join(request):
    """
    Handles the final 'Pay & Join' action.
    """
    trip_id = request.data.get('trip_id')
    
    try:
        trip = Trip.objects.get(id=trip_id)
        user = request.user

        # --- 1. VALIDATION ---
        
        # Check if user already registered
        if user.details.trips_registered and trip.id in user.details.trips_registered:
             return Response({"error": "You have already joined this trip."}, status=status.HTTP_400_BAD_REQUEST)

        # Get Seat Info
        try:
            seat_info = SeatAvailability.objects.get(trip=trip)
        except SeatAvailability.DoesNotExist:
            return Response({"error": "Seat information missing for this trip."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Check availability
        if seat_info.available_seats <= 0:
            return Response({"error": "Trip is full! No seats available."}, status=status.HTTP_400_BAD_REQUEST)

        # --- 2. EXECUTE UPDATES ---

        # A. Reduce Seat Count
        seat_info.available_seats -= 1
        seat_info.save()

        # B. Add User to Group
        group = GroupDetails.objects.get(trip=trip)
        
        current_members = list(group.members_list)
        if user.id not in current_members:
            current_members.append(user.id)
            group.members_list = current_members
            group.members_count = len(current_members)
            group.save()

        # C. Update User Profile
        user_details = user.details
        current_trips = list(user_details.trips_registered)
        current_trips.append(trip.id)
        user_details.trips_registered = current_trips
        user_details.save()

        # --- FIX: INCLUDE ADMIN ID ---
        return Response({
            "message": "Joined successfully!",
            "group_id": group.id,
            "group_name": group.group_name,
            "admin_id": group.admin.id, # <-- Vital for the Chat UI
            "destination": trip.destination
        }, status=status.HTTP_200_OK)

    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=status.HTTP_404_NOT_FOUND)
    except GroupDetails.DoesNotExist:
        return Response({"error": "Group chat not found for this trip"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)