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
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import random
import traceback

from .models import (
    Trip, Route, Vehicle, PaymentDetails,
    ContactDetails, GroupDetails, UserDetails, SeatAvailability,
    Post, Follower, CompletedTrip, Notification
)
from .serializers import (
    UserProfileSerializer, OtherUserProfileSerializer,
    TripSerializer, RouteSerializer, VehicleSerializer,
    PaymentDetailsSerializer, ContactDetailsSerializer,
    GroupDetailsSerializer, NotificationSerializer
)

SUPABASE_JWKS_URL = 'https://tqmrytzypqsuxjwdrihh.supabase.co/auth/v1/.well-known/jwks.json'
OTP_EXPIRY_SECONDS = 600

# ── HELPERS ──────────────────────────────────────────────────────────────────

def _verify_supabase_token(access_token: str) -> dict:
    try:
        jwks_client = PyJWKClient(SUPABASE_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(access_token)
        return jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["ES256", "RS256", "HS256"],
            options={"verify_aud": False},
            leeway=timedelta(seconds=60),
        )
    except Exception as e:
        print(f"❌ JWT decode failed: {e}")
        raise

def _extract_name(decoded: dict):
    meta = decoded.get('user_metadata', {})
    full_name = meta.get('full_name') or meta.get('name', '')
    if full_name:
        parts = full_name.strip().split(' ', 1)
        return parts[0], parts[1] if len(parts) > 1 else ''
    return (
        meta.get('first_name') or meta.get('given_name', ''),
        meta.get('last_name')  or meta.get('family_name', ''),
    )

def _get_or_fix_user_details(user, supabase_uid, email, name):
    details, created = UserDetails.objects.get_or_create(
        user=user,
        defaults={'supabase_uid': supabase_uid, 'email': email, 'name': name}
    )
    if not created and (details.supabase_uid != supabase_uid or details.email != email):
        details.supabase_uid = supabase_uid
        details.email = email
        details.save()
    return details


