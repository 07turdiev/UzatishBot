from bot.handlers import commands, messages, callbacks, source


def register_all():
    """Import all handler modules to register decorators."""
    # Handlers are registered via @app.on_message / @app.on_callback_query
    # decorators at import time. This function just ensures they're imported.
    _ = commands, messages, callbacks, source
