"""
Selectable visual themes ("chartes graphiques") for the sandbox storefront.

The active theme comes from the OSCAR_THEME setting. Staff users (or anyone
when DEBUG is on) can preview another theme with ``?theme=<name>``; the choice
is kept in the session and ``?theme=default`` resets it.
"""
from django.conf import settings

THEMES = {
    'default': {'label': 'Oscar (par défaut)', 'fonts': None},
    'sahel': {
        'label': 'Sahel – terracotta & sable',
        'fonts': 'family=Poppins:wght@400;600&family=Playfair+Display:wght@600',
    },
    'ocean': {
        'label': 'Océan – bleu moderne',
        'fonts': 'family=Inter:wght@400;600;700',
    },
    'nuit': {
        'label': 'Nuit – sombre & doré',
        'fonts': 'family=Montserrat:wght@400;600&family=Cormorant+Garamond:wght@600',
    },
    'minimal': {
        'label': 'Minimal – noir & blanc',
        'fonts': 'family=DM+Sans:wght@400;500;700&family=DM+Serif+Display',
    },
    'nature': {
        'label': 'Nature – vert frais',
        'fonts': 'family=Nunito:wght@400;600;800',
    },
}

SESSION_KEY = 'oscar_theme_preview'


def _can_preview(request):
    user = getattr(request, 'user', None)
    return settings.DEBUG or (user is not None and user.is_staff)


def theme(request):
    name = getattr(settings, 'OSCAR_THEME', 'default')
    if _can_preview(request):
        requested = request.GET.get('theme')
        if requested in THEMES:
            if requested == name:
                request.session.pop(SESSION_KEY, None)
            else:
                request.session[SESSION_KEY] = requested
        name = request.session.get(SESSION_KEY, name)
    if name not in THEMES:
        name = 'default'
    return {
        'theme_name': name,
        'theme': THEMES[name],
        'available_themes': THEMES if _can_preview(request) else {},
    }