# ── TRIP CREATION FLOW ────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_route(request):
    """Save route details for a trip"""
    data = request.data
    trip_id = data.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

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

    errors = {}
    if not route_serializer.is_valid():
        errors.update(route_serializer.errors)
    if not vehicle_serializer.is_valid():
        errors.update(vehicle_serializer.errors)
    return Response(errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_payment(request):
    """Save payment details for a trip"""
    data = request.data
    trip_id = data.get('trip_id')
    payment_method = data.get('payment_method')
    details_map = data.get('payment_details', {})

    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

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
    """Save contact details for a trip and publish it"""
    data = request.data
    trip_id = data.get('trip_id')

    try:
        trip = Trip.objects.get(id=trip_id, user=request.user)
    except Trip.DoesNotExist:
        return Response({'error': 'Trip not found'}, status=status.HTTP_404_NOT_FOUND)

    contact_data = {
        'trip': trip.id,
        'phone': data.get('phone'),
        'email': data.get('email'),
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
                'admin': request.user,
                'group_name': f"Trip to {trip.destination}",
                'members_count': 1,
                'members_list': [request.user.id],
            },
        )

        return Response({
            'message': 'Trip Published & Group Created!',
            'group_id': group.id,
            'group_name': group.group_name
        }, status=status.HTTP_201_CREATED)

    return Response(contact_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ── DATA RETRIEVAL ────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_trips(request):
    """Get all trips the user has joined (for group/chat view)"""
    try:
        user_details = request.user.details
        registered_ids = user_details.trips_registered or []

        if not registered_ids:
            return Response([], status=status.HTTP_200_OK)

        results = []
        for trip in Trip.objects.filter(id__in=registered_ids):
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
                'group_name': group_name,
                'group_id': group_id,
                'admin_id': admin_id,
                'destination': trip.destination,
                'date': trip.start_date,
                'last_message': f"Trip to {trip.destination} is confirmed!",
                'time': 'Just now',
            })

        return Response(results, status=status.HTTP_200_OK)

    except UserDetails.DoesNotExist:
        return Response({'error': 'User details not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_user_trips(request):
    """Get all trips the user is part of — ongoing + completed (for post creation)"""
    try:
        user_details = request.user.details
        trip_ids = user_details.trips_registered or []

        if not trip_ids:
            return Response([], status=status.HTTP_200_OK)

        results = []
        for trip in Trip.objects.filter(id__in=trip_ids):
            results.append({
                'trip_id': trip.id,
                'destination': trip.destination,
                'start_date': str(trip.start_date),
                'end_date': str(trip.end_date),
            })

        return Response(results, status=status.HTTP_200_OK)

    except UserDetails.DoesNotExist:
        return Response([], status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_trips(request):
    """Search for available trips"""
    try:
        trips = Trip.objects.exclude(user=request.user).order_by('-created_at')
        results = []

        for trip in trips:
            if not hasattr(trip, 'payment_info') or not hasattr(trip, 'route'):
                continue

            start_str = 'Date not set'
            start_location = 'Unknown'
            if hasattr(trip, 'route'):
                start_location = trip.route.start_location
                if trip.route.start_datetime:
                    start_str = trip.route.start_datetime.strftime('%d %b, %I:%M %p')
                elif trip.start_date:
                    start_str = trip.start_date.strftime('%d %b')

            vehicle_name = trip.vehicle_details.vehicle_model if hasattr(trip, 'vehicle_details') else trip.vehicle
            price = f"₹{trip.payment_info.price_per_head}" if hasattr(trip, 'payment_info') else '₹0'

            max_capacity = trip.passengers
            is_registered = False
            people_already = 0

            try:
                group = GroupDetails.objects.get(trip=trip)
                people_already = max(0, group.members_count - 1)
                if request.user.id in group.members_list:
                    is_registered = True
            except GroupDetails.DoesNotExist:
                pass

            driver_name = trip.user.details.name if hasattr(trip.user, 'details') else (trip.user.first_name or trip.user.username)

            results.append({
                'id': trip.id,
                'destination': trip.destination,
                'start_date': start_str,
                'vehicle': vehicle_name,
                'people_needed': max(0, max_capacity - people_already),
                'max_capacity': max_capacity,
                'people_already': people_already,
                'price': price,
                'driver_name': driver_name,
                'user_id': trip.user.id,
                'from': start_location,
                'is_joined': is_registered,
            })

        return Response(results, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_completed_trips(request):
    """Get all completed trips for the user"""
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
            if trip.end_date and trip.end_date < date.today():
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


# ── GROUP ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_group_details(request, group_id):
    """Get details of a specific group"""
    try:
        group = GroupDetails.objects.get(id=group_id)
        members = []

        for uid in group.members_list:
            try:
                user = User.objects.get(id=uid)
                user_detail = getattr(user, 'details', None)
                members.append({
                    'user_id': user.id,
                    'name': user_detail.name if user_detail else (f"{user.first_name} {user.last_name}".strip() or user.username),
                    'email': user.email,
                    'is_admin': user.id == group.admin.id,
                })
            except User.DoesNotExist:
                pass

        return Response({
            'group_id': group.id,
            'group_name': group.group_name,
            'admin_id': group.admin.id,
            'members': members,
        }, status=status.HTTP_200_OK)

    except GroupDetails.DoesNotExist:
        return Response({'error': 'Group not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def rename_group(request, group_id):
    """Rename a group (admin only)"""
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


# ── POSTS ─────────────────────────────────────────────────────────────────────

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_post(request, post_id):
    """Delete a post"""
    try:
        post = Post.objects.get(id=post_id, user=request.user)
        post.delete()
        return Response({"message": "Post deleted successfully"}, status=status.HTTP_200_OK)

    except Post.DoesNotExist:
        return Response({"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_post(request):
    trip_id = request.data.get("trip_id")
    images = request.data.get("images", [])

    try:
        trip = Trip.objects.get(id=trip_id)
    except Trip.DoesNotExist:
        return Response({"error": "Trip not found"}, status=404)

    try:
        group = GroupDetails.objects.get(trip=trip)
    except GroupDetails.DoesNotExist:
        return Response({"error": "Group not found"}, status=404)

    created_posts = []
    for img in images:
        post = Post.objects.create(user=request.user, trip=trip, image_url=img)
        created_posts.append({"id": post.id, "image_url": post.image_url})

    # Notify ALL members except the poster
    member_ids = [m for m in group.members_list if m != request.user.id]
    members = User.objects.filter(id__in=member_ids)

    notifications = []
    for m in members:
        notifications.append(
            Notification(recipient=m, actor=request.user, verb='posted in your trip', target=trip)
        )

    if notifications:
        Notification.objects.bulk_create(notifications)
        print(f"✅ Post notifications created: {len(notifications)} members notified for trip {trip.id}")
    else:
        print(f"⚠️ No members to notify for trip {trip.id} (member_ids: {member_ids})")

    return Response({"message": "Posts created", "posts": created_posts})


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_notifications(request):
    """Return all notifications for the current user, newest first."""
    notifications = Notification.objects.filter(recipient=request.user).order_by('-timestamp')
    serializer = NotificationSerializer(notifications, many=True)
    return Response(serializer.data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, pk):
    """Mark a single notification as read."""
    try:
        notif = Notification.objects.get(pk=pk, recipient=request.user)
        notif.read = True
        notif.save()
        return Response({'status': 'marked read'})
    except Notification.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_all_read(request):
    """Mark all notifications for the user as read."""
    Notification.objects.filter(recipient=request.user, read=False).update(read=True)
    return Response({'status': 'all marked read'})


# ── OTP ───────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_otp(request):
    email = request.data.get('email', '').strip()
    if not email or '@' not in email:
        return Response({'error': 'Valid email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        otp = str(random.randint(100000, 999999))
        cache.set(f'otp_email_{email}', otp, timeout=OTP_EXPIRY_SECONDS)

        send_mail(
            subject='Your TripShare Verification Code',
            message=f'Your verification code is: {otp}\n\nValid for 10 minutes.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return Response({'message': f'OTP sent to {email}'}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_otp(request):
    email = request.data.get('email', '').strip()
    otp = request.data.get('otp', '').strip()
    cache_key = f'otp_email_{email}'
    stored_otp = cache.get(cache_key)

    if stored_otp and stored_otp == otp:
        cache.delete(cache_key)
        return Response({'verified': True}, status=status.HTTP_200_OK)
    return Response({'verified': False, 'error': 'Invalid or expired OTP'}, status=status.HTTP_400_BAD_REQUEST)


# ── AUTH & PROFILE ────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    """Handle user signup with Supabase token"""
    access_token = request.data.get('access_token')
    first_name = request.data.get('first_name', '')
    last_name = request.data.get('last_name', '')

    if not access_token:
        return Response({'error': 'access_token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        decoded = _verify_supabase_token(access_token)
        supabase_uid = decoded['sub']
        email = decoded.get('email', '')

        if not first_name:
            first_name, last_name = _extract_name(decoded)

        name = f"{first_name} {last_name}".strip() or email
        user, _ = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': first_name, 'last_name': last_name},
        )
        user_details = _get_or_fix_user_details(user, supabase_uid, email, name)
        token, _ = Token.objects.get_or_create(user=user)

        return Response({'key': token.key, 'user_id': user.id}, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    """Get or update current user's profile"""
    if request.method == 'GET':
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)

    try:
        user_details = request.user.details
        bio = request.data.get('bio')
        profile_picture = request.data.get('profile_picture')

        if bio is not None:
            user_details.bio = bio
        if profile_picture is not None:
            user_details.profile_picture = profile_picture

        user_details.save()
        return Response({'message': 'Profile updated successfully'}, status=status.HTTP_200_OK)
    except UserDetails.DoesNotExist:
        return Response({'error': 'User details not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    access_token = request.data.get('access_token')
    if not access_token:
        return Response({'error': 'access_token is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        decoded = _verify_supabase_token(access_token)
        supabase_uid = decoded['sub']
        email = decoded.get('email', '')
        f_name, l_name = _extract_name(decoded)

        user, created = User.objects.get_or_create(
            username=supabase_uid,
            defaults={'email': email, 'first_name': f_name, 'last_name': l_name},
        )
        details = _get_or_fix_user_details(user, supabase_uid, email, f"{f_name} {l_name}".strip())
        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            'key': token.key,
            'user_id': user.id,
            'first_name': details.name,
            'email': user.email,
            'created': created,
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def other_user_profile(request, user_id):
    try:
        target_user = User.objects.select_related('details').get(id=user_id)
        serializer = OtherUserProfileSerializer(target_user)
        response_data = dict(serializer.data)

        posts = Post.objects.filter(user=target_user).select_related('trip').order_by('-created_at')
        response_data['posts'] = [{
            'id': p.id, 'image_url': p.image_url, 'caption': p.caption, 'created_at': p.created_at,
            'trip': {'id': p.trip.id, 'destination': p.trip.destination} if p.trip else None
        } for p in posts]

        response_data['is_following'] = Follower.objects.filter(follower=request.user, following=target_user).exists()
        response_data['is_own_profile'] = (request.user.id == target_user.id)

        return Response(response_data)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def follow_user(request, user_id):
    """Follow or unfollow a user"""
    try:
        target_user = User.objects.get(id=user_id)
        if target_user == request.user:
            return Response({'error': 'Cannot follow yourself'}, status=status.HTTP_400_BAD_REQUEST)

        follow, created = Follower.objects.get_or_create(follower=request.user, following=target_user)

        if not created:
            follow.delete()
            return Response({'following': False, 'message': 'Unfollowed'}, status=status.HTTP_200_OK)

        Notification.objects.create(
            recipient=target_user,
            actor=request.user,
            verb='started following you'
        )
        print(f"✅ Follow notification created: {request.user} followed {target_user}")

        return Response({'following': True, 'message': 'Followed'}, status=status.HTTP_200_OK)

    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
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

        details, _ = UserDetails.objects.get_or_create(user=request.user)
        registered = list(details.trips_registered or [])
        if trip.id not in registered:
            registered.append(trip.id)
            details.trips_registered = registered
            details.save()

        return Response({'message': 'Trip saved', 'trip_id': trip.id}, status=201)
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_join(request):
    trip_id = request.data.get('trip_id')
    try:
        trip = Trip.objects.get(id=trip_id)
        group = GroupDetails.objects.get(trip=trip)

        if request.user.id not in group.members_list:
            group.members_list.append(request.user.id)
            group.members_count += 1
            group.save()

            # Register trip in user's trips_registered
            details, _ = UserDetails.objects.get_or_create(user=request.user)
            registered = list(details.trips_registered or [])
            if trip.id not in registered:
                registered.append(trip.id)
                details.trips_registered = registered
                details.save()

            # 🔔 Create notification
            notif = Notification.objects.create(
                recipient=group.admin,
                actor=request.user,
                verb='joined your trip',
                target=trip,
            )
            print(f"✅ Join notification created: {request.user} joined trip {trip.id}, notified admin {group.admin.id}")

            # Real-time WebSocket update — wrapped so it never breaks the response
            try:
                channel_layer = get_channel_layer()
                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        f'user_{group.admin.id}',
                        {
                            'type': 'notification_message',
                            'data': NotificationSerializer(notif).data
                        }
                    )
            except Exception as ws_error:
                print(f"⚠️ WebSocket notification failed (non-critical): {ws_error}")

            return Response({'message': 'Joined successfully'})
        return Response({'message': 'Already a member'})
    except Exception as e:
        print(f"❌ confirm_join error: {e}")
        return Response({'error': str(e)}, status=400)