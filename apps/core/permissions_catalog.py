PERMISSION_CATALOG = {
    'scheduling': {
        'label': 'Scheduling & Timetable',
        'resources': {
            'classes': {
                'label': 'Classes & Templates',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'sessions': {
                'label': 'Class Sessions',
                'actions': ['view', 'create', 'edit', 'delete', 'cancel_session']
            },
            'events': {
                'label': 'Workshops & Events',
                'actions': ['view', 'create', 'edit', 'delete', 'cancel_event', 'roster', 'check_in']
            },
            'rooms': {
                'label': 'Rooms & Layouts',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'spot_types': {
                'label': 'Spot Types',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'packages': {
                'label': 'Packages & Passes',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'bookings': {
                'label': 'Bookings',
                'actions': ['view', 'create', 'edit', 'delete', 'check_in']
            },
            'waitlist': {
                'label': 'Waitlist',
                'actions': ['view', 'edit', 'delete']
            },
            'staff_availability': {
                'label': 'Staff Availability',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'workout': {
        'label': 'Workouts & Exercises',
        'resources': {
            'workouts': {
                'label': 'Workouts',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'exercises': {
                'label': 'Exercises',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'workout_logs': {
                'label': 'Workout Logs',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'payments': {
        'label': 'Payments & Billing',
        'resources': {
            'transactions': {
                'label': 'Transactions',
                'actions': ['view']
            },
            'subscriptions': {
                'label': 'Subscriptions',
                'actions': ['view', 'edit']
            },
            'refunds': {
                'label': 'Refunds',
                'actions': ['view', 'create']
            },
            'gateways': {
                'label': 'Payment Gateways',
                'actions': ['view', 'edit']
            }
        }
    },
    'inventory': {
        'label': 'Inventory & Shop',
        'resources': {
            'products': {
                'label': 'Products',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'stock': {
                'label': 'Stock Levels',
                'actions': ['view', 'adjust_stock']
            },
            'categories': {
                'label': 'Categories',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'rewards': {
        'label': 'Rewards & Loyalty',
        'resources': {
            'rules': {
                'label': 'Reward Rules',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'store_items': {
                'label': 'Store Items',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'points': {
                'label': 'Member Points',
                'actions': ['view', 'grant_points']
            },
            'redemptions': {
                'label': 'Redemptions',
                'actions': ['view', 'edit']
            }
        }
    },
    'notifications': {
        'label': 'Notifications & Marketing',
        'resources': {
            'campaigns': {
                'label': 'Campaigns',
                'actions': ['view', 'create', 'edit', 'delete', 'send_campaign']
            },
            'templates': {
                'label': 'Templates',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'automations': {
                'label': 'Automations',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'client_assessments': {
        'label': 'Client Assessments',
        'resources': {
            'assessments': {
                'label': 'Assessments',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'nutrition': {
        'label': 'Nutrition & Meals',
        'resources': {
            'food_items': {
                'label': 'Food Items',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'meals': {
                'label': 'Meals & Plans',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'socialnetwork': {
        'label': 'Community & Social',
        'resources': {
            'posts': {
                'label': 'Posts & Feeds',
                'actions': ['view', 'create', 'edit', 'delete', 'moderate']
            },
            'groups': {
                'label': 'Groups',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'support': {
        'label': 'Support & Helpdesk',
        'resources': {
            'tickets': {
                'label': 'Tickets',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    },
    'staff_users': {
        'label': 'Staff & Users',
        'resources': {
            'staff': {
                'label': 'Staff Members',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'clients': {
                'label': 'Clients Directory',
                'actions': ['view', 'edit']
            }
        }
    }
}


def get_permission_catalog():
    return PERMISSION_CATALOG


VALID_ACTIONS_MAP = {
    app: {res: set(res_data['actions']) for res, res_data in app_data['resources'].items()}
    for app, app_data in PERMISSION_CATALOG.items()
}


def get_all_valid_actions():
    return VALID_ACTIONS_MAP



def sanitize_permissions(permissions_dict):
    if not isinstance(permissions_dict, dict):
        return {}
    valid_map = get_all_valid_actions()
    sanitized = {}
    for app, resources in permissions_dict.items():
        if app not in valid_map or not isinstance(resources, dict):
            continue
        sanitized[app] = {}
        for res, actions in resources.items():
            if res not in valid_map[app] or not isinstance(actions, (list, set, tuple)):
                continue
            allowed = valid_map[app][res]
            filtered_actions = [a for a in actions if a in allowed or a == '*']
            if filtered_actions:
                sanitized[app][res] = filtered_actions
        if not sanitized[app]:
            sanitized.pop(app, None)
    return sanitized
