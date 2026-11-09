# Fitverx Public Checkout API Documentation

Documentation and integration reference for the independently deployed Next.js frontend (`checkout.fitverx.com`).

---

## 1. Base Configuration

- **Production Base URL**: `https://api.fitverx.com/api/v1/checkout`
- **Local Dev Base URL**: `http://localhost:8000/api/v1/checkout`
- **Postman Collection**: [`docs/fitverx_checkout_postman_collection.json`](file:///Users/ansmustafa/gym-app-backend/docs/fitverx_checkout_postman_collection.json)

### Session Security Header
All endpoints operating on an active session (`/sessions/{session_id}/...`) require the raw session token generated upon session creation.

Provide this token using **any** of the following:
1. Header: `X-Checkout-Token: <raw_session_token>` *(Recommended)*
2. Header: `Authorization: Bearer <raw_session_token>`
3. Query Parameter: `?token=<raw_session_token>`

---

## 2. API Endpoints

### 2.1. Retrieve Public Gym Information

Retrieves public branding, logo, payment methods, and checkout settings for a gym.

- **Method**: `GET`
- **Path**: `/gym/{tenant_id}/`
- **Parameters**: `tenant_id` (subdomain or tenant UUID)

#### Example Request:
```bash
curl -X GET "https://api.fitverx.com/api/v1/checkout/gym/irontemple/"
```

#### Success Response (`200 OK`):
```json
{
  "id": "8e89e815-7e7a-495f-9603-2722b6e3ef06",
  "name": "Iron Temple Fitness",
  "subdomain": "irontemple",
  "logo_url": "https://api.fitverx.com/media/logos/irontemple.png",
  "branding": {
    "primary_color": "#1A56DB"
  },
  "currency": "usd",
  "available_payment_methods": ["card", "apple_pay", "google_pay"],
  "checkout_settings": {
    "require_phone_number": false,
    "terms_url": "https://irontemple.com/terms",
    "privacy_policy_url": "https://irontemple.com/privacy",
    "success_redirect_url": "https://irontemple.com/welcome",
    "cancel_redirect_url": "https://irontemple.com/pricing",
    "theme_primary_color": "#1A56DB",
    "meta_pixel_id": "123456789012345"
  }
}
```

---

### 2.2. Retrieve Available Membership Packages

Retrieves publicly purchasable membership packages enabled by the gym owner.

- **Method**: `GET`
- **Path**: `/gym/{tenant_id}/packages/`

#### Example Request:
```bash
curl -X GET "https://api.fitverx.com/api/v1/checkout/gym/irontemple/packages/"
```

#### Success Response (`200 OK`):
```json
[
  {
    "id": "bfaec75b-010e-49b8-bc73-d3c52a0a256a",
    "name": "Unlimited Monthly Membership",
    "description": "30 credits valid for 30 days",
    "price": "99.00",
    "currency": "usd",
    "billing_cycle": "monthly",
    "credit_count": 30,
    "validity_days": 30,
    "location_id": "c1f7f0c5-59b4-48ff-98c4-54157ce94ff4",
    "location_name": "Downtown Flagship",
    "location_address": "100 Main St, Suite 400"
  }
]
```

---

### 2.3. Create Checkout Session

Initiates a checkout session, takes a price snapshot, generates a 256-bit session token, and dispatches an email OTP.

- **Method**: `POST`
- **Path**: `/sessions/`
- **Headers**: `Content-Type: application/json`

#### Request Body:
```json
{
  "tenant_id": "irontemple",
  "package_type_id": "bfaec75b-010e-49b8-bc73-d3c52a0a256a",
  "email": "sarah.connor@example.com",
  "first_name": "Sarah",
  "last_name": "Connor",
  "phone_number": "+14155552671",
  "fbp": "fb.1.1596403882.1767243688",
  "fbc": "fb.1.1596403882.IwAR0example",
  "source_url": "https://irontemple.com/pricing"
}
```

#### Success Response (`201 Created`):
```json
{
  "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
  "session_token": "8df82b793730245e143fa681f215f79aa803ec2ff4579cbb5c81fce77a94119d",
  "expires_at": "2026-11-09T15:45:00Z",
  "is_verified": false,
  "otp_cooldown_seconds": 60,
  "message": "Verification code sent to your email."
}
```

---

### 2.4. Verify Session OTP

Validates the 6-digit OTP code sent to the customer's email.

- **Method**: `POST`
- **Path**: `/sessions/{session_id}/otp/verify/`
- **Headers**:
  - `Content-Type: application/json`
  - `X-Checkout-Token: <session_token>`

#### Request Body:
```json
{
  "otp_code": "481920"
}
```

#### Success Response (`200 OK`):
```json
{
  "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
  "is_verified": true,
  "status": "READY_FOR_PAYMENT",
  "message": "Email verified successfully."
}
```

---

### 2.5. Resend Session OTP

Sends a new 6-digit OTP to the customer's email. Enforces a 60-second cooldown.

- **Method**: `POST`
- **Path**: `/sessions/{session_id}/otp/resend/`
- **Headers**:
  - `X-Checkout-Token: <session_token>`

#### Success Response (`200 OK`):
```json
{
  "detail": "A new verification code has been sent to your email.",
  "expires_in_seconds": 300,
  "cooldown_seconds": 60
}
```

---

### 2.6. Retrieve Session Details

Retrieves the current state of the session.

- **Method**: `GET`
- **Path**: `/sessions/{session_id}/`
- **Headers**:
  - `X-Checkout-Token: <session_token>`

#### Success Response (`200 OK`):
```json
{
  "id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
  "status": "READY_FOR_PAYMENT",
  "customer_email": "sarah.connor@example.com",
  "first_name": "Sarah",
  "last_name": "Connor",
  "phone_number": "+14155552671",
  "price_snapshot": "99.00",
  "currency": "usd",
  "is_verified": true,
  "payment_status": "unpaid",
  "expires_at": "2026-11-09T15:45:00Z",
  "created_at": "2026-11-09T15:15:00Z",
  "package": {
    "id": "bfaec75b-010e-49b8-bc73-d3c52a0a256a",
    "name": "Unlimited Monthly Membership",
    "description": "30 credits valid for 30 days",
    "price": "99.00",
    "currency": "usd",
    "billing_cycle": "monthly",
    "credit_count": 30,
    "validity_days": 30,
    "location_id": "c1f7f0c5-59b4-48ff-98c4-54157ce94ff4",
    "location_name": "Downtown Flagship",
    "location_address": "100 Main St, Suite 400"
  }
}
```

---

### 2.7. Initiate Payment

Validates that OTP verification passed, verifies price against Stripe platform catalog, and creates a Stripe Checkout session.

- **Method**: `POST`
- **Path**: `/sessions/{session_id}/initiate-payment/`
- **Headers**:
  - `Content-Type: application/json`
  - `X-Checkout-Token: <session_token>`

#### Request Body *(Optional)*:
```json
{
  "return_url": "https://checkout.fitverx.com/order-summary",
  "cancel_url": "https://checkout.fitverx.com/checkout"
}
```

#### Success Response (`200 OK`):
```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_a1b2c3d4...",
  "stripe_session_id": "cs_live_a1b2c3d4...",
  "publishable_key": "pk_live_51..."
}
```

---

### 2.8. Poll Payment Status

Polls payment completion while awaiting asynchronous Stripe webhook fulfillment.

- **Method**: `GET`
- **Path**: `/sessions/{session_id}/payment-status/`
- **Headers**:
  - `X-Checkout-Token: <session_token>`

#### Success Response (`200 OK`):
```json
{
  "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
  "payment_status": "paid",
  "checkout_status": "COMPLETED",
  "is_completed": true,
  "completed_at": "2026-11-09T15:20:00Z"
}
```

---

### 2.9. Retrieve Order Completion Summary

Retrieves post-payment details and onboarding flag.

- **Method**: `GET`
- **Path**: `/sessions/{session_id}/completion/`
- **Headers**:
  - `X-Checkout-Token: <session_token>`

#### Success Response (`200 OK`):
```json
{
  "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
  "gym_name": "Iron Temple Fitness",
  "package_name": "Unlimited Monthly Membership",
  "credits": 30,
  "amount_paid": "99.00",
  "currency": "usd",
  "customer_email": "sarah.connor@example.com",
  "customer_name": "Sarah Connor",
  "package_expires_at": "2026-12-09T15:20:00Z",
  "needs_password_setup": true,
  "mobile_app_download_url": "https://fitverx.com/app"
}
```

---

### 2.10. Customer Onboarding Password Setup

Sets customer mobile app password and returns SimpleJWT tokens.

- **Method**: `POST`
- **Path**: `/sessions/{session_id}/set-password/`
- **Headers**:
  - `Content-Type: application/json`
  - `X-Checkout-Token: <session_token>`

#### Request Body:
```json
{
  "password": "StrongPassword123!",
  "confirm_password": "StrongPassword123!"
}
```

#### Success Response (`200 OK`):
```json
{
  "message": "Password set successfully. You can now log in to the Fitverx mobile app.",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

---

## 3. Subscription & Mobile Platform Features API (Gym Owner / Admin)

Endpoints for retrieving gym tenant subscription status and managing platform-specific feature gates (`android` and `ios`).

### 3.1. Retrieve Current Subscription & Platform Features

Returns the active subscription plan, entitlements, and the `android` and `ios` platform feature objects. By default, both platforms inherit all active plan features with `is_customized: false`.

- **Method**: `GET`
- **Path**: `/api/v1/payments/billing/subscription/`
- **Headers**:
  - `Authorization: Bearer <gym_owner_jwt_access_token>`

#### Success Response (`200 OK`):
```json
[
  {
    "id": "7f8a9b1c-2d3e-4f5a-6b7c-8d9e0f1a2b3c",
    "billing_plan": {
      "id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
      "name": "Standard Plan",
      "slug": "standard",
      "allowed_feature_count": 5,
      "is_public": true
    },
    "active_features": [
      {
        "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
        "name": "Class Booking",
        "code": "class_booking",
        "description": "Enables group class scheduling",
        "price": "29.00",
        "billing_cycle": "monthly",
        "is_active": true,
        "platform_is_active": true,
        "is_entitled": true,
        "current_period_end": "2026-12-09T00:00:00Z"
      }
    ],
    "android": {
      "platform": "android",
      "is_customized": false,
      "features": [
        {
          "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
          "name": "Class Booking",
          "code": "class_booking",
          "description": "Enables group class scheduling",
          "price": "29.00",
          "billing_cycle": "monthly",
          "is_active": true,
          "platform_is_active": true,
          "is_entitled": true,
          "current_period_end": "2026-12-09T00:00:00Z"
        }
      ],
      "active_features": [
        {
          "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
          "name": "Class Booking",
          "code": "class_booking",
          "description": "Enables group class scheduling",
          "price": "29.00",
          "billing_cycle": "monthly",
          "is_active": true,
          "platform_is_active": true,
          "is_entitled": true,
          "current_period_end": "2026-12-09T00:00:00Z"
        }
      ],
      "entitlements": {
        "class_booking": true
      },
      "feature_codes": [
        "class_booking"
      ]
    },
    "ios": {
      "platform": "ios",
      "is_customized": false,
      "features": [
        {
          "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
          "name": "Class Booking",
          "code": "class_booking",
          "description": "Enables group class scheduling",
          "price": "29.00",
          "billing_cycle": "monthly",
          "is_active": true,
          "platform_is_active": true,
          "is_entitled": true,
          "current_period_end": "2026-12-09T00:00:00Z"
        }
      ],
      "active_features": [
        {
          "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
          "name": "Class Booking",
          "code": "class_booking",
          "description": "Enables group class scheduling",
          "price": "29.00",
          "billing_cycle": "monthly",
          "is_active": true,
          "platform_is_active": true,
          "is_entitled": true,
          "current_period_end": "2026-12-09T00:00:00Z"
        }
      ],
      "entitlements": {
        "class_booking": true
      },
      "feature_codes": [
        "class_booking"
      ]
    },
    "status": "active",
    "current_period_end": "2026-12-09T00:00:00Z",
    "cancel_at_period_end": false,
    "is_entitled": true
  }
]
```

---

### 3.2. Manage Platform Features

Customizes, adds, removes, or resets feature access specifically for Android or iOS.

- **Method**: `PATCH` / `POST` / `GET`
- **Path**: `/api/v1/payments/billing/subscription/platform-features/`
- **Alternative Path (by ID)**: `/api/v1/payments/billing/subscription/{subscription_id}/platform-features/`
- **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <gym_owner_jwt_access_token>`

#### Usage 1: Explicit Platform Replacement (`PATCH`)
Replaces the feature list for a given platform.
```json
{
  "platform": "android",
  "features": ["class_booking", "workout_tracking"]
}
```

#### Usage 2: Bulk Multi-Platform Replacement (`PATCH`)
Sets both platforms simultaneously.
```json
{
  "android": ["class_booking", "workout_tracking"],
  "ios": ["class_booking"]
}
```

#### Usage 3: Add a Single Feature to a Platform (`POST`)
Adds one feature to an existing platform list.
```json
{
  "platform": "android",
  "action": "add",
  "feature": "workout_tracking"
}
```

#### Usage 4: Remove a Single Feature from a Platform (`POST`)
Removes one feature from a platform list.
```json
{
  "platform": "android",
  "action": "remove",
  "feature": "workout_tracking"
}
```

#### Usage 5: Reset Platform Back to Subscription Defaults (`POST`)
Clears the custom override so the platform inherits all tenant subscription features again.
```json
{
  "platform": "android",
  "action": "reset"
}
```

#### Success Response (`200 OK`):
```json
{
  "subscription_id": "7f8a9b1c-2d3e-4f5a-6b7c-8d9e0f1a2b3c",
  "android": {
    "platform": "android",
    "is_customized": true,
    "features": [
      {
        "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6e",
        "name": "Class Booking",
        "code": "class_booking",
        "description": "Enables group class scheduling",
        "price": "29.00",
        "billing_cycle": "monthly",
        "is_active": true,
        "platform_is_active": true,
        "is_entitled": true,
        "current_period_end": "2026-12-09T00:00:00Z"
      }
    ],
    "active_features": [],
    "entitlements": {
      "class_booking": true
    },
    "feature_codes": [
      "class_booking"
    ]
  },
  "ios": {
    "platform": "ios",
    "is_customized": false,
    "features": [],
    "active_features": [],
    "entitlements": {},
    "feature_codes": []
  }
}
```
