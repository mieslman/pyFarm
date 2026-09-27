# Import all handler modules to trigger their @register_task decorators
from app.modules.seasonpass.handlers import (
    crops,  # noqa: F401
    defaults,  # noqa: F401
    foodworld,  # noqa: F401
    forestry,  # noqa: F401
    misc,  # noqa: F401
    sheds,  # noqa: F401
)
