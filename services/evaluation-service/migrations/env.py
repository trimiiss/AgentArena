from agentarena_common.migrations import run_migrations
from evaluation_service.models import Base

run_migrations(Base.metadata)
