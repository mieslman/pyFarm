# Import all handler modules to trigger their @register_task decorators
from app.modules.seasonpass.handlers import crops  # noqa: F401
from app.modules.seasonpass.handlers import defaults  # noqa: F401
from app.modules.seasonpass.handlers import foodworld  # noqa: F401
from app.modules.seasonpass.handlers import forestry  # noqa: F401
from app.modules.seasonpass.handlers import misc  # noqa: F401
from app.modules.seasonpass.handlers import sheds  # noqa: F401
