# Postman API Collection & Coverage Report

## Executive Summary

- **Target Coverage**: 100%
- **Discovered Endpoints**: 145
- **Collection Endpoints**: 145
- **Actual Coverage**: **100%**
- **Postman Collection JSON**: `postman_collection.json`
- **Postman Environment JSON**: `postman_environment.json`

---

## Deliverables Summary

1. **`postman_collection.json`**: Formatted according to Postman Collection v2.1.0 specification. Includes request headers, JSON body payloads, path parameters, query parameters, authentication headers (`Bearer {{access_token}}`), and test scripts for automated token capture.
2. **`postman_environment.json`**: Pre-configured Postman environment file containing variables for `base_url`, `access_token`, `refresh_token`, `tenant_id`, `user_id`, and entity IDs.
3. **`API_COVERAGE_REPORT.md`**: Complete documentation breakdown.

---

## Domain Folders & Endpoint Breakdown

| # | Folder Name | Requests | Key Functionality |
|---|---|---|---|
| 1 | **Authentication & User Management** | 22 | Registration OTP, JWT login/refresh/verify, password reset, profile management, staff creation, scheduling & nutrition summaries |
| 2 | **Platform Admin & Core Tenants** | 17 | Tenant provisioning, SaaS plans, feature toggles, platform financial ledger, analytics, Stripe webhooks |
| 3 | **Scheduling & Operations** | 34 | Gym locations, rooms, class templates, sessions, bookings, waitlists, substitute requests, credit packages, facility check-in/out |
| 4 | **Workout Management** | 17 | Workout builder, multi-level workouts, deck of cards, exercise catalog, video uploads, substitutions, exercise alternatives |
| 5 | **Food & Nutrition Logger** | 9 | Daily meal logs, custom foods, recipes, water intake, daily macro targets, staff recipes |
| 6 | **NutritionX Third-Party API Integration** | 5 | Natural language food search, beverage tracking, macro completion metrics |
| 7 | **Payments, Subscriptions & Financial Ledger** | 8 | Stripe Connect onboarding, SaaS checkout sessions, credit package checkout, payout triggers, tenant finance summary |
| 8 | **Social Network & Community Feed** | 6 | Feed stream, media upload, poll creation & voting, post likes, comments |
| 9 | **Mindset & Reflection Logger** | 6 | Daily mood & energy logs, female cycle tracking, AI quotes, streak metrics |
| 10 | **Inventory & Retail POS** | 4 | Retail product catalog, stock adjustment transactions |
| 11 | **Notifications Engine** | 12 | FCM device registration, campaign broadcasts, template management, group targeting, automations, inbox management |
| 12 | **Support Ticketing System** | 3 | Client support ticket submission and staff response message threads |
| 13 | **Client Health Assessments** | 2 | Body composition, weight, and body fat percentage tracking |

---

## Authentication & API Execution Flow

```text
POST /api/v1/users/auth/register/init/
  │
  ▼
POST /api/v1/users/auth/register/
  │
  ▼
POST /api/v1/users/auth/register/verify/  ───► (Auto-saves {{access_token}}, {{refresh_token}}, {{user_id}}, {{tenant_id}})
  │
  ▼
POST /api/v1/users/auth/login/            ───► (Alternative: Login to auto-populate tokens)
  │
  ▼
Authenticated Requests (e.g. GET /api/v1/users/profiles/me/, POST /api/v1/scheduling/bookings/)
```

### Automated Token Capture
The `1.4 Login (Obtain JWT Pair)` and `1.3 Verify OTP & Register` requests contain Postman test scripts that parse response payloads and populate environment variables:
```javascript
var jsonData = pm.response.json();
if (jsonData.access) {
    pm.environment.set("access_token", jsonData.access);
}
if (jsonData.refresh) {
    pm.environment.set("refresh_token", jsonData.refresh);
}
if (jsonData.user && jsonData.user.id) {
    pm.environment.set("user_id", jsonData.user.id);
}
if (jsonData.user && jsonData.user.tenant) {
    pm.environment.set("tenant_id", jsonData.user.tenant);
}
```

---

## How to Import into Postman

1. Open Postman.
2. Click **Import** (Top Left).
3. Drag & drop `postman_collection.json` and `postman_environment.json`.
4. In the upper-right corner of Postman, select the **Gym App Local Environment**.
5. Execute `1.4 Login (Obtain JWT Pair)` to authenticate and auto-fill your `access_token`.
