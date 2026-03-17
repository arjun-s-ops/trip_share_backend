from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authtoken.models import Token
import jwt
from jwt import PyJWKClient
from datetime import timedelta, date
from django.db import IntegrityError
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.cache import cache
from django.conf import settings
import random
from .models import (
    Trip, Route, Vehicle, PaymentDetails,
    ContactDetails, GroupDetails, UserDetails, SeatAvailability,
    Post, Follower, CompletedTrip
)
from .serializers import (
    UserProfileSerializer, OtherUserProfileSerializer,
    TripSerializer, RouteSerializer, VehicleSerializer,
    PaymentDetailsSerializer, ContactDetailsSerializer, GroupDetailsSerializer,
)

SUPABASE_JWKS_URL = 'https://tqmrytzypqsuxjwdrihh.supabase.co/auth/v1/.well-known/jwks.json'
OTP_EXPIRY_SECONDS = 600  # 10 minutes

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
    try:
        return UserDetails.objects.get(user=user)
    except UserDetails.DoesNotExist:
        pass
    try:
        details = UserDetails.objects.get(supabase_uid=supabase_uid)
        if details.user != user:
            details.user  = user
            details.email = email
            details.save()
        return details
    except UserDetails.DoesNotExist:
        pass
    try:
        details              = UserDetails.objects.get(email=email)
        details.user         = user
        details.supabase_uid = supabase_uid
        details.save()
        return details
    except UserDetails.DoesNotExist:
        pass
    try:
        return UserDetails.objects.create(
            user=user, supabase_uid=supabase_uid, name=name, email=email)
    except IntegrityError:
        try:
            return UserDetails.objects.get(supabase_uid=supabase_uid)
        except UserDetails.DoesNotExist:
            return UserDetails.objects.get(email=email)


