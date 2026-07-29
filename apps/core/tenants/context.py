"""
Tenant Context Manager

Handles thread-local storage for the active tenant using contextvars.
This allows models and managers to access the current tenant without
needing the Request object passed down the stack.
"""
from contextvars import ContextVar
from typing import Optional, Union
from uuid import UUID
from contextlib import contextmanager

# Context variable to hold the current tenant (or None)
_current_tenant: ContextVar[Optional[Union[UUID, object]]] = ContextVar('current_tenant', default=None)

# Context variable for bypassing tenant isolation (Super Admin Control Tower)
_bypass_isolation: ContextVar[bool] = ContextVar('bypass_isolation', default=False)

def get_current_tenant():
    """
    Get the active tenant for the current thread/request.
    Returns the Tenant instance (or object with ID) or None.
    """
    return _current_tenant.get()

def set_current_tenant(tenant):
    """
    Set the active tenant for the current thread/request.
    Returns a token that can be used to reset the context.
    """
    return _current_tenant.set(tenant)

def reset_current_tenant(token):
    """
    Reset the tenant context using the token from set_current_tenant.
    """
    _current_tenant.reset(token)

def is_isolation_bypassed() -> bool:
    """
    Returns True if the current context explicitly bypasses tenant isolation.
    """
    return _bypass_isolation.get()

@contextmanager
def bypass_tenant_isolation():
    """
    Context manager to execute code block without tenant query filtering.
    Crucial for cross-tenant operations by Platform Admins.
    """
    token = _bypass_isolation.set(True)
    try:
        yield
    finally:
        _bypass_isolation.reset(token)