from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authtoken.models import Token
import jwt
from jwt import PyJWKClient
from datetime import timedelta
from django.db import IntegrityError
from django.contrib.auth.models import User
from .models import (
    Trip, Route, Vehicle, PaymentDetails,
    ContactDetails, GroupDetails, UserDetails, SeatAvailability,
)
from .serializers import (
    UserProfileSerializer,
    TripSerializer, RouteSerializer, VehicleSerializer,
    PaymentDetailsSerializer, ContactDetailsSerializer, GroupDetailsSerializer,
)

SUPABASE_JWKS_URL = 'https://tqmrytzypqsuxjwdrihh.supabase.co/auth/v1/.well-known/jwks.json'


def _verify_supabase_token(access_token: str) -> dict:
    try:
        jwks_client = PyJWKClient(SUPABASE_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(access_token)
        decoded = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["ES256", "RS256", "HS256"],
            options={"verify_aud": False},
            leeway=timedelta(seconds=60),
        )
        print(f"✅ JWT verified. User: {decoded.get('sub')}")
        return decoded
    except Exception as e:
        print(f"❌ JWT decode failed: {e}")
        raise


def _extract_name(decoded: dict):
    meta      = decoded.get('user_metadata', {})
    full_name = meta.get('full_name') or meta.get('name', '')
    if full_name:
        parts = full_name.strip().split(' ', 1)
        return parts[0], parts[1] if len(parts) > 1 else ''
    return (
        meta.get('first_name') or meta.get('given_name', ''),
        meta.get('last_name')  or meta.get('family_name', ''),
    )


def _get_or_fix_user_details(user, supabase_uid, email, name):
    # 1. Already linked to this user
    try:
        return UserDetails.objects.get(user=user)
    except UserDetails.DoesNotExist:
        pass

    # 2. Exists under same supabase_uid (race condition / duplicate request)
    try:
        details = UserDetails.objects.get(supabase_uid=supabase_uid)
        if details.user != user:
            details.user  = user
            details.email = email
            details.save()
        return details
    except UserDetails.DoesNotExist:
        pass

    # 3. Exists under same email (e.g. email signup then Google login)
    try:
        details              = UserDetails.objects.get(email=email)
        details.user         = user
        details.supabase_uid = supabase_uid
        details.save()
        return details
    except UserDetails.DoesNotExist:
        pass

    # 4. Truly new — create, handle race condition gracefully
    try:
        return UserDetails.objects.create(
            user=user,
            supabase_uid=supabase_uid,
            name=name,
            email=email,
        )
    except IntegrityError:
        try:
            return UserDetails.objects.get(supabase_uid=supabase_uid)
        except UserDetails.DoesNotExist:
            return UserDetails.objects.get(email=email)


