PERMISSION_CATALOG = {
    'scheduling': {
        'label': 'Scheduling & Timetable',
        'resources': {
            'locations': {
                'label': 'Locations',
                'actions': ['view', 'create', 'edit', 'delete']
            },
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
            'appointments': {
                'label': 'Appointments',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'waitlist': {
                'label': 'Waitlist',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'substitutes': {
                'label': 'Substitutes',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'staff_availability': {
                'label': 'Staff Availability',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'facility_access': {
                'label': 'Facility Access & Check-ins',
                'actions': ['view', 'create', 'check_out']
            },
            'staff_assignments': {
                'label': 'Staff Client Assignments',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'reports': {
                'label': 'Reports & Utilization',
                'actions': ['view']
            },
            'booking_settings': {
                'label': 'Booking Settings',
                'actions': ['view', 'edit']
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
            },
            'playlists': {
                'label': 'Music Playlists',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'assignments': {
                'label': 'Workout Assignments',
                'actions': ['view', 'create', 'delete']
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
            },
            'payouts': {
                'label': 'Payouts & Transfers',
                'actions': ['view']
            },
            'billing': {
                'label': 'SaaS Subscription & Billing',
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
            'programs': {
                'label': 'Reward Programs',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'rules': {
                'label': 'Reward Rules',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'badges': {
                'label': 'Badges & Achievements',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'tiers': {
                'label': 'Reward Tiers',
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
            },
            'analytics': {
                'label': 'Reward Analytics',
                'actions': ['view']
            },
            'referrals': {
                'label': 'Referral Programs',
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
            },
            'groups': {
                'label': 'Notification Groups',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'settings': {
                'label': 'Notification Settings',
                'actions': ['view', 'edit']
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
            },
            'recipes': {
                'label': 'Staff & Client Recipes',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'goals': {
                'label': 'Nutrition & Macro Goals',
                'actions': ['view', 'create', 'edit']
            },
            'food_logs': {
                'label': 'Client Food Logs',
                'actions': ['view']
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
            },
            'moderation': {
                'label': 'Moderation & Reports',
                'actions': ['view', 'moderate']
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
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'staff_requests': {
                'label': 'Staff Registration Requests',
                'actions': ['view', 'approve', 'reject', 'delete']
            }
        }
    },
    'retention': {
        'label': 'Retention & Intelligence',
        'resources': {
            'metrics': {
                'label': 'Client Retention Metrics',
                'actions': ['view', 'recalculate']
            },
            'segments': {
                'label': 'Saved Segments',
                'actions': ['view', 'create', 'edit', 'delete']
            },
            'analytics': {
                'label': 'Retention & Operational Analytics',
                'actions': ['view']
            },
            'triggers': {
                'label': 'Retention Campaign Triggers',
                'actions': ['view', 'create', 'edit', 'delete']
            }
        }
    }
}


def get_permission_catalog():
    return PERMISSION_CATALOG


def get_all_valid_actions():
    return {
        app: {res: set(res_data['actions']) for res, res_data in app_data['resources'].items()}
        for app, app_data in PERMISSION_CATALOG.items()
    }


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
