from fastapi import APIRouter

# The metrics endpoint is not in the contract yet. Routes added here are served automatically.
router = APIRouter(tags=["metrics"])
