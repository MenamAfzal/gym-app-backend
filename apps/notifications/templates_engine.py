"""
Template Renderer

Safe, sandboxed string substitution for notification templates.
Uses Python's string.Formatter with a SafeDict fallback — missing variables
resolve to an empty string rather than raising KeyError.

No Jinja2, no eval, no arbitrary code execution.
Only the variables listed in SAFE_VARIABLES are supported.

Usage:
    rendered_title = TemplateRenderer.render("Hi {{client_name}}!", {"client_name": "Ahmed"})
    # Result: "Hi Ahmed!"

    rendered_body = TemplateRenderer.render("Your {{missing_var}} is here", {})
    # Result: "Your  is here"  (missing vars silently become empty string)
"""


class _SafeDict(dict):
    """
    A dict subclass that returns an empty string for missing keys
    instead of raising KeyError during format_map().
    """
    def __missing__(self, key):
        return ''


class TemplateRenderer:
    """
    Renders notification template strings with variable substitution.

    Template format: {{variable_name}}
    Rendered using: str.format_map(SafeDict(context))

    Only variables in SAFE_VARIABLES are documented/supported.
    Unknown variables resolve silently to empty string.
    """

    SAFE_VARIABLES = {
        'client_name',
        'gym_name',
        'trainer_name',
        'class_name',
        'class_time',
        'room_name',
        'package_name',
        'expiry_date',
        'booking_date',
        'appointment_time',
        'days_remaining',
        'amount',
        'staff_name',
        'workout_name',
    }

    @staticmethod
    def render(template_str: str, context: dict) -> str:
        """
        Render a template string with context variables.

        Template syntax uses double braces: {{variable_name}}
        This is converted to Python format syntax {variable_name} before rendering.

        Args:
            template_str: Template string with {{variable}} placeholders
            context: Dict of variable values

        Returns:
            Rendered string. Missing variables become empty strings.
        """
        if not template_str:
            return ''

        # Convert {{variable}} → {variable} for Python format_map
        python_template = template_str.replace('{{', '{').replace('}}', '}')

        try:
            return python_template.format_map(_SafeDict(context))
        except (ValueError, KeyError):
            # Malformed template — return original as fallback
            return template_str

    @staticmethod
    def render_pair(
        title_template: str,
        body_template: str,
        context: dict,
    ) -> tuple[str, str]:
        """
        Render both title and body templates in a single call.

        Returns:
            Tuple of (rendered_title, rendered_body)
        """
        return (
            TemplateRenderer.render(title_template, context),
            TemplateRenderer.render(body_template, context),
        )
