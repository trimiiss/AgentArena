from agent_registry.models import Base
from agentarena_common.migrations import run_migrations

run_migrations(Base.metadata)