# ── AUTH ──────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    access_token = request.data.get('access_token')
    first_name   = request.data.get('first_name', '')
    last_name    = request.data.get('last_name', '')

    if not access_token:
        return Response({'error': 'access_token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        decoded      = _verify_supabase_token(access_token)
        supabase_uid = decoded['sub']
        email        = decoded.get('email', '')

        if not first_name:
            first_name, last_name = _extract_name(decoded)

        name = f"{first_name} {last_name}".strip() or email
        print(f"👤 Signup: uid={supabase_uid} email={email} name={name}")

        user, _ = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name},
        )

        user_details = _get_or_fix_user_details(user, supabase_uid, email, name)
        token, _     = Token.objects.get_or_create(user=user)

        print(f"✅ Signup success: user_id={user.id}")
        return Response({'key': token.key, 'user_id': user.id}, status=status.HTTP_201_CREATED)

    except jwt.ExpiredSignatureError:
        return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
    except jwt.InvalidTokenError as e:
        return Response({'error': f'Invalid token: {e}'}, status=status.HTTP_401_UNAUTHORIZED)
    except Exception as e:
        print(f"❌ Signup error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    access_token = request.data.get('access_token')

    if not access_token:
        return Response({'error': 'access_token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        decoded      = _verify_supabase_token(access_token)
        supabase_uid = decoded['sub']
        email        = decoded.get('email', '')

        first_name, last_name = _extract_name(decoded)
        name = f"{first_name} {last_name}".strip() or email
        print(f"👤 Login: uid={supabase_uid} email={email} name={name}")

        user, created = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name},
        )

        user_details = _get_or_fix_user_details(user, supabase_uid, email, name)
        token, _     = Token.objects.get_or_create(user=user)

        print(f"✅ Login success: user_id={user.id} created={created}")
        return Response({
            'key':        token.key,
            'user_id':    user.id,
            'first_name': user_details.name,
            'email':      user.email,
            'created':    created,
        }, status=status.HTTP_200_OK)

    except jwt.ExpiredSignatureError:
        return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
    except jwt.InvalidTokenError as e:
        return Response({'error': f'Invalid token: {e}'}, status=status.HTTP_401_UNAUTHORIZED)
    except Exception as e:
        print(f"❌ Login error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    serializer = UserProfileSerializer(request.user)
    return Response(serializer.data)


# ── TRIP FLOW ─────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_trip(request):
    serializer = TripSerializer(data=request.data)
    if serializer.is_valid():
        trip = serializer.save(user=request.user)
        SeatAvailability.objects.create(
            trip=trip, total_seats=trip.passengers, available_seats=trip.passengers)
        try:
            user_details = request.user.details
            current_list = list(user_details.trips_registered)
            if trip.id not in current_list:
                current_list.append(trip.id)
                user_details.trips_registered = current_list
                user_details.save()
        except UserDetails.DoesNotExist:
            pass
        return Response({'message': 'Trip saved successfully', 'trip_id': trip.id},
                        status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_route(request):
    data    = request.data
    trip_id = data.get('trip_id')
    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

    route_data = {
        'trip': trip.id, 'start_location': data.get('start_location'),
        'stops': data.get('stops', []), 'start_datetime': data.get('start_datetime'),
        'end_datetime': data.get('end_datetime'),
    }
    vehicle_data = {
        'trip': trip.id, 'vehicle_number': data.get('vehicle_number'),
        'vehicle_model': data.get('vehicle_model'),
    }

    try:
        route_serializer = RouteSerializer(Route.objects.get(trip=trip), data=route_data)
    except Route.DoesNotExist:
        route_serializer = RouteSerializer(data=route_data)

    try:
        vehicle_serializer = VehicleSerializer(Vehicle.objects.get(trip=trip), data=vehicle_data)
    except Vehicle.DoesNotExist:
        vehicle_serializer = VehicleSerializer(data=vehicle_data)

    if route_serializer.is_valid() and vehicle_serializer.is_valid():
        route_serializer.save()
        vehicle_serializer.save()
        return Response({'message': 'Route and Vehicle details saved!'}, status=status.HTTP_200_OK)
    return Response(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_payment(request):
    data           = request.data
    trip_id        = data.get('trip_id')
    payment_method = data.get('payment_method')
    details_map    = data.get('payment_details', {})
    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

    payment_data = {
        'trip': trip.id, 'price_per_head': data.get('price_per_head'),
        'booking_deadline': data.get('booking_deadline'),
        'cancel_deadline':  data.get('cancel_deadline'),
        'payment_method':   payment_method,
        'upi_id':     details_map.get('upi_id')     if payment_method == 'UPI'  else None,
        'account_no': details_map.get('account_no') if payment_method == 'Bank' else None,
        'ifsc':       details_map.get('ifsc')        if payment_method == 'Bank' else None,
    }
    try:
        serializer = PaymentDetailsSerializer(PaymentDetails.objects.get(trip=trip), data=payment_data)
    except PaymentDetails.DoesNotExist:
        serializer = PaymentDetailsSerializer(data=payment_data)

    if serializer.is_valid():
        serializer.save()
        return Response({'message': 'Payment details saved!'}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_contact(request):
    data    = request.data
    trip_id = data.get('trip_id')
    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

    contact_data = {
        'trip': trip.id, 'phone': data.get('phone'), 'email': data.get('email'),
        'is_phone_verified': data.get('is_phone_verified', False),
        'is_email_verified': data.get('is_email_verified', False),
    }
    try:
        contact_serializer = ContactDetailsSerializer(
            ContactDetails.objects.get(trip=trip), data=contact_data)
    except ContactDetails.DoesNotExist:
        contact_serializer = ContactDetailsSerializer(data=contact_data)

    if contact_serializer.is_valid():
        contact_serializer.save()
        group, _ = GroupDetails.objects.get_or_create(
            trip=trip,
            defaults={
                'admin': request.user, 'group_name': f"Trip to {trip.destination}",
                'members_count': 1, 'members_list': [request.user.id],
            },
        )
        return Response({'message': 'Trip Published & Group Created!',
                         'group_id': group.id, 'group_name': group.group_name},
                        status=status.HTTP_201_CREATED)
    return Response(contact_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_trips(request):
    try:
        user_details   = request.user.details
        registered_ids = user_details.trips_registered
        if not registered_ids:
            return Response([], status=status.HTTP_200_OK)
        results = []
        for trip in Trip.objects.filter(id__in=registered_ids):
            try:
                group = trip.group_info
                group_name, group_id, admin_id = group.group_name, group.id, group.admin.id
            except GroupDetails.DoesNotExist:
                group_name, group_id, admin_id = f"Trip to {trip.destination}", None, None
            results.append({
                'group_name': group_name, 'group_id': group_id, 'admin_id': admin_id,
                'destination': trip.destination, 'date': trip.start_date,
                'last_message': f"Trip to {trip.destination} is confirmed!", 'time': 'Just now',
            })
        return Response(results, status=status.HTTP_200_OK)
    except UserDetails.DoesNotExist:
        return Response({'error': 'User details not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_trips(request):
    try:
        trips = Trip.objects.exclude(user=request.user).order_by('-created_at').select_related(
            'vehicle_details', 'route', 'payment_info', 'seat_info')
        results = []
        for trip in trips:
            if not hasattr(trip, 'payment_info') or not hasattr(trip, 'route'):
                continue
            start_str, start_location = 'Date not set', 'Unknown'
            if hasattr(trip, 'route'):
                start_location = trip.route.start_location
                if trip.route.start_datetime:
                    start_str = trip.route.start_datetime.strftime('%d %b, %I:%M %p')
                elif trip.start_date:
                    start_str = trip.start_date.strftime('%d %b')
            vehicle_name = trip.vehicle_details.vehicle_model if hasattr(trip, 'vehicle_details') else trip.vehicle
            price        = f"₹{trip.payment_info.price_per_head}" if hasattr(trip, 'payment_info') else '₹0'
            max_capacity, is_registered, people_already = trip.passengers, False, 0
            try:
                group          = GroupDetails.objects.get(trip=trip)
                people_already = max(0, group.members_count - 1)
                if request.user.id in group.members_list:
                    is_registered = True
            except GroupDetails.DoesNotExist:
                pass
            driver_name = (trip.user.details.name if hasattr(trip.user, 'details')
                           else (trip.user.first_name or trip.user.username))
            results.append({
                'id': trip.id, 'destination': trip.destination, 'start_date': start_str,
                'vehicle': vehicle_name, 'people_needed': max(0, max_capacity - people_already),
                'max_capacity': max_capacity, 'people_already': people_already,
                'price': price, 'driver_name': driver_name, 'user_id': trip.user.id,
                'from': start_location, 'is_joined': is_registered,
            })
        return Response(results, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_join(request):
    trip_id = request.data.get('trip_id')
    try:
        trip, user = Trip.objects.get(id=trip_id), request.user
        if user.details.trips_registered and trip.id in user.details.trips_registered:
            return Response({'error': 'You have already joined this trip.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            seat_info = SeatAvailability.objects.get(trip=trip)
        except SeatAvailability.DoesNotExist:
            return Response({'error': 'Seat information missing.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if seat_info.available_seats <= 0:
            return Response({'error': 'Trip is full!'}, status=status.HTTP_400_BAD_REQUEST)

        seat_info.available_seats -= 1
        seat_info.save()

        group           = GroupDetails.objects.get(trip=trip)
        current_members = list(group.members_list)
        if user.id not in current_members:
            current_members.append(user.id)
            group.members_list  = current_members
            group.members_count = len(current_members)
            group.save()

        user_details  = user.details
        current_trips = list(user_details.trips_registered)
        current_trips.append(trip.id)
        user_details.trips_registered = current_trips
        user_details.save()

        return Response({
            'message': 'Joined successfully!', 'group_id': group.id,
            'group_name': group.group_name, 'admin_id': group.admin.id,
            'destination': trip.destination,
        }, status=status.HTTP_200_OK)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)
    except GroupDetails.DoesNotExist:
        return Response({'error': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)