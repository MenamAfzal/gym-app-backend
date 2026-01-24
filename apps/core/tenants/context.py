"""
Tenant Context Manager

Handles thread-local storage for the active tenant using contextvars.
This allows models and managers to access the current tenant without
needing the Request object passed down the stack.
"""
from contextvars import ContextVar
from typing import Optional, Union
from uuid import UUID

# Context variable to hold the current tenant (or None)
_current_tenant: ContextVar[Optional[Union[UUID, object]]] = ContextVar('current_tenant', default=None)

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
    