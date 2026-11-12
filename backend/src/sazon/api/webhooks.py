"""Google Calendar push notifications (SAZ-40): `POST /api/webhooks/google/calendar`.

Google posts an empty body with the channel in headers. A notification whose channel id,
token and resource id do not match a stored channel gets 403: Google retries only 5xx, so a
stale or forged one is not sent again, and the answer is the same whichever part was wrong.
A valid one gets 200 at once; the sync runs in this process afterwards (`CalendarPush`). The
`sync` notification that opens a channel needs no work. Over the rate limit the answer is 503,
not 429, so that Google sends the notification again later. Headers are never logged.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response

from sazon import metrics
from sazon.api.deps import RepoDep, caller_ip, spend
from sazon.scheduling.push import MAX_HEADER, CalendarPush, verify

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

CHANGED = frozenset({"exists", "not_exists"})
KNOWN_STATES = frozenset({"sync", *CHANGED})


def get_push(request: Request) -> CalendarPush | None:
    return getattr(request.app.state, "calendar_push", None)


GoogHeader = Annotated[str, Header(max_length=MAX_HEADER)]


@router.post(
    "/google/calendar",
    status_code=200,
    response_class=Response,
    responses={
        403: {"description": "Unknown channel, wrong token or resource"},
        503: {"description": "Over the rate limit: Google retries"},
    },
)
async def google_calendar(
    request: Request,
    repo: RepoDep,
    x_goog_channel_id: GoogHeader = "",
    x_goog_channel_token: GoogHeader = "",
    x_goog_resource_id: GoogHeader = "",
    x_goog_resource_state: Annotated[str, Header(max_length=32)] = "",
) -> Response:
    """A change on a recruiter's calendar. Answers before the change is processed."""
    spend(request, "calendar_webhook_limit", caller_ip(request), status=503)
    channel = await asyncio.to_thread(
        verify, repo, x_goog_channel_id, x_goog_channel_token, x_goog_resource_id
    )
    if channel is None:
        metrics.CALENDAR_NOTIFICATIONS.labels("rejected").inc()
        raise HTTPException(403, "Unknown channel.")
    state = x_goog_resource_state if x_goog_resource_state in KNOWN_STATES else "other"
    metrics.CALENDAR_NOTIFICATIONS.labels(state).inc()
    push = get_push(request)
    if state in CHANGED and push is not None:
        push.notify(channel.recruiter_id)
    return Response(status_code=200)
