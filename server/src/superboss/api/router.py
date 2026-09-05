from fastapi import APIRouter

from superboss.modules.agent.router import router as agent_router
from superboss.modules.audit.router import router as audit_router
from superboss.modules.auth.router import router as auth_router
from superboss.modules.files.router import folders_router
from superboss.modules.files.router import router as files_router
from superboss.modules.finance.router import router as finance_router
from superboss.modules.health.router import router as health_router
from superboss.modules.knowledge.router import router as knowledge_router
from superboss.modules.projects.router import router as projects_router
from superboss.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(projects_router)
api_router.include_router(files_router)
api_router.include_router(folders_router)
api_router.include_router(finance_router)
api_router.include_router(knowledge_router)
api_router.include_router(agent_router)
api_router.include_router(audit_router)
api_router.include_router(users_router)
