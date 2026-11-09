  # Fitverx Website Checkout — Frontend Developer Integration Manual

  **Audience:** Frontend Engineers building the standalone Fitverx Checkout Application (`checkout.fitverx.com`).  
  **API Collection Reference:** [`docs/fitverx_checkout_postman_collection.json`](file:///Users/ansmustafa/gym-app-backend/docs/fitverx_checkout_postman_collection.json)  
  **Backend API Base URL:** `https://api.fitverx.com/api/v1/checkout` (Local: `http://localhost:8000/api/v1/checkout`)

  ---

  ## 1. Introduction

  ### What is Fitverx?
  Fitverx is a cloud-based SaaS management platform built specifically for gyms, fitness studios, and wellness centers. It provides gym owners with a unified system to manage members, memberships, class schedules, bookings, billing, access control, and native iOS/Android mobile apps for gym members.

  ### What is the Website Checkout Integration?
  Fitverx Website Checkout is a headless, decoupled e-commerce solution that allows gym owners to sell membership packages directly from their existing marketing websites (such as WordPress, Webflow, Squarespace, or custom landing pages) without having to build custom registration forms, maintain merchant accounts, or code backend logic.

  ### Why Was This System Developed?
  Gym owners spend advertising budgets on Meta (Facebook/Instagram) and Google Ads, driving traffic to external marketing websites. Previously, gyms either had to:
  - Direct visitors to download the Fitverx mobile app before making a purchase (introducing massive drop-off).
  - Build insecure, one-off web forms connected to disjointed payment tools that didn't automatically provision memberships or synchronize with member check-in scanners.

  The Website Checkout Integration solves this by providing a frictionless, web-first checkout funnel that captures leads, verifies email ownership, processes credit card/Apple Pay transactions, and instantly provisions membership credentials into the member's mobile app account.

  ### Why is the Checkout Frontend Deployed Separately?
  The checkout frontend is deployed as an independent Next.js application at `checkout.fitverx.com` rather than being server-rendered inside the Django backend. This decoupling provides:
  - **Instant Global Page Loads**: Static frontend assets and edge rendering provide sub-second load times for ad-driven mobile traffic.
  - **Gym-Branded White-Labeling**: The frontend can dynamically theme itself (colors, logos, gym identity) based on URL parameters (`?gym=subdomain`) without touching backend servers.
  - **Strict Security Isolation**: The public web interface has zero direct access to backend databases or internal mobile application administrative endpoints.
  - **Independent Deployment Lifecycles**: UI improvements, A/B testing, and conversion optimizations can be shipped continuously without redeploying the core Django backend.

  ### Architecture & Separation of Responsibilities

  ```mermaid
  flowchart LR
      subgraph External["Gym Website / Ads"]
          Site["Gym Website<br/>(Embed Widget / Link)"]
      end

      subgraph FrontendApp["Frontend: checkout.fitverx.com"]
          UI["Next.js Web App<br/>(Package UI, OTP, Stripe Form)"]
      end

      subgraph BackendAPI["Backend: Django REST API"]
          API["Public Checkout API<br/>(/api/v1/checkout/)"]
          Auth["Auth & Multi-Tenant<br/>Isolation Layer"]
          StripeService["Stripe Payment & Webhook<br/>Fulfillment Engine"]
      end

      subgraph ThirdParty["External Services"]
          Stripe["Stripe Gateway"]
          Email["SMTP Email Service"]
      end

      Site -->|Redirect / Open| UI
      UI -->|REST JSON + X-Checkout-Token| API
      API --> Auth
      API --> StripeService
      StripeService --> Stripe
      API --> Email
  ```

  | Component | Responsibilities |
  | :--- | :--- |
  | **Frontend (`checkout.fitverx.com`)** | UI rendering, gym branding adaptation, package selection state, collecting customer input, OTP challenge entry, displaying Stripe Elements / redirects, polling payment status, and presenting the post-purchase password setup form. |
  | **Backend (Fitverx Django)** | Authoritative price calculation, tenant identification and isolation, session token generation (hashed at rest), OTP generation and rate limiting, Stripe checkout session creation, webhook fulfillment, user provisioning, cross-tenant identity linking, and ledger accounting. |

  ---

  ## 2. Complete Product Overview & Customer Journey

  1. **Gym Setup**: The gym owner creates membership packages in their Fitverx admin portal and enables online checkout.
  2. **Website Integration**: The gym embeds the Fitverx checkout widget or adds a "Join Now" button linking to `https://checkout.fitverx.com/?gym=<subdomain>`.
  3. **Ad Click & Visit**: A prospect clicks a Facebook/Instagram ad and lands on the gym's website or checkout page.
  4. **Dynamic Loading**: The frontend calls `GET /gym/{subdomain}/` and `GET /gym/{subdomain}/packages/` to load the gym's logo, primary color, currency, and available packages.
  5. **Package Selection**: The customer chooses a package (e.g., "Unlimited Monthly - $99/mo") and enters their name, email, and optional phone number.
  6. **Session Creation**: The frontend calls `POST /sessions/`. The backend locks the price snapshot, generates a 256-bit cryptographically secure session token, and emails a 6-digit OTP code to the customer.
  7. **Email Verification**: The customer enters the 6-digit OTP. The frontend calls `POST /sessions/{id}/otp/verify/`. The backend verifies the code and unlocks the payment stage.
  8. **Payment Initiation**: The frontend calls `POST /sessions/{id}/initiate-payment/`. The backend verifies authoritative pricing against the Stripe catalog and returns a Stripe Checkout URL.
  9. **Customer Payment**: The customer enters payment details via Stripe (Credit Card, Apple Pay, Google Pay).
  10. **Server-Side Webhook Verification**: Stripe sends a signed `checkout.session.completed` event to the Fitverx backend webhook. The backend verifies payment idempotency, asserts that the charged amount matches the session price snapshot, and locks the session.
  11. **Account Identification / Provisioning**: The backend checks if the email already belongs to a Fitverx user:
      - *New Customer*: Creates a `User` record with an unusable password and grants a `TenantCustomerAccess` record for this gym with role `CLIENT`.
      - *Existing Customer*: Safely links the existing `User` to this gym via `TenantCustomerAccess` without violating global email uniqueness.
  12. **Membership Activation**: The backend creates an active `Package` record for the member, allocating credits and setting expiry dates.
  13. **Order Completion & Onboarding**: The frontend polls `GET /sessions/{id}/payment-status/`, detects completion, and routes the user to `/completion`. If the customer is new, the frontend displays an inline form allowing them to set their password via `POST /sessions/{id}/set-password/`.
  14. **Mobile App Handover**: The customer downloads the Fitverx mobile app (iOS/Android) and logs in immediately with their email and password to scan their barcode at the gym front desk.

  ---

  ## 3. User Stories & Edge Case Scenarios

  ### Story 1: New Customer Purchasing a Membership
  - **Goal**: A new gym visitor wants to buy a monthly membership online and start attending classes.
  - **Starting Conditions**: No prior Fitverx account exists with this email address.
  - **Actions**: Selects package $\to$ enters email and name $\to$ verifies OTP $\to$ completes Stripe payment $\to$ enters new password on confirmation screen.
  - **Expected Frontend Behaviour**: Guides user smoothly from package selection through OTP, Stripe, and displays the "Create Your Password" form on the final confirmation screen.
  - **Backend Operations**: Locks price, verifies OTP, fulfills payment via webhook, creates `User` (unusable password), links `TenantCustomerAccess`, creates `Package`, sets password upon submission, and returns SimpleJWT access/refresh tokens.
  - **Outcome**: Customer is fully active in the gym database and can log into the mobile app immediately.

  ### Story 2: Existing Fitverx Customer Purchasing at Another Gym
  - **Goal**: A customer who already uses Fitverx at Gym A buys a class pack at Gym B.
  - **Starting Conditions**: Customer `User` record exists in Fitverx with their email.
  - **Actions**: Selects package at Gym B $\to$ enters existing email $\to$ verifies OTP $\to$ completes payment.
  - **Expected Frontend Behaviour**: Final confirmation screen recognizes the existing account (`needs_password_setup: false`), displays a "Welcome Back" message, and instructs them to open their existing Fitverx app where Gym B is now accessible.
  - **Backend Operations**: Webhook fulfillment locates the existing `User` record and creates a new `TenantCustomerAccess` row for Gym B. Existing password remains unchanged.
  - **Outcome**: Customer logs in with their existing credentials and can switch between Gym A and Gym B in the mobile app.

  ### Story 3: Customer Verifying Email via OTP
  - **Goal**: Prove ownership of the entered email address before payment is allowed.
  - **Starting Conditions**: Session created; OTP sent via email.
  - **Actions**: User checks inbox, types 6-digit code into input boxes.
  - **Expected Frontend Behaviour**: Auto-submits on 6th digit. If incorrect, highlights error message and shows remaining attempts. Displays a 60-second countdown for the "Resend Code" button.
  - **Backend Operations**: Compares SHA-256 hash of entered code against session `otp_hash`. Increments `otp_attempts`. Locks out session if attempts reach 5.

  ### Story 4: Customer Experiencing Payment Failure or Cancellation
  - **Goal**: Customer attempts payment, but their card is declined or they cancel on Stripe.
  - **Starting Conditions**: Session verified; user redirected to Stripe.
  - **Actions**: Card is declined on Stripe or user clicks "Back / Cancel".
  - **Expected Frontend Behaviour**: Customer is redirected back to the frontend checkout screen with an explanatory banner: *"Your payment was not completed. Please try again or use a different payment method."* User does not have to re-verify their email.
  - **Backend Operations**: Session remains in `READY_FOR_PAYMENT` state with `payment_status: "unpaid"`. No package or user is provisioned.
  - **Alternative**: User can click "Pay Now" again to generate a new Stripe session without re-entering their details.

  ### Story 5: Customer Returning to an Incomplete Checkout
  - **Goal**: A customer accidentally refreshes the browser or returns from Stripe.
  - **Starting Conditions**: `session_id` and `session_token` stored in browser `sessionStorage`.
  - **Actions**: Customer re-opens or refreshes `checkout.fitverx.com`.
  - **Expected Frontend Behaviour**: Frontend detects stored token, calls `GET /sessions/{session_id}/`, restores session state, and skips previously completed steps (e.g., jumps straight to payment if already OTP-verified).
  - **Backend Operations**: Returns current session state and validates that `expires_at > now()`.

  ### Story 6: Customer Whose Checkout Session Expires
  - **Goal**: Customer abandons tab for 35 minutes and returns to complete checkout.
  - **Starting Conditions**: Session duration exceeds 30 minutes.
  - **Actions**: Customer attempts to verify OTP or initiate payment.
  - **Expected Frontend Behaviour**: API returns HTTP 400 (`"This checkout session has expired. Please create a new session."`). Frontend clears `sessionStorage` and displays a graceful alert: *"Your checkout session timed out for your security. Please select your package again."*
  - **Backend Operations**: Session marked as `EXPIRED`. Requests rejected.

  ### Story 7: Package Becomes Inactive During Checkout
  - **Goal**: Gym owner unpublishes a package while a customer has it in an active checkout session.
  - **Starting Conditions**: Package `is_active` set to `False` by gym staff.
  - **Actions**: Customer clicks "Proceed to Payment".
  - **Expected Frontend Behaviour**: API returns HTTP 400 with `code: "PACKAGE_UNAVAILABLE"`. Frontend displays: *"This membership package is no longer available. Please select an alternate package."*

  ---

  ## 4. API Integration Reference

  All requests must be made against the base path: `/api/v1/checkout/`  
  All session-authenticated requests require the header:
  ```http
  X-Checkout-Token: <session_token>
  ```

  ---

  ### API 1: Retrieve Public Gym Information
  - **Collection Folder**: `1. Gym Information & Packages`
  - **Request Name**: `Retrieve Public Gym Information`
  - **Endpoint**: `GET /api/v1/checkout/gym/{tenant_id}/`
  - **Method**: `GET`
  - **Purpose**: Retrieves gym branding (colors, logo), supported payment methods, and legal/redirect settings.
  - **When to Call**: On initial page load when reading the `?gym=` query parameter from the URL.
  - **Headers**: None.
  - **Path Parameters**: `tenant_id` — Gym subdomain string (e.g., `alpha`) or Gym UUID.
  - **Request Body**: None.

  #### Successful Response (`200 OK`):
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

  #### Error Responses:
  - `404 Not Found`:
  ```json
  {
    "error": "Gym not found or online checkout is currently disabled."
  }
  ```

  #### Frontend Action:
  - Apply `branding.primary_color` to buttons and accent UI elements.
  - Display gym `name` and `logo_url` in the checkout header.
  - Store `id` (tenant UUID) and `currency` in global app state.
  - If `404`, display an error screen informing the user that checkout is unavailable for this gym.

  ---

  ### API 2: Retrieve Available Packages
  - **Collection Folder**: `1. Gym Information & Packages`
  - **Request Name**: `Retrieve Available Packages`
  - **Endpoint**: `GET /api/v1/checkout/gym/{tenant_id}/packages/`
  - **Method**: `GET`
  - **Purpose**: Retrieves the list of active, purchasable membership packages configured for this gym.
  - **When to Call**: Immediately after loading gym information.
  - **Headers**: None.
  - **Path Parameters**: `tenant_id` — Gym subdomain or UUID.
  - **Request Body**: None.

  #### Successful Response (`200 OK`):
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

  #### Frontend Action:
  - Render package cards displaying name, price, billing frequency, and description.
  - If pre-selected via query param (`?package=<id>`), automatically select that card.

  ---

  ### API 3: Create Checkout Session
  - **Collection Folder**: `2. Session Lifecycle & Verification`
  - **Request Name**: `Create Checkout Session`
  - **Endpoint**: `POST /api/v1/checkout/sessions/`
  - **Method**: `POST`
  - **Purpose**: Locks the selected package price, generates a 256-bit session token, creates a pending session in the database, and dispatches a 6-digit OTP to the customer's email.
  - **When to Call**: When the user clicks "Continue" after selecting a package and entering their email and name.
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

  #### Successful Response (`201 Created`):
  ```json
  {
    "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
    "session_token": "8df82b793730245e143fa681f215f79aa803ec2ff4579cbb5c81fce77a94119d",
    "expires_at": "2026-11-09T16:15:00Z",
    "is_verified": false,
    "otp_cooldown_seconds": 60,
    "message": "Verification code sent to your email."
  }
  ```

  #### Error Responses:
  - `400 Bad Request` (Existing Active Membership):
  ```json
  {
    "error": "You already have an active subscription for this package.",
    "code": "ALREADY_SUBSCRIBED_TO_PACKAGE"
  }
  ```
  - `400 Bad Request` (Invalid Input):
  ```json
  {
    "email": ["Enter a valid email address."]
  }
  ```

  #### Frontend Action:
  - Store `session_id` and `session_token` in `sessionStorage`.
  - Advance user to the OTP verification screen.
  - Start a 60-second cooldown timer for the "Resend OTP" button.

  ---

  ### API 4: Verify Session OTP
  - **Collection Folder**: `2. Session Lifecycle & Verification`
  - **Request Name**: `Verify Session OTP`
  - **Endpoint**: `POST /api/v1/checkout/sessions/{session_id}/otp/verify/`
  - **Method**: `POST`
  - **Purpose**: Validates the 6-digit OTP code sent to the customer's email.
  - **When to Call**: When the user enters the complete 6-digit code.
  - **Headers**:
    - `Content-Type: application/json`
    - `X-Checkout-Token: <session_token>`

  #### Request Body:
  ```json
  {
    "otp_code": "481920"
  }
  ```

  #### Successful Response (`200 OK`):
  ```json
  {
    "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
    "is_verified": true,
    "status": "READY_FOR_PAYMENT",
    "message": "Email verified successfully."
  }
  ```

  #### Error Responses:
  - `400 Bad Request` (Wrong Code):
  ```json
  {
    "detail": "Invalid verification code."
  }
  ```
  - `400 Bad Request` (Locked Out):
  ```json
  {
    "detail": "Maximum verification attempts exceeded. Please request a new code."
  }
  ```

  #### Frontend Action:
  - On success, transition the UI to the Payment stage.
  - On error, clear the OTP inputs, display error message, and shake the input field.

  ---

  ### API 5: Resend Session OTP
  - **Collection Folder**: `2. Session Lifecycle & Verification`
  - **Request Name**: `Resend Session OTP`
  - **Endpoint**: `POST /api/v1/checkout/sessions/{session_id}/otp/resend/`
  - **Method**: `POST`
  - **Purpose**: Generates and sends a new OTP code if the customer didn't receive the first one.
  - **When to Call**: When the customer clicks "Resend Code" after the 60-second cooldown expires.
  - **Headers**:
    - `X-Checkout-Token: <session_token>`

  #### Successful Response (`200 OK`):
  ```json
  {
    "detail": "A new verification code has been sent to your email.",
    "expires_in_seconds": 300,
    "cooldown_seconds": 60
  }
  ```

  #### Error Responses:
  - `400 Bad Request` (Cooldown active):
  ```json
  {
    "detail": "Please wait 45 seconds before requesting a new code."
  }
  ```

  #### Frontend Action:
  - Restart the 60-second countdown button.
  - Show toast notification: *"New code sent to your email."*

  ---

  ### API 6: Retrieve Session Details
  - **Collection Folder**: `2. Session Lifecycle & Verification`
  - **Request Name**: `Retrieve Session Details`
  - **Endpoint**: `GET /api/v1/checkout/sessions/{session_id}/`
  - **Method**: `GET`
  - **Purpose**: Retrieves the authoritative session state from the backend.
  - **When to Call**: On page refresh or when a user returns to an existing checkout session.
  - **Headers**:
    - `X-Checkout-Token: <session_token>`

  #### Successful Response (`200 OK`):
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
    "expires_at": "2026-11-09T16:15:00Z",
    "created_at": "2026-11-09T15:45:00Z",
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

  #### Frontend Action:
  - Restore user session state: if `is_verified` is `true`, bypass the OTP screen and show the Payment view.

  ---

  ### API 7: Initiate Payment
  - **Collection Folder**: `3. Payment & Completion`
  - **Request Name**: `Initiate Payment`
  - **Endpoint**: `POST /api/v1/checkout/sessions/{session_id}/initiate-payment/`
  - **Method**: `POST`
  - **Purpose**: Creates a Stripe Checkout Session with authoritative server-side price matching.
  - **When to Call**: When the user clicks "Proceed to Payment" on the verified checkout screen.
  - **Headers**:
    - `Content-Type: application/json`
    - `X-Checkout-Token: <session_token>`

  #### Request Body *(Optional)*:
  ```json
  {
    "return_url": "https://checkout.fitverx.com/success",
    "cancel_url": "https://checkout.fitverx.com/checkout"
  }
  ```

  #### Successful Response (`200 OK`):
  ```json
  {
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_a1b2c3d4...",
    "stripe_session_id": "cs_live_a1b2c3d4...",
    "publishable_key": "pk_live_51..."
  }
  ```

  #### Error Responses:
  - `400 Bad Request` (Not Verified):
  ```json
  {
    "detail": "Email verification is required before initiating payment."
  }
  ```

  #### Frontend Action:
  - Redirect the browser to `checkout_url` to load Stripe's hosted checkout (or initialize Stripe Elements with `stripe_session_id`).

  ---

  ### API 8: Poll Payment Status
  - **Collection Folder**: `3. Payment & Completion`
  - **Request Name**: `Poll Payment Status`
  - **Endpoint**: `GET /api/v1/checkout/sessions/{session_id}/payment-status/`
  - **Method**: `GET`
  - **Purpose**: Checks whether the asynchronous Stripe webhook has arrived and fulfilled the membership.
  - **When to Call**: When the user returns from Stripe to `/order-summary` or `/success`. Poll every 2 seconds (up to 15 retries / 30 seconds).
  - **Headers**:
    - `X-Checkout-Token: <session_token>`

  #### Successful Response (`200 OK` - Completed):
  ```json
  {
    "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
    "payment_status": "paid",
    "checkout_status": "COMPLETED",
    "is_completed": true,
    "completed_at": "2026-11-09T15:50:12Z"
  }
  ```

  #### Successful Response (`200 OK` - Awaiting Webhook):
  ```json
  {
    "session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
    "payment_status": "unpaid",
    "checkout_status": "PAYMENT_PENDING",
    "is_completed": false,
    "completed_at": null
  }
  ```

  #### Frontend Action:
  - Display a loading spinner: *"Confirming your membership with your bank..."*
  - When `is_completed: true`, route immediately to the completion view.
  - If timeout (30 seconds) is reached without completion, show a friendly fallback: *"Your payment is processing! Check your email in a few minutes for confirmation."*

  ---

  ### API 9: Retrieve Completion Summary
  - **Collection Folder**: `3. Payment & Completion`
  - **Request Name**: `Retrieve Completion Summary`
  - **Endpoint**: `GET /api/v1/checkout/sessions/{session_id}/completion/`
  - **Method**: `GET`
  - **Purpose**: Retrieves purchased package details, credit balance, and customer onboarding requirements.
  - **When to Call**: Upon navigating to the `/success` screen once payment is confirmed.
  - **Headers**:
    - `X-Checkout-Token: <session_token>`

  #### Successful Response (`200 OK`):
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
    "package_expires_at": "2026-12-09T15:50:12Z",
    "needs_password_setup": true,
    "mobile_app_download_url": "https://fitverx.com/app"
  }
  ```

  #### Frontend Action:
  - Display the purchase receipt card with gym name, package name, credits, and expiry.
  - If `needs_password_setup: true`, render the **"Set Your Password"** form directly on this screen.
  - If `needs_password_setup: false`, render a "Welcome Back" message with mobile app download buttons.

  ---

  ### API 10: Customer Password Setup
  - **Collection Folder**: `3. Payment & Completion`
  - **Request Name**: `Customer Password Setup`
  - **Endpoint**: `POST /api/v1/checkout/sessions/{session_id}/set-password/`
  - **Method**: `POST`
  - **Purpose**: Sets the password for newly created customer accounts directly from the checkout success page.
  - **When to Call**: When the user submits the password form on the completion screen.
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

  #### Successful Response (`200 OK`):
  ```json
  {
    "message": "Password set successfully. You can now log in to the Fitverx mobile app.",
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
  }
  ```

  #### Frontend Action:
  - Show success animation: *"Account Ready!"*
  - Display direct App Store / Google Play download badges.

  ---

  ## 5. Complete Frontend Integration Flow & Sequence Diagram

  ```mermaid
  sequenceDiagram
      autonumber
      actor Customer
      participant Frontend as Next.js Frontend
      participant Backend as Django API (/api/v1/checkout/)
      participant Email as SMTP Email Service
      participant Stripe as Stripe Gateway
      participant DB as PostgreSQL Database

      Customer->>Frontend: Visits checkout.fitverx.com/?gym=alpha
      Frontend->>Backend: GET /gym/alpha/
      Backend-->>Frontend: 200 OK (Gym profile, branding, logo)
      Frontend->>Backend: GET /gym/alpha/packages/
      Backend-->>Frontend: 200 OK (Active packages)

      Customer->>Frontend: Selects package & enters email/name
      Frontend->>Backend: POST /sessions/ (tenant_id, package_id, email, name)
      Backend->>Email: Dispatches 6-digit OTP code
      Backend-->>Frontend: 201 Created (session_id, session_token)
      Frontend->>Frontend: Saves session_token in sessionStorage

      Customer->>Frontend: Enters 6-digit OTP
      Frontend->>Backend: POST /sessions/{id}/otp/verify/ (X-Checkout-Token)
      Backend-->>Frontend: 200 OK (is_verified: true)

      Frontend->>Backend: POST /sessions/{id}/initiate-payment/ (X-Checkout-Token)
      Backend->>Stripe: Validates price snapshot parity & creates Stripe Session
      Backend-->>Frontend: 200 OK (checkout_url, stripe_session_id)

      Frontend->>Customer: Redirects to Stripe Checkout
      Customer->>Stripe: Submits payment (Card / Apple Pay)
      Stripe->>Backend: Webhook POST /payments/stripe/webhook/ (checkout.session.completed)
      Backend->>DB: Idempotency check, verify amount, create User, TCA & Package
      
      Customer->>Frontend: Returns to checkout.fitverx.com/order-summary
      loop Poll every 2s (up to 30s)
          Frontend->>Backend: GET /sessions/{id}/payment-status/ (X-Checkout-Token)
          Backend-->>Frontend: 200 OK (is_completed: true/false)
      end

      Frontend->>Backend: GET /sessions/{id}/completion/ (X-Checkout-Token)
      Backend-->>Frontend: 200 OK (receipt, needs_password_setup: true)

      alt If needs_password_setup == true
          Customer->>Frontend: Enters password & confirm password
          Frontend->>Backend: POST /sessions/{id}/set-password/ (X-Checkout-Token)
          Backend-->>Frontend: 200 OK (access_token, refresh_token)
          Frontend->>Customer: Shows "App Download" links
      end
  ```

  ---

  ## 6. Frontend State Management & Expected Behaviour

  ### Authoritative vs. UI-Only States

  | State | Location | Authoritative? | Explanation |
  | :--- | :--- | :--- | :--- |
  | **Package Price & Currency** | Backend | **YES** | The frontend must never calculate or submit prices. The backend price snapshot is the sole source of truth. |
  | **OTP Verification Status** | Backend | **YES** | The frontend must never unlock payment based on local validation. It must receive `is_verified: true` from the backend. |
  | **Payment Status** | Backend | **YES** | Only the backend webhook processing can mark a payment as `paid` and `COMPLETED`. |
  | **Selected Package Card** | Frontend | **NO** (UI only) | Which card is visually highlighted in the UI before submission. |
  | **OTP Cooldown Countdown** | Frontend | **NO** (UI only) | 60-second visual timer on the "Resend" button. |

  ### Session Persistence Across Browser Refresh
  Store the following in `sessionStorage` (scoped to the browser tab):
  ```json
  {
    "fitverx_session_id": "5cb89267-33e9-4e50-a9fe-e4f0c474d2fe",
    "fitverx_session_token": "8df82b793730245e143fa681f215f79aa803ec2ff4579cbb5c81fce77a94119d",
    "fitverx_tenant_id": "alpha"
  }
  ```
  When the app boots on `/checkout`:
  1. Check if `fitverx_session_id` and `fitverx_session_token` exist in `sessionStorage`.
  2. If present, call `GET /sessions/{fitverx_session_id}/`.
  3. If valid and not expired, hydrate state and restore the customer to their furthest completed step.
  4. If expired (`400` or `401`), call `sessionStorage.clear()` and show a fresh package selection screen.

  ---

  ## 7. Error Handling Guide

  | Error Scenario | HTTP Status | Backend Response Shape | Frontend Treatment |
  | :--- | :--- | :--- | :--- |
  | **Gym Not Found / Disabled** | `404` | `{"error": "Gym not found or online checkout is currently disabled."}` | Display a dedicated 404 page: *"This gym does not currently have online checkout enabled."* |
  | **Duplicate Membership** | `400` | `{"error": "You already have an active subscription for this package.", "code": "ALREADY_SUBSCRIBED_TO_PACKAGE"}` | Show inline modal: *"You already have this active membership. Log in to your mobile app to manage your plan."* |
  | **Invalid Email Format** | `400` | `{"email": ["Enter a valid email address."]}` | Highlight email field in red with field validation message. |
  | **Invalid OTP Code** | `400` | `{"detail": "Invalid verification code."}` | Shake OTP input boxes, clear digits, and display error text. |
  | **OTP Lockout (5 Fails)** | `400` | `{"detail": "Maximum verification attempts exceeded. Please request a new code."}` | Disable OTP input boxes. Highlight the "Resend Code" button. |
  | **Session Expired** | `400` / `401` | `{"detail": "This checkout session has expired. Please create a new session."}` | Clear `sessionStorage`, show banner: *"Your session timed out. Please select your package again."*, return to Step 1. |
  | **Price Tampering** | `400` | `{"detail": "Price discrepancy detected between platform catalog and checkout session."}` | Alert user: *"Pricing has recently changed. Please refresh the page to view current rates."* |
  | **Payment Cancelled** | N/A (Redirect) | Query param `?canceled=true` | Restore checkout view at Step 3 (Payment) with banner: *"Payment was cancelled. You have not been charged."* |
  | **Network Failure** | `0` (Client) | Fetch exception / timeout | Show retry button: *"Network connection lost. Tap to retry."* |

  ---

  ## 8. Security & Integration Requirements

  1. **Token Storage**:
    - Store the raw `session_token` only in memory or `sessionStorage`.
    - Never store `session_token` in `localStorage` (which persists indefinitely across browser restarts) or in cookies accessible to other domains.
  2. **CORS Headers**:
    - The Django backend is pre-configured with `CORS_ALLOW_HEADERS` including `x-checkout-token` and `x-tenant-id`. Ensure your API client passes `X-Checkout-Token` in exact kebab-case.
  3. **No Direct Secret Keys**:
    - The frontend must never handle Stripe secret keys or internal Django staff tokens. All Stripe session generation happens on the server.
  4. **Meta Pixel & Tracking Cookies**:
    - Extract `_fbp` and `_fbc` cookies from the browser document if present and pass them in `POST /sessions/`. The Fitverx backend automatically forwards server-side Meta Conversions API events (`InitiateCheckout` and `Purchase`) without relying exclusively on ad blockers in the browser.

  ---

  ## 9. Frontend Implementation Checklist

  - [ ] **Environment Setup**: Define `NEXT_PUBLIC_API_BASE_URL` pointing to backend API.
  - [ ] **Dynamic Gym Detection**: Extract `?gym=<subdomain>` from URL or fallback to subpath routing.
  - [ ] **Branding Hydration**: Apply `branding.primary_color` to buttons and spinners dynamically.
  - [ ] **Session Token Storage**: Manage `sessionStorage` read/write for seamless page refresh recovery.
  - [ ] **OTP Input UX**: 6-box segmented OTP component with auto-focus, backspace jumping, and paste handling.
  - [ ] **OTP Cooldown Timer**: 60-second visual countdown on Resend button with disabled state.
  - [ ] **Stripe Redirection**: Handle return URL with query parameters (`?session_id=...`).
  - [ ] **Webhook Status Polling**: Exponential backoff polling loop (2s interval, 15 retries max).
  - [ ] **Password Strength Meter**: Validate password length ($\ge 8$ chars) on post-purchase screen.
  - [ ] **JWT Handover**: Securely capture `access_token` and `refresh_token` upon password creation.
  - [ ] **Mobile Responsiveness**: Test touch targets and viewport layout on iPhone Safari and Android Chrome.

  ---

  ## 10. Final Integration Acceptance Criteria

  The frontend implementation is accepted as complete when:
  1. Navigating to `https://checkout.fitverx.com/?gym=alpha` correctly loads Alpha Gym's logo, primary color, and available packages.
  2. Selecting a package and submitting valid customer information creates a session and dispatches an OTP email within 3 seconds.
  3. Entering an incorrect OTP displays an error without resetting customer name/email.
  4. Entering a correct OTP immediately transitions to the payment step.
  5. Initiating payment routes the customer to the Stripe checkout experience.
  6. Completing payment on Stripe returns the customer to `/order-summary`, polls until confirmed, and presents the purchase receipt.
  7. Entering a new password activates the user account, returns valid JWT tokens, and displays mobile app download links.
  8. Refreshing the browser at any point before payment retains the current step without data loss.

