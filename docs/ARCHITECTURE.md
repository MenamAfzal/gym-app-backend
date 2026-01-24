# Architecture Decisions & Standards

## 1. Multi-Tenancy Strategy: Row-Level Isolation
**Decision:** Shared Database, Shared Schema.
**Reasoning:** - Simplifies infrastructure management (no need to run migrations on 1000+ schemas).
- Enables cross-tenant analytics if needed in the future.
- Reduces connection pooling overhead compared to separate DBs.

**Enforcement:**
- All tenant-specific models **MUST** inherit from `TenantAwareModel`.
- The `tenant_id` column is a Foreign Key to `apps.core.tenants.models.Tenant`.
- `TenantAwareManager` automatically filters queries by the active tenant context.
- **NEVER** bypass the manager unless writing a specific administrative script.

## 2. Primary Keys: UUIDs
**Decision:** All models use UUIDv4 as primary keys.
**Reasoning:**
- Prevents ID enumeration attacks (scanning `/api/users/1`, `/api/users/2`).
- Allows ID generation in the application layer before DB insertion.
- Safe for distributed systems/sharding if we ever split the DB.

**Enforcement:**
- All models must inherit from `core_models.base_models.BaseModel` (or `TenantAwareModel`).
- Do NOT use standard Django `AutoField` or `BigAutoField` for business entities.

## 3. Caching Strategy
- **Entitlements:** Cached in Redis with 5-minute TTL (invalidated on write).
- **Sessions:** Stored in Redis (not Database) to reduce I/O on the primary DB.
- **Key Prefixing:** All cache keys must be namespaced (e.g., `tenant:{uuid}:feature:{name}`).

## 4. Background Tasks
- **Celery:** Must be used for any long-running process (email, reports, webhooks).
- **Tenant Context:** Tasks do NOT have a request object. You must explicitly pass `tenant_id` to the task and retrieve the Tenant instance to set the context if necessary.