# ── OTP ───────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_otp(request):
    email = request.data.get('email', '').strip()
    if not email or '@' not in email:
        return Response({'error': 'Valid email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        otp       = str(random.randint(100000, 999999))
        cache_key = f'otp_email_{email}'
        cache.set(cache_key, otp, timeout=OTP_EXPIRY_SECONDS)

        send_mail(
            subject='Your TripShare Verification Code',
            message=(
                f'Your verification code is: {otp}\n\n'
                f'This code is valid for 10 minutes.\n'
                f'Do not share this code with anyone.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return Response({'message': f'OTP sent to {email}'}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': f'Failed to send OTP: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_otp(request):
    email = request.data.get('email', '').strip()
    otp   = request.data.get('otp', '').strip()

    if not email or not otp:
        return Response({'error': 'Email and OTP are required'}, status=status.HTTP_400_BAD_REQUEST)

    cache_key   = f'otp_email_{email}'
    stored_otp  = cache.get(cache_key)

    if stored_otp is None:
        return Response({'verified': False, 'error': 'OTP expired or not sent'}, status=status.HTTP_400_BAD_REQUEST)

    if stored_otp != otp:
        return Response({'verified': False, 'error': 'Incorrect OTP'}, status=status.HTTP_400_BAD_REQUEST)

    cache.delete(cache_key)
    return Response({'verified': True}, status=status.HTTP_200_OK)


# ── AUTH & PROFILE ────────────────────────────────────────────────────────────

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
        user, _ = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name},
        )
        user_details = _get_or_fix_user_details(user, supabase_uid, email, name)
        token, _     = Token.objects.get_or_create(user=user)

        return Response({'key': token.key, 'user_id': user.id}, status=status.HTTP_201_CREATED)

    except jwt.ExpiredSignatureError:
        return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
    except jwt.InvalidTokenError as e:
        return Response({'error': f'Invalid token: {e}'}, status=status.HTTP_401_UNAUTHORIZED)
    except Exception as e:
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

        user, created = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name},
        )
        user_details = _get_or_fix_user_details(user, supabase_uid, email, name)
        token, _     = Token.objects.get_or_create(user=user)

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
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    if request.method == 'GET':
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)

    if request.method == 'PATCH':
        try:
            user_details = request.user.details
            bio             = request.data.get('bio')
            profile_picture = request.data.get('profile_picture')

            if bio is not None:
                user_details.bio = bio
            if profile_picture is not None:
                user_details.profile_picture = profile_picture

            user_details.save()
            return Response({'message': 'Profile updated successfully'}, status=status.HTTP_200_OK)
        except UserDetails.DoesNotExist:
            return Response({'error': 'User details not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def other_user_profile(request, user_id):
    try:
        target_user  = User.objects.get(id=user_id)
        is_following = Follower.objects.filter(
            follower=request.user, following=target_user).exists()
        serializer   = OtherUserProfileSerializer(target_user)
        
        # --- THE FIX ---
        # Convert the immutable ReturnDict into a standard, editable dictionary
        response_data = dict(serializer.data)
        
        # Get posts with trip details
        posts = Post.objects.filter(user=target_user).select_related('trip').order_by('-created_at')
        posts_data = []
        for post in posts:
            post_data = {
                'id': post.id,
                'image_url': post.image_url,
                'caption': post.caption,
                'created_at': post.created_at,
                'trip': {
                    'id': post.trip.id,
                    'destination': post.trip.destination,
                    'start_date': post.trip.start_date,
                    'end_date': post.trip.end_date
                } if post.trip else None
            }
            posts_data.append(post_data)
        
        # Now we can safely add our custom fields
        response_data['posts']          = posts_data
        response_data['is_following']   = is_following
        response_data['is_own_profile'] = (request.user.id == target_user.id)
        
        return Response(response_data, status=status.HTTP_200_OK)

    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        # Added a traceback print here so if a 500 ever happens again, 
        # it prints the exact line of the error in your terminal!
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def follow_user(request, user_id):
    try:
        target_user = User.objects.get(id=user_id)
        if target_user == request.user:
            return Response({'error': 'Cannot follow yourself'}, status=status.HTTP_400_BAD_REQUEST)
        follow, created = Follower.objects.get_or_create(follower=request.user, following=target_user)
        if not created:
            follow.delete()
            return Response({'following': False, 'message': 'Unfollowed'}, status=status.HTTP_200_OK)
        return Response({'following': True, 'message': 'Followed'}, status=status.HTTP_200_OK)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── GROUP ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_group_details(request, group_id):
    try:
        group   = GroupDetails.objects.get(id=group_id)
        members = []
        for uid in group.members_list:
            try:
                user        = User.objects.get(id=uid)
                user_detail = getattr(user, 'details', None)
                members.append({
                    'user_id':  user.id,
                    'name':     user_detail.name if user_detail else
                                f"{user.first_name} {user.last_name}".strip() or user.username,
                    'email':    user.email,
                    'is_admin': user.id == group.admin.id,
                })
            except User.DoesNotExist:
                pass
        return Response({
            'group_id':   group.id,
            'group_name': group.group_name,
            'admin_id':   group.admin.id,
            'members':    members,
        }, status=status.HTTP_200_OK)
    except GroupDetails.DoesNotExist:
        return Response({'error': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def rename_group(request, group_id):
    try:
        group = GroupDetails.objects.get(id=group_id)
        if group.admin != request.user:
            return Response({'error': 'Only admin can rename the group'}, status=status.HTTP_403_FORBIDDEN)
        new_name = request.data.get('group_name', '').strip()
        if not new_name:
            return Response({'error': 'Group name cannot be empty'}, status=status.HTTP_400_BAD_REQUEST)
        group.group_name = new_name
        group.save()
        return Response({'group_name': group.group_name}, status=status.HTTP_200_OK)
    except GroupDetails.DoesNotExist:
        return Response({'error': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── TRIP FLOW ─────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_trip(request):
    serializer = TripSerializer(data=request.data)
    if serializer.is_valid():
        trip = serializer.save(user=request.user)
        SeatAvailability.objects.create(trip=trip, total_seats=trip.passengers, available_seats=trip.passengers)
        try:
            user_details = request.user.details
            current_list = list(user_details.trips_registered)
            if trip.id not in current_list:
                current_list.append(trip.id)
                user_details.trips_registered = current_list
                user_details.save()
        except UserDetails.DoesNotExist:
            pass
        return Response({'message': 'Trip saved successfully', 'trip_id': trip.id}, status=status.HTTP_201_CREATED)
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
        'ifsc':       details_map.get('ifsc')       if payment_method == 'Bank' else None,
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
        contact_serializer = ContactDetailsSerializer(ContactDetails.objects.get(trip=trip), data=contact_data)
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
            vehicle_name = (trip.vehicle_details.vehicle_model
                            if hasattr(trip, 'vehicle_details') else trip.vehicle)
            price        = (f"₹{trip.payment_info.price_per_head}"
                            if hasattr(trip, 'payment_info') else '₹0')
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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_completed_trips(request):
    try:
        user = request.user
        try:
            user_details = user.details
        except UserDetails.DoesNotExist:
            return Response([], status=status.HTTP_200_OK)

        trip_ids = user_details.trips_registered or []
        trips = Trip.objects.filter(id__in=trip_ids)
        completed_list = []

        for trip in trips:
            if trip.end_date:
                CompletedTrip.objects.get_or_create(
                    user=user,
                    trip=trip,
                    defaults={
                        "destination": trip.destination,
                        "start_date": trip.start_date,
                        "end_date": trip.end_date,
                    }
                )
                completed_list.append({
                    "trip_id": trip.id,
                    "destination": trip.destination,
                    "start_date": trip.start_date,
                    "end_date": trip.end_date
                })

        return Response(completed_list, status=status.HTTP_200_OK)
    except Exception as e:
        print("COMPLETED TRIPS ERROR:", e)
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── POSTS (ALBUMS) ────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_post(request):
    user = request.user
    trip_id = request.data.get("trip_id")
    images = request.data.get("images", [])

    try:
        trip = Trip.objects.get(id=trip_id)
    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=status.HTTP_404_NOT_FOUND)

    created_posts = []
    for img in images:
        post = Post.objects.create(
            user=user,
            trip=trip,
            image_url=img,
        )
        created_posts.append({
            "id": post.id,
            "image_url": post.image_url,
            "trip": {
                "id": trip.id,
                "destination": trip.destination,
                "start_date": trip.start_date,
                "end_date": trip.end_date
            }
        })

    return Response({
        "message": "Post created",
        "posts": created_posts
    }, status=status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_post(request, post_id):
    try:
        post = Post.objects.get(id=post_id, user=request.user)
        # Optional: Delete from Supabase storage (handled safely on the front end usually)
        post.delete()
        return Response({"message": "Post deleted successfully"}, status=status.HTTP_200_OK)
    except Post.DoesNotExist:
        return Response({"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)