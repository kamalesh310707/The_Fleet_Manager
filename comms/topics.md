# MQTT Topic Schema

Frozen contract — treat changes here as breaking across backend and robot
processes. Source of truth for payload shapes is `shared/schemas.py`.

| Topic | Direction | Payload |
|---|---|---|
| `fleet/robot/{id}/command` | Backend → Robot | `CommandPayload` — `{target: [x,y], task_id, path?: [[x,y],...]}` |
| `fleet/robot/{id}/status` | Robot → Backend | `StatusPayload` — `{position, status, task_id, timestamp}` |
| `fleet/dispatch/broadcast` | Backend → All | `DispatchBroadcast` — new task announcements |
| `fleet/robot/{id}/error` | Robot → Backend | `ErrorPayload` — `{error_type, last_position, timestamp}` |

Backend-side wildcard subscriptions: `fleet/robot/+/status`, `fleet/robot/+/error`.

When `path` is present in a command the robot **must** follow it so motion
matches the space-time reservation table held by the Fleet Manager.
