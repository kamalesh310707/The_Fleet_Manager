import pytest

from fleet_manager.app.main import (
    DispatchRequest,
    app,
    dispatch_task,
    lifespan,
    world_state,
)


@pytest.mark.asyncio
async def test_dispatch_assigns_an_idle_robot_and_reserves_its_route():
    async with lifespan(app):
        # Pick a free cell near robot_1 start (1,1)
        response = await dispatch_task(DispatchRequest(target=(3, 3)))

        task = response["task"]
        assert task["assigned_to"] is not None
        assert task["status"] == "assigned"
        assert response["path"][-1] == (3, 3)
        assert world_state.snapshot()["reservations"]
