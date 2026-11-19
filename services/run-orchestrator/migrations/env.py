from agentarena_common.migrations import run_migrations
from run_orchestrator.models import Base

run_migrations(Base.metadata)
